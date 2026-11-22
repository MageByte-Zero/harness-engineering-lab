"""Local OpenTelemetry spans and a safe projection of existing Run facts."""
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import json
import logging
from pathlib import Path
from threading import Lock
from uuid import UUID

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Link, Span, SpanContext, StatusCode, TraceFlags, Tracer, get_current_span, use_span


LOGGER = logging.getLogger(__name__)
_CURRENT: ContextVar[Tracer | None] = ContextVar('repo_agent_tracer', default=None)
_TOOL_RESULTS = frozenset({
    'ok', 'submitted', 'not_found', 'not_allowed', 'target_denied', 'old_text_not_found',
    'old_text_ambiguous', 'no_change', 'file_size_limit', 'error', 'timeout',
})
_EVENTS = frozenset({
    'queued', 'running', 'model_started', 'tool_requested', 'tool_started', 'tool_finished',
    'verification_started', 'verification_finished', 'repair_started', 'cancel_requested',
    'completed', 'failed', 'cancelled', 'interrupted', 'recovery_recorded',
})


def _reference(context: SpanContext) -> dict:
    return {'trace_id': f'{context.trace_id:032x}', 'span_id': f'{context.span_id:016x}'}


def reference(span: Span) -> dict:
    return _reference(span.get_span_context())


def _call_ref(call_id: str) -> str:
    return sha256(call_id.encode()).hexdigest()[:16]


def _tool_status(value) -> str:
    return value if isinstance(value, str) and value in _TOOL_RESULTS else 'returned'


class LocalSpanExporter(SpanExporter):
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = Lock()

    def path(self, run_id: str) -> Path:
        return self.directory / f'{UUID(run_id)}.jsonl'

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            with self.lock:
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                for span in spans:
                    attrs = dict(span.attributes or {})
                    key, context = attrs.get('harness.run_id'), span.context
                    if not isinstance(key, str) or context is None:
                        continue
                    path = self.path(key)
                    entry = {'name': span.name, **_reference(context),
                             'parent_span_id': _reference(span.parent)['span_id'] if span.parent else None,
                             'start_ns': span.start_time, 'end_ns': span.end_time,
                             'status': span.status.status_code.name,
                             'attributes': attrs,
                             'links': [_reference(link.context) for link in span.links],
                             'events': [{'name': event.name, 'at_ns': event.timestamp,
                                         'attributes': dict(event.attributes or {})} for event in span.events]}
                    # Synchronous export preserves completed spans on process kill, with a local size bound.
                    with path.open('a+b') as stream:
                        stream.seek(0, 2)
                        size = stream.tell()
                        if size >= 1024 * 1024:
                            continue
                        if size:
                            stream.seek(-1, 2)
                            if stream.read(1) != b'\n':
                                stream.write(b'\n')
                        stream.write((json.dumps(entry, ensure_ascii=False) + '\n').encode())
            return SpanExportResult.SUCCESS
        except (OSError, ValueError):
            LOGGER.warning('Local Trace export failed')
            return SpanExportResult.FAILURE

    def read(self, run_id: str) -> list[dict]:
        try:
            with self.lock:
                data = self.path(run_id).read_bytes()
        except FileNotFoundError:
            return []
        # A process can be killed during the last append. A newline terminates a complete record.
        records = []
        for line in data.split(b'\n')[:-1]:
            try:
                records.append(json.loads(line))
            except (ValueError, UnicodeDecodeError):
                continue
        return records


