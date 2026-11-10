"""Select complete successful turns; stage SDK session writes until a run is accepted."""
from dataclasses import dataclass, field
from copy import deepcopy
import json
from hashlib import sha256
from uuid import uuid4

from agents import SQLiteSession

from repo_agent.tools import load_source_bundle, snapshot_hash

CONVERSATION_INSTRUCTIONS = """
历史仅帮助理解指代；用户更换目标时采用新目标。本轮事实必须重新取材，只引用本轮完整observation_id（含运行前缀）。
历史回答不是当前依据；历史省略或指代不清时指出缺口，请用户补充，不猜测。
"""


class ConversationScopeChanged(RuntimeError):
    pass


class ConversationBusy(RuntimeError):
    pass


@dataclass
class Conversation:
    conversation_id: str = field(default_factory=lambda: str(uuid4()))
    turns: list[dict] = field(default_factory=list)
    busy: bool = False
    scope: dict = field(default_factory=dict)

    def select(self, *, max_turns=3, char_limit=24000):
        selected, size = [], 0
        for turn in reversed(self.turns[-max_turns:]):
            items = deepcopy(turn['items'])
            for index, item in enumerate(items):
                # Call/output IDs stay paired; message/reasoning IDs are unique per item.
                if 'call_id' in item:
                    item['call_id'] = 'c-' + sha256(f"{turn['run_id']}/{item['call_id']}".encode()).hexdigest()[:24]
                if 'id' in item:
                    item['id'] = "m-" + sha256(f"{turn['run_id']}/{index}/{item['id']}".encode()).hexdigest()[:24]
            cost = len(json.dumps(items, ensure_ascii=False))
            if size + cost > char_limit:
                break
            selected.insert(0, {'run_id':turn['run_id'], 'items':items})
            size += cost
        history = [item for turn in selected for item in turn['items']]
        return {'id': self.conversation_id, 'history': history,
            'selected_run_ids': [t['run_id'] for t in selected],
            'omitted_turns': len(self.turns) - len(selected), 'selected_chars': len(json.dumps(history, ensure_ascii=False)),
            'source_policy': 'current_run_only'}


class ConversationWorker:
    """Bind resources once; a temporary SDK session cannot pollute committed history."""
    def __init__(self, sources, *, business_url, business_token, store=None):
        self.sources, self.business_url, self.business_token = sources, business_url, business_token
        self.source_hash = snapshot_hash(load_source_bundle(sources))
        self.store = store
        self.scope = {'source_hash': self.source_hash,
            'sources': {k: str(v.resolve()) for k, v in sources.items()}, 'business_url': business_url}
        self.pending_turns: dict[str, dict] = {}
        self.conversations: dict[str, Conversation] = {}
        if store is not None:
            for saved in store.load_conversations():
                self.conversations[saved['id']] = Conversation(
                    conversation_id=saved['id'], turns=saved['turns'], scope=saved['scope'])

    def create(self):
        if len(self.conversations) >= 32:
            raise ValueError('Conversation capacity reached (32); use another data directory for a new local workspace')
        conversation = Conversation(scope=self.scope)
        if self.store is not None:
            self.store.add_conversation(conversation.conversation_id, conversation.scope)
        self.conversations[conversation.conversation_id] = conversation
        return conversation

    def acquire(self, identity):
        conversation = self.conversations[identity]
        if conversation.scope != self.scope:
            raise ConversationScopeChanged('Bound resources changed; create a new conversation')
        if conversation.busy:
            raise ConversationBusy()
        conversation.busy = True
        return conversation

    async def __call__(self, question, output, *, conversation, run_id, emit):
        from repo_agent.application import investigate
        if self.store is not None:
            conversation.turns = await self.store.read_history(conversation.turns)
        selection = conversation.select()
        if snapshot_hash(load_source_bundle(self.sources)) != self.source_hash:
            raise ValueError('Bound sources changed; restart to bind a new conversation scope')
        session = SQLiteSession(conversation.conversation_id)  # :memory:, one staged attempt.
        try:
            await session.add_items(selection['history'])
            record = await investigate(self.sources, question, output, business_url=self.business_url,
                business_token=self.business_token, run_id=run_id, emit=emit,
                session=session, conversation=selection)
            from repo_agent.output import consume_answer
            if consume_answer(record)['kind'] != 'unavailable':
                items = await session.get_items()
                new_items = items[len(selection['history']):]
                turn = {'run_id': run_id, 'items': new_items}
                if self.store is None:
                    conversation.turns.append(turn)
                else:
                    turn.update(await self.store.write_history(run_id, new_items))
                    self.pending_turns[run_id] = turn
            return record
        finally:
            session.close()
