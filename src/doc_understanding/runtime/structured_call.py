# 역할: 엄격한 JSON 스키마 응답을 받는 OpenAI Responses API 호출 한 번을 예산·감사 기록과 함께 실행한다.
"""One bounded structured model call shared by review and question agents.

호출 전에 외부 허용·키·요청 크기·예약 비용을 검사하고, 요청/응답/실패를 새 폴더에 남긴다.
자동 재시도·도구 호출·저장 응답 재생은 없다. 응답 내용의 의미 정확도는 검증하지 않는다.
"""
import hashlib
import json
import math
import os
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel

from doc_understanding.runtime.telemetry import span

from doc_understanding.runtime.openai_http import RESPONSES_URL, http_transport  # noqa: E402,F401


class CallPolicy(BaseModel):
    """호출자가 명시하는 모델·단가·한도. 단가 기본값은 두지 않는다."""

    model: str
    input_usd_per_million: float
    output_usd_per_million: float
    max_cost_usd: float = 0.05
    max_output_tokens: int = 1200
    max_input_bytes: int = 60000
    timeout_seconds: float = 60.0
    # 같은 입력에는 같은 판단이 나오도록 표본 추출 온도를 0으로 둔다(계획·재정렬·구조 검토가 실행마다 달라지지 않게).
    # None이면 보내지 않는다. 온도 설정을 받지 않는 추론 모델 계열에는 자동으로 보내지 않는다(sampling_fixed).
    temperature: float | None = 0.0

    def check(self):
        values = (self.input_usd_per_million, self.output_usd_per_million, self.max_cost_usd)
        if not self.model.strip() or any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("EXPLICIT_MODEL_PRICING_REQUIRED")
        if self.max_cost_usd <= 0:
            raise ValueError("INVALID_MODEL_BUDGET")

    def cost(self, input_tokens, output_tokens):
        return (input_tokens * self.input_usd_per_million
                + output_tokens * self.output_usd_per_million) / 1_000_000


NO_TEMPERATURE_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def sampling_fixed(policy):
    """이 호출에 temperature를 보낼 것인가. 추론 모델 계열(o1·o3·o4·gpt-5)은 이 설정을 거부하므로 보내지 않는다."""
    return policy.temperature is not None and not policy.model.strip().lower().startswith(NO_TEMPERATURE_PREFIXES)


class Ledger:
    """여러 호출의 누적 예약 비용과 호출 수를 제한한다."""

    def __init__(self, max_calls=20, max_total_usd=0.5):
        self.max_calls, self.max_total_usd = max_calls, max_total_usd
        self.calls = []

    @property
    def reserved_usd(self):
        return sum(c["reserved_cost_usd"] for c in self.calls)

    @property
    def estimated_usd(self):
        return sum(c.get("estimated_cost_usd", 0) for c in self.calls)

    def reserve(self, amount):
        if len(self.calls) >= self.max_calls or self.reserved_usd + amount > self.max_total_usd:
            raise ValueError("MODEL_RUN_BUDGET_EXCEEDED")


def structured_call(name, system, user, schema, policy, output, *, allow_external=False,
                    ledger=None, transport=http_transport):
    """schema(Pydantic 모델)에 맞는 응답 하나를 반환한다. 실패는 기록 후 RuntimeError로 올린다."""
    if not allow_external:
        raise ValueError("EXTERNAL_CALL_NOT_ENABLED")
    policy.check()
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("API_KEY_UNAVAILABLE")
    payload = dict(
        model=policy.model,
        store=False,
        max_output_tokens=policy.max_output_tokens,
        input=[dict(role="developer", content=system),
               dict(role="user", content=json.dumps(user, ensure_ascii=False))],
        text=dict(format=dict(type="json_schema", name=name, strict=True,
                              schema=schema.model_json_schema())),
    )
    if sampling_fixed(policy):
        payload["temperature"] = policy.temperature
    raw = json.dumps(payload, ensure_ascii=False).encode("utf8")
    # UTF-8 바이트 수로 입력 토큰 상한을 잡아 비용을 미리 예약한다.
    reserve = policy.cost(len(raw), policy.max_output_tokens)
    if len(raw) > policy.max_input_bytes or reserve > policy.max_cost_usd:
        raise ValueError("MODEL_REQUEST_BUDGET_EXCEEDED")
    if ledger is not None:
        ledger.reserve(reserve)
    record = dict(name=name, model=policy.model, reserved_cost_usd=reserve,
                  request_sha256=hashlib.sha256(raw).hexdigest(), status="started")
    if ledger is not None:
        ledger.calls.append(record)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)

    def write(file, value):
        (output / file).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")

    write("request.json", payload)
    started = perf_counter()
    # 활성 추적(Langfuse)이 있으면 generation 기록을 남긴다. 추적이 없거나 실패해도 호출은 그대로다.
    with span("model." + name, dict(name=name, model=policy.model, question=user.get("question")),
              generation=True) as box:
        try:
            response = transport(payload, key, policy.timeout_seconds)
            write("response.json", response)
            usage = response.get("usage") or {}
            record.update(input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                          estimated_cost_usd=policy.cost(usage.get("input_tokens", 0),
                                                         usage.get("output_tokens", 0)))
            box["output"] = dict(model=response.get("model", policy.model), usage=usage,
                                 estimated_cost_usd=record["estimated_cost_usd"], status="received")
            if response.get("status") != "completed":
                raise ValueError("INCOMPLETE_MODEL_RESPONSE")
            content = [c for item in response.get("output", []) if item.get("type") == "message"
                       for c in item.get("content", [])]
            if any(c.get("type") == "refusal" for c in content):
                raise ValueError("MODEL_REFUSAL")
            text = "".join(c.get("text", "") for c in content if c.get("type") == "output_text")
            parsed = schema.model_validate_json(text)
            record.update(status="completed", elapsed_seconds=perf_counter() - started)
            box["output"]["status"] = "completed"
            write("parsed.json", parsed.model_dump(mode="json"))
            return parsed, dict(record)
        except Exception as error:
            # 예외 원문에는 제공자 응답이나 입력이 포함될 수 있어 종류만 남긴다. 재시도는 없다.
            record.update(status="failed", error_type=type(error).__name__,
                          elapsed_seconds=perf_counter() - started)
            box.setdefault("output", dict(model=policy.model, usage={}, estimated_cost_usd=0))
            box["output"].update(status="failed", error=type(error).__name__)
            write("failure.json", record)
            raise RuntimeError("MODEL_CALL_FAILED_SEE_LOCAL_AUDIT") from None