class RunTelemetry:
    def __init__(self, directory: Path):
        self.exporter = LocalSpanExporter(directory)
        self.provider = TracerProvider(sampler=ALWAYS_ON, shutdown_on_exit=False)
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer('repo_agent')

    def start(self, run_id: str, kind: str) -> Span:
        return self.tracer.start_span('agent.run', context=Context(),
                                      attributes={'harness.run_id': run_id, 'harness.operation': kind})

    @contextmanager
    def activate(self, span: Span) -> Iterator[None]:
        token = _CURRENT.set(self.tracer)
        try:
            with use_span(span, end_on_exit=False, record_exception=False, set_status_on_exception=False):
                yield
        finally:
            _CURRENT.reset(token)

    @contextmanager
    def recovery(self, run_id: str, original: dict | None) -> Iterator[Span]:
        links = []
        if original:
            links.append(Link(SpanContext(trace_id=int(original['trace_id'], 16),
                                          span_id=int(original['span_id'], 16), is_remote=False,
                                          trace_flags=TraceFlags(TraceFlags.SAMPLED))))
        with self.tracer.start_as_current_span('agent.recovery', context=Context(), links=links,
                                               attributes={'harness.run_id': run_id},
                                               record_exception=False, set_status_on_exception=False) as span:
            try:
                yield span
            except Exception:
                span.set_status(StatusCode.ERROR)
                raise


def child(name: str, **attributes) -> Span | None:
    tracer = _CURRENT.get()
    if tracer is None:
        return None
    parent = get_current_span()
    run_id = (getattr(parent, 'attributes', None) or {}).get('harness.run_id')
    if not isinstance(run_id, str):
        return None
    return tracer.start_span(name, attributes={'harness.run_id': run_id, **attributes})


class AgentSpans:
    """Use the SDK's callbacks without capturing prompts, arguments or responses."""
    def __init__(self):
        self.round = 0
        self.model: Span | None = None
        self.tools: dict[str, Span] = {}

    def model_start(self):
        self.round += 1
        self.model = child('agent.model', **{'harness.model.round': self.round})

    def model_end(self, requests):
        if self.model is not None:
            for name, call_id in requests:
                self.model.add_event('tool.requested', {'harness.tool.name': name, 'harness.tool.call_id': _call_ref(call_id)})
            self.model.end()
            self.model = None

    def tool_start(self, name: str, call_id: str):
        span = child('agent.tool', **{'harness.tool.name': name, 'harness.tool.call_id': _call_ref(call_id)})
        if span is not None:
            self.tools[call_id] = span

    def tool_end(self, call_id: str, status):
        span = self.tools.pop(call_id, None)
        if span is not None:
            span.set_attribute('harness.tool.result', _tool_status(status))
            span.end()

    def finish(self):
        pending = list(self.tools.values())
        if self.model is not None:
            pending.append(self.model)
        for span in pending:
            span.set_attribute('harness.outcome', 'no_callback')
            span.end()
        self.tools.clear()
        self.model = None


def trace_view(run, spans: list[dict]) -> dict:
    timeline = []
    observed_names = {item['data'].get('tool') for item in run.events if item['kind'] == 'tool_started'}
    for item in run.events:
        if item['kind'] not in _EVENTS:
            continue
        data = item['data']
        event = {key: data[key] for key in ('attempt', 'call_index') if key in data}
        if 'tool' in data:
            event['tool'] = data['tool'] if data['tool'] in observed_names else 'unconfirmed_tool'
        if isinstance(data.get('call_id'), str):
            event['call_id'] = _call_ref(data['call_id'])
        if item['kind'] == 'tool_finished':
            event['status'] = _tool_status(data.get('status'))
        timeline.append({'seq': item['seq'], 'at': item['at'], 'kind': item['kind'], 'data': event})
    operation = run.operation
    recovery = run.recovery or {}
    result = run.result or {}
    return {'run_id': run.run_id, 'original_trace': run.trace_context,
            'spans': spans, 'timeline': timeline,
            'business': {'state': run.state, 'result_kind': result.get('kind'),
                         'receipt_saved': None if operation is None else 'receipt' in operation,
                         'tracked_containers': None if operation is None else len(operation.get('containers', [])),
                         'recovery_status': recovery.get('status'),
                         'changed_file_count': len(recovery.get('changed_paths', [])),
                         'removed_containers': recovery.get('removed_containers')}}
