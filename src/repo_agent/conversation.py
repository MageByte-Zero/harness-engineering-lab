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


class ConversationBusy(RuntimeError):
    pass


@dataclass
class Conversation:
    conversation_id: str = field(default_factory=lambda: str(uuid4()))
    turns: list[dict] = field(default_factory=list)
    busy: bool = False

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
    def __init__(self, sources, *, business_url, business_token):
        self.sources, self.business_url, self.business_token = sources, business_url, business_token
        self.source_hash = snapshot_hash(load_source_bundle(sources))
        self.conversations: dict[str, Conversation] = {}

    def create(self):
        if len(self.conversations) >= 32:
            raise ValueError('Conversation capacity reached; restart for a fresh local session')
        conversation = Conversation()
        self.conversations[conversation.conversation_id] = conversation
        return conversation

    def acquire(self, identity):
        conversation = self.conversations[identity]
        if conversation.busy:
            raise ConversationBusy()
        conversation.busy = True
        return conversation

    async def __call__(self, question, output, *, conversation, run_id, emit):
        from repo_agent.application import investigate
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
                conversation.turns.append({'run_id':run_id, 'items':new_items})
            return record
        finally:
            session.close()
