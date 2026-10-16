import json

import pytest
from pydantic import ValidationError
from support_agent.contracts import SupportAnswer, parse_answer


DOCS = [{"id": "webhooks-v1", "content": "使用 HMAC-SHA256。"}]
VALID = {"answer": "使用 HMAC-SHA256。", "evidence": ["webhooks-v1"],
         "needs_clarification": False, "clarification_question": None}


def response(answer=VALID, **choice_fields):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}, **choice_fields}]}


def test_correct_shape_and_reference_pass():
    assert parse_answer(response(), DOCS).evidence == ["webhooks-v1"]


def test_valid_json_but_unknown_reference_fails():
    wrong = dict(VALID, evidence=["nonexistent-doc"])
    assert SupportAnswer.model_validate(wrong)
    with pytest.raises(ValueError, match="unknown_evidence"):
        parse_answer(response(wrong), DOCS)


def test_existing_reference_does_not_prove_claim():
    wrong = dict(VALID, answer="使用 MD5。")
    assert parse_answer(response(wrong), DOCS).answer == "使用 MD5。"


@pytest.mark.parametrize("change", [dict(needs_clarification="false"), dict(extra="unexpected"), dict(evidence="webhooks-v1")])
def test_schema_rejects_wrong_types_and_extra_fields(change):
    with pytest.raises(ValidationError):
        parse_answer(response(dict(VALID, **change)), DOCS)


@pytest.mark.parametrize("value", [
    response(finish_reason="length"), response(finish_reason="content_filter"),
    response(message={"content": None, "refusal": "refused"}),
    response(message={"content": None}), {"choices": []},
])
def test_non_answer_branches_fail(value):
    with pytest.raises(ValueError):
        parse_answer(value, DOCS)


def test_clarification_requires_a_question():
    with pytest.raises(ValueError, match="inconsistent_clarification"):
        parse_answer(response(dict(VALID, needs_clarification=True)), DOCS)
    answer = dict(VALID, needs_clarification=True, clarification_question="哪一次投递？")
    assert parse_answer(response(answer), DOCS).needs_clarification


@pytest.mark.parametrize("needs_clarification", [True, False])
@pytest.mark.parametrize("question", ["", "   "])
def test_blank_question_is_never_a_valid_clarification(needs_clarification, question):
    answer = dict(VALID, needs_clarification=needs_clarification, clarification_question=question)
    with pytest.raises(ValueError, match="inconsistent_clarification"):
        parse_answer(response(answer), DOCS)
