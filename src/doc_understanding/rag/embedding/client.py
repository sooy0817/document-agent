# 역할: 외부 호출 허용·입력 크기·누적 예산을 검사하고 임베딩 응답을 검증한다.
import json
import os
import urllib.error
import urllib.request

from doc_understanding.rag.embedding.models import EmbeddingConfig, input_hash, validate_vector
from doc_understanding.runtime.telemetry import span


def http_transport(payload, key, timeout):
    request = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def failure(error):
    # 예외 원문에는 제공자 응답이나 입력이 포함될 수 있어 종류와 HTTP 상태 코드만 기록한다.
    record = dict(status="failed", error_type=type(error).__name__)
    if isinstance(error, urllib.error.HTTPError):
        record["http_status"] = error.code
    return record


def compatible_transport(payload, endpoint, key, timeout):
    # 사용자가 고른 OpenAI 형식 서버. 키는 서버가 요구할 때만 보낸다.
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        endpoint + "/embeddings", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def local_transport(payload, endpoint, timeout):
    # Ollama 기본 API. truncate=false이면 문맥 길이를 넘는 입력을 자르지 않고 오류로 반환한다.
    request = urllib.request.Request(
        endpoint + "/api/embed",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def same_local_model(requested, returned):
    # Ollama는 태그 없는 이름에 :latest를 붙여 돌려줄 수 있다. 그 외 이름 차이는 불일치로 본다.
    def name(value):
        return value[: -len(":latest")] if isinstance(value, str) and value.endswith(":latest") else value

    return name(requested) == name(returned)


class EmbeddingClient:
    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        allow_external=False,
        transport=http_transport,
        local=local_transport,
        compatible=compatible_transport,
    ):
        self.config = config
        self.allow_external = allow_external
        self.transport = transport
        self.local = local
        self.compatible = compatible
        self.audit = []
        self.reserved_cost_usd = 0.0

    @property
    def input_rejected(self):
        """마지막 호출을 서버가 입력 때문에 거부했는가(길이 초과 등). 같은 입력을 다시 보내도 결과가 같다."""
        return bool(self.audit) and self.audit[-1].get("http_status") in (400, 413, 422)

    def embed(self, texts):
        cfg = self.config
        if cfg.external and not self.allow_external:
            raise ValueError("EXTERNAL_CALL_NOT_ENABLED")
        if (
            not isinstance(texts, list)
            or not 1 <= len(texts) <= cfg.batch_size
            or any(
                not isinstance(t, str)
                or not t.strip()
                or len(t.encode("utf-8")) > cfg.max_input_bytes
                for t in texts
            )
        ):
            raise ValueError("EMBEDDING_INPUT_LIMIT")
        # UTF-8 바이트 수에 여유분을 더해 예산을 예약하고 실제 토큰 수는 응답에서 별도로 기록한다.
        bound = sum(len(t.encode("utf-8")) + 16 for t in texts)
        reservation = bound * cfg.input_usd_per_million / 1_000_000
        if (
            len(self.audit) >= cfg.max_calls
            or self.reserved_cost_usd + reservation > cfg.max_cost_usd
        ):
            raise ValueError("EMBEDDING_RUN_BUDGET_EXCEEDED")
        key = os.environ.get(cfg.key_env) if cfg.key_env else None
        if cfg.key_env and not key:
            raise ValueError("API_KEY_UNAVAILABLE")
        record = dict(
            status="started",
            profile_id=cfg.profile_id,
            provider=cfg.provider,
            inputs=len(texts),
            input_hashes=[input_hash(t) for t in texts],
            reserved_cost_usd=reservation,
        )
        self.audit.append(record)
        self.reserved_cost_usd += reservation
        # 활성 추적(Langfuse)이 있으면 호출 시각·사용량·비용을 generation으로 남긴다. 입력 원문은 보내지 않는다.
        with span("model.embedding", dict(provider=cfg.provider, model=cfg.model, inputs=len(texts),
                                          input_hashes=record["input_hashes"]), generation=True) as box:
            try:
                if cfg.provider == "local":
                    return self._embed_local(texts, record)
                return self._embed_openai(texts, record, key)
            finally:
                box["output"] = dict(model=cfg.model, status=record["status"],
                                     usage=dict(input_tokens=record.get("input_tokens") or 0, output_tokens=0),
                                     estimated_cost_usd=record.get("estimated_cost_usd", 0))

    def _embed_openai(self, texts, record, key):
        cfg = self.config
        strict = cfg.provider == "openai"
        payload = dict(model=cfg.model, encoding_format="float", input=texts)
        if strict:
            # 차원 축소 인자는 OpenAI만 보낸다. 다른 서버는 모델이 차원을 정하고, 응답 길이로 설정과 대조한다.
            payload["dimensions"] = cfg.dimensions
        try:
            if strict:
                response = self.transport(payload, key, cfg.timeout_seconds)
            else:
                response = self.compatible(payload, cfg.endpoint, key, cfg.timeout_seconds)
            usage = response.get("usage") or {}
            tokens = usage.get("prompt_tokens")
            if type(tokens) is not int or tokens < 0:
                if strict:
                    raise ValueError("INVALID_EMBEDDING_USAGE")
                tokens = None  # 토큰 수를 주지 않는 서버가 있다. 비용은 예약한 상한으로 적는다.
            record.update(
                input_tokens=tokens,
                estimated_cost_usd=(tokens * cfg.input_usd_per_million / 1e6 if tokens is not None
                                    else record["reserved_cost_usd"]))
            returned = response.get("model")
            if returned != cfg.model if strict else returned is not None and not same_local_model(cfg.model, returned):
                raise ValueError("EMBEDDING_MODEL_MISMATCH")
            rows = response.get("data")
            if (
                not isinstance(rows, list)
                or len(rows) != len(texts)
                or any(
                    not isinstance(row, dict) or type(row.get("index")) is not int for row in rows
                )
                or sorted(row["index"] for row in rows) != list(range(len(texts)))
            ):
                raise ValueError("INVALID_EMBEDDING_RESPONSE_INDICES")
            vectors = [
                validate_vector(row.get("embedding"), cfg.dimensions)
                for row in sorted(rows, key=lambda row: row["index"])
            ]
            record["status"] = "completed"
            return vectors
        except Exception as error:
            # 자동 재시도는 없다.
            record.update(failure(error))
            raise RuntimeError("EMBEDDING_CALL_FAILED_SEE_AUDIT") from None

    def _embed_local(self, texts, record):
        cfg = self.config
        # 차원은 모델이 정한다. 설정 차원은 응답 벡터 길이 검사에 사용한다.
        payload = dict(model=cfg.model, input=texts, truncate=False)
        try:
            response = self.local(payload, cfg.endpoint, cfg.timeout_seconds)
            tokens = response.get("prompt_eval_count")
            # 로컬 실행은 과금이 없다. 토큰 수를 받지 못해도 비용은 0으로 확정한다.
            record.update(
                input_tokens=tokens if type(tokens) is int and tokens >= 0 else None,
                estimated_cost_usd=0.0,
            )
            if not same_local_model(cfg.model, response.get("model")):
                raise ValueError("EMBEDDING_MODEL_MISMATCH")
            rows = response.get("embeddings")
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise ValueError("INVALID_EMBEDDING_RESPONSE_INDICES")
            vectors = [validate_vector(row, cfg.dimensions) for row in rows]
            record["status"] = "completed"
            return vectors
        except Exception as error:
            record.update(failure(error))
            raise RuntimeError("EMBEDDING_CALL_FAILED_SEE_AUDIT") from None
