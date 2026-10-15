import importlib.util
import json
from pathlib import Path
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from pydantic import ValidationError
from team_harness.investigation import NoRedirect, OpenAIResponses, execute_tool, investigate, validate_answer

ROOT = Path(__file__).resolve().parents[1] / "examples" / "lesson05"
spec = importlib.util.spec_from_file_location("lesson05_demo", ROOT / "demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)
CASES = json.loads((ROOT / "cases.json").read_text())


class InvestigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_evaluation_returns_nonzero_for_failed_case(self):
        with patch.dict("sys.modules", {"demo": demo}):
            eval_spec = importlib.util.spec_from_file_location("lesson05_eval", ROOT / "eval.py")
            evaluation = importlib.util.module_from_spec(eval_spec)
            eval_spec.loader.exec_module(evaluation)
        failed = {"run_id":"failed", "answer":None, "turns":[], "stop_reason":"step_limit"}
        with tempfile.TemporaryDirectory() as folder, patch.object(evaluation, "investigate", AsyncMock(return_value=failed)), patch("builtins.print"):
            code = await evaluation.main(SimpleNamespace(mode="scripted", output_dir=Path(folder)))
        self.assertEqual(code, 1)

    async def test_real_tools_and_call_result_correlation(self):
        record = await investigate(CASES[0], demo.ScriptedModel(CASES[0]), ROOT)
        self.assertEqual(record["stop_reason"], "answered")
        self.assertIn("expected=false, actual=true", record["evidence"]["e2"])
        self.assertEqual(json.loads(record["evidence"]["e2"])["status"], "failed")
        tool = record["turns"][0]["tools"][0]
        feedback = record["turns"][1]["request"]["input"][-1]
        self.assertEqual(tool["call_id"], feedback["call_id"])
        self.assertEqual(json.loads(feedback["output"]), tool["result"])
        self.assertIsNone(record["turns"][0]["usage"])
        self.assertEqual(record["turns"][0]["model"], "scripted-protocol-test-double")

    async def test_normal_and_missing_are_not_misclassified(self):
        for case in CASES[1:]:
            record = await investigate(case, demo.ScriptedModel(case), ROOT)
            self.assertEqual(record["answer"]["status"], case["expected_status"])
            if case["id"] == "normal":
                self.assertIn("4 boundary checks passed", record["evidence"]["e2"])
            else:
                self.assertIn("unavailable", record["evidence"]["e2"])

    async def test_parameters_and_unknown_tools_rejected_before_execution(self):
        with patch("team_harness.investigation.run_command") as run:
            for arguments in ('{"command":"echo hacked"}', '[]', '{oops', '{"path":"/etc/passwd"}'):
                with self.assertRaises(ValidationError):
                    await execute_tool("run_tests", arguments, CASES[0], ROOT)
            with self.assertRaises(ValueError):
                await execute_tool("shell", "{}", CASES[0], ROOT)
            run.assert_not_called()

    async def test_limit_stops_and_preserves_trace(self):
        record = await investigate(CASES[0], demo.ScriptedModel(CASES[0]), ROOT, max_turns=1)
        self.assertEqual(record["stop_reason"], "step_limit")
        self.assertEqual(len(record["turns"]), 1)
        self.assertIn("e1", record["evidence"])
        self.assertIsNone(record["answer"])

    async def test_bad_final_and_bad_calls_leave_failure_records(self):
        for output in (
            [{"type":"message", "content":[{"type":"output_text", "text":'{"status":"known","summary":"bad","citations":[]}'}]}],
            [{"type":"function_call", "call_id":"x", "name":"read_logs", "arguments":'{"path":"/etc/passwd"}'}],
            [{"type":"function_call", "call_id":"x", "name":"shell", "arguments":"{}"}],
        ):
            class BadModel:
                model = "invalid-test-double"
                def __call__(self, payload):
                    return {"status":"completed", "output":output}
            record = await investigate(CASES[0], BadModel(), ROOT)
            self.assertEqual(record["stop_reason"], "invalid_response_or_tool")
            self.assertIsNotNone(record["turns"][0]["response"])
            self.assertFalse(record["evidence"])

    async def test_reasoning_items_are_preserved(self):
        class ReasoningModel(demo.ScriptedModel):
            def __call__(self, payload):
                response = super().__call__(payload)
                response["output"].insert(0, {"type":"reasoning", "id":"r1", "summary":[]})
                return response
        record = await investigate(CASES[0], ReasoningModel(CASES[0]), ROOT)
        self.assertIn({"type":"reasoning", "id":"r1", "summary":[]}, record["turns"][1]["request"]["input"])

    async def test_model_error_is_sanitized(self):
        class BrokenModel:
            model = "broken-test-double"
            def __call__(self, payload):
                raise URLError("credential-not-for-logs")
        record = await investigate(CASES[0], BrokenModel(), ROOT)
        self.assertEqual(record["stop_reason"], "model_or_tool_error")
        self.assertNotIn("credential-not-for-logs", json.dumps(record))

    def test_http_adapter_uses_json_bearer_and_bounded_timeout(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY":"test-only-not-a-real-key", "OPENAI_MODEL":"test-model"}), \
             patch("team_harness.investigation.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"status":"completed","output":[]}'
            model = OpenAIResponses()
            self.assertEqual(model({"model":"test-model"})["status"], "completed")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
            self.assertEqual(json.loads(request.data), {"model":"test-model"})
            self.assertEqual(request.get_header("Authorization"), "Bearer test-only-not-a-real-key")
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 60)

    def test_redirects_are_refused(self):
        from urllib.request import Request
        with self.assertRaises(HTTPError):
            NoRedirect().redirect_request(Request("https://api.openai.com/v1/responses"), None,
                                          302, "redirect", {}, "https://other.example/")

    def test_invalid_citations_and_shapes_are_rejected(self):
        for answer in (
            {"status":"known", "summary":"wrong", "citations":[]},
            {"status":"known", "summary":"wrong", "citations":[{"evidence_id":"e9", "quote":"real"}]},
            {"status":"known", "summary":"wrong", "citations":[{"evidence_id":"e1", "quote":"invented"}]},
            {"status":"unknown", "summary":"unknown", "citations":[], "extra":True},
        ):
            with self.assertRaises(ValueError):
                validate_answer(json.dumps(answer), {"e1":"real evidence"})
        answer = validate_answer('{"status":"unknown","summary":"no input","citations":[]}', {})
        self.assertEqual(answer.status, "unknown")
