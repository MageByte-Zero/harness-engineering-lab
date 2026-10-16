"""Lesson 03: validate shape first, then references supplied by the application."""

from pydantic import BaseModel, ConfigDict


class SupportAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str
    evidence: list[str]
    needs_clarification: bool
    clarification_question: str | None


def parse_answer(response: dict, documents: list[dict[str, str]]) -> SupportAnswer:
    choices = response.get("choices", [])
    if not choices:
        raise ValueError("missing_choice")
    choice = choices[0]
    message = choice["message"]
    if message.get("refusal"):
        raise ValueError("model_refusal")
    if choice.get("finish_reason") != "stop":
        raise ValueError("incomplete_response")
    if not message.get("content"):
        raise ValueError("empty_content")
    answer = SupportAnswer.model_validate_json(message["content"])
    if not answer.answer.strip():
        raise ValueError("empty_answer")
    unknown = set(answer.evidence) - {doc["id"] for doc in documents}
    if unknown:
        raise ValueError("unknown_evidence")
    has_question = bool(answer.clarification_question and answer.clarification_question.strip())
    if (answer.needs_clarification and not has_question) or (
        not answer.needs_clarification and answer.clarification_question is not None
    ):
        raise ValueError("inconsistent_clarification")
    return answer
