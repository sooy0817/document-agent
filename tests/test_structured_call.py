"""제한된 구조화 모델 호출: 외부 허용·예산·감사 기록·스키마 검증. 실제 외부 호출은 없다."""
import json
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from doc_understanding.runtime.structured_call import CallPolicy, Ledger, structured_call


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice: Literal["a", "b"]


POLICY = CallPolicy(model="test-model", input_usd_per_million=1.0, output_usd_per_million=2.0)


def reply(text, status="completed"):
    return lambda payload, key, timeout: dict(
        status=status, usage={"input_tokens": 10, "output_tokens": 5},
        output=[{"type": "message", "content": [{"type": "output_text", "text": text}]}])


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")


def test_valid_response_is_parsed_and_audited(tmp_path):
    ledger = Ledger()
    parsed, record = structured_call("pick", "system", {"q": "x"}, Choice, POLICY, tmp_path / "c1",
                                     allow_external=True, ledger=ledger,
                                     transport=reply('{"choice":"a"}'))
    assert parsed.choice == "a" and record["status"] == "completed"
    assert record["estimated_cost_usd"] == pytest.approx((10 * 1 + 5 * 2) / 1e6)
    request = json.loads((tmp_path / "c1/request.json").read_text(encoding="utf8"))
    assert request["text"]["format"]["strict"] is True and request["store"] is False
    assert "test-key-never-sent" not in (tmp_path / "c1/request.json").read_text(encoding="utf8")
    assert len(ledger.calls) == 1


def test_external_permission_key_and_pricing_are_checked_before_call(tmp_path, monkeypatch):
    never = lambda *a: pytest.fail("no call expected")  # noqa: E731
    with pytest.raises(ValueError, match="EXTERNAL_CALL_NOT_ENABLED"):
        structured_call("pick", "s", {}, Choice, POLICY, tmp_path / "a", transport=never)
    bad = POLICY.model_copy(update={"input_usd_per_million": float("nan")})
    with pytest.raises(ValueError, match="EXPLICIT_MODEL_PRICING_REQUIRED"):
        structured_call("pick", "s", {}, Choice, bad, tmp_path / "b", allow_external=True, transport=never)
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValueError, match="API_KEY_UNAVAILABLE"):
        structured_call("pick", "s", {}, Choice, POLICY, tmp_path / "c", allow_external=True, transport=never)


def test_request_and_run_budgets_block_before_transport(tmp_path):
    never = lambda *a: pytest.fail("no call expected")  # noqa: E731
    tiny = POLICY.model_copy(update={"max_cost_usd": 1e-9})
    with pytest.raises(ValueError, match="MODEL_REQUEST_BUDGET_EXCEEDED"):
        structured_call("pick", "s", {}, Choice, tiny, tmp_path / "a", allow_external=True, transport=never)
    ledger = Ledger(max_calls=0)
    with pytest.raises(ValueError, match="MODEL_RUN_BUDGET_EXCEEDED"):
        structured_call("pick", "s", {}, Choice, POLICY, tmp_path / "b", allow_external=True,
                        ledger=ledger, transport=never)


@pytest.mark.parametrize("transport", [reply('{"choice":"z"}'), reply("not json"),
                                       reply('{"choice":"a"}', status="incomplete")])
def test_invalid_or_incomplete_responses_fail_with_local_audit(tmp_path, transport):
    with pytest.raises(RuntimeError, match="MODEL_CALL_FAILED_SEE_LOCAL_AUDIT"):
        structured_call("pick", "s", {}, Choice, POLICY, tmp_path / "f", allow_external=True,
                        transport=transport)
    failure = json.loads((tmp_path / "f/failure.json").read_text(encoding="utf8"))
    assert failure["status"] == "failed" and "error_type" in failure


def test_temperature_is_fixed_at_zero_unless_the_model_family_rejects_it(tmp_path):
    seen = []

    def capture(payload, key, timeout):
        seen.append(payload)
        return reply('{"choice":"a"}')(payload, key, timeout)

    structured_call("pick", "s", {}, Choice, POLICY, tmp_path / "a", allow_external=True, transport=capture)
    assert seen[-1]["temperature"] == 0.0
    for policy in (POLICY.model_copy(update={"temperature": None}), POLICY.model_copy(update={"model": "o4-mini"}),
                   POLICY.model_copy(update={"model": "gpt-5-mini"})):
        structured_call("pick", "s", {}, Choice, policy, tmp_path / f"n{len(seen)}", allow_external=True,
                        transport=capture)
        assert "temperature" not in seen[-1]
