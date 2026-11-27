# 역할: 임베딩 설정·벡터 계약과 재사용 키를 정의한다.
import hashlib
import json
import math
import struct
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
DEFAULT_LOCAL_URL = "http://127.0.0.1:11434"


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    # local은 이 컴퓨터의 Ollama 서버다. 문서 텍스트가 외부로 나가지 않도록 루프백 주소만 허용한다.
    # compatible은 사용자가 고른 OpenAI 형식(`POST <base_url>/embeddings`) 서버다. 사내 추론 서버, vLLM, TEI,
    # Ollama의 /v1 등 어떤 모델이든 주소·모델 이름·차원만 주면 쓴다. 이 컴퓨터가 아닌 주소면 외부 전송으로 본다.
    provider: Literal["openai", "local", "compatible"] = "openai"
    base_url: str | None = Field(default=None, max_length=200)
    # compatible 서버가 키를 요구할 때 키가 든 환경변수 이름. 키 자체는 설정에 적지 않는다.
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    model: str = Field(default="text-embedding-3-small", min_length=1)
    dimensions: int = Field(default=1536, ge=1, le=3072)
    profile_version: str = Field(default="1", min_length=1)
    input_usd_per_million: float = Field(ge=0)
    max_cost_usd: float = Field(default=1.0, gt=0)
    max_calls: int = Field(default=64, ge=1, le=1000)
    batch_size: int = Field(default=32, ge=1, le=32)
    max_input_bytes: int = Field(default=7500, ge=1, le=8191)
    timeout_seconds: float = Field(default=45.0, gt=0, le=120)

    @model_validator(mode="after")
    def _check_endpoint(self):
        if self.provider != "compatible" and self.api_key_env is not None:
            raise ValueError("API_KEY_ENV_ONLY_FOR_COMPATIBLE_PROVIDER")
        if self.provider == "openai":
            if self.base_url is not None:
                raise ValueError("BASE_URL_ONLY_FOR_LOCAL_PROVIDER")
            return self
        if self.provider == "compatible":
            parts = urlsplit(self.base_url or "")
            if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password
                    or parts.query or parts.fragment):
                raise ValueError("COMPATIBLE_EMBEDDING_URL_REQUIRED")
            return self
        parts = urlsplit(self.endpoint)
        if (
            parts.scheme != "http"
            or parts.hostname not in LOCAL_HOSTS
            or parts.username
            or parts.password
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError("LOCAL_EMBEDDING_URL_MUST_BE_LOOPBACK")
        if self.input_usd_per_million != 0:
            raise ValueError("LOCAL_EMBEDDING_PRICE_MUST_BE_ZERO")
        return self

    @property
    def endpoint(self):
        if self.provider == "compatible":
            return self.base_url.rstrip("/")
        return (self.base_url or DEFAULT_LOCAL_URL).rstrip("/") if self.provider == "local" else None

    @property
    def external(self):
        # 문서 글자가 이 컴퓨터 밖으로 나가는지(외부 전송 동의가 필요한지) 나타낸다.
        if self.provider == "compatible":
            return urlsplit(self.base_url).hostname not in LOCAL_HOSTS
        return self.provider == "openai"

    @property
    def key_env(self):
        # API 키를 읽을 환경변수 이름. 키가 필요 없으면 None.
        return "OPENAI_API_KEY" if self.provider == "openai" else self.api_key_env

    @property
    def profile(self):
        # profile_version은 운영자가 지정하는 재색인 세대다. 공급자의 모델 고정 버전은 아니다.
        # 기존 OpenAI 프로필 ID를 유지하고, 로컬 모델은 provider로 구분해 벡터가 섞이지 않게 한다.
        return dict(
            provider=self.provider,
            model=self.model,
            dimensions=self.dimensions,
            profile_version=self.profile_version,
            input_version="search-text/1",
        )

    @property
    def profile_id(self):
        return input_hash(json.dumps(self.profile, sort_keys=True, ensure_ascii=False))


def input_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_vector(vector, dimensions):
    if (
        not isinstance(vector, list)
        or len(vector) != dimensions
        or any(
            isinstance(x, bool)
            or not isinstance(x, (int, float))
            or not math.isfinite(x)
            or abs(x) > 3.4e38
            for x in vector
        )
        or not any(x != 0 for x in vector)
    ):
        raise ValueError("INVALID_EMBEDDING_VECTOR")
    normalized = [struct.unpack("f", struct.pack("f", float(x)))[0] for x in vector]
    if not any(normalized):
        raise ValueError("INVALID_EMBEDDING_VECTOR")
    return normalized
