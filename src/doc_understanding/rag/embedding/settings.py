# 역할: 설치마다 고르는 임베딩 모델 설정을 읽는다. 모델을 코드에 박지 않고 사용자가 정한다.
"""설정 파일은 `config/embedding.json`(또는 환경변수 `NH_EMBEDDING_CONFIG`가 가리키는 파일)이다.

내용은 EmbeddingConfig 항목 그대로다. 예:
    {"provider": "compatible", "base_url": "http://10.0.0.5:8000/v1", "model": "bge-m3", "dimensions": 1024,
     "input_usd_per_million": 0, "api_key_env": "EMBEDDING_API_KEY"}
    {"provider": "local", "model": "bge-m3", "dimensions": 1024, "input_usd_per_million": 0}
    {"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536, "input_usd_per_million": 0.02}
    {"provider": "none"}        임베딩 없이 키워드 검색만 쓴다
키는 파일에 적지 않는다(환경변수 이름만 적는다). 외부 전송 동의는 파일이 아니라 실행할 때 따로 준다.
"""
import json
import os
from pathlib import Path

from doc_understanding.rag.embedding.models import EmbeddingConfig

ROOT = Path(__file__).resolve().parents[4]
FLOATS = ("input_usd_per_million", "max_cost_usd", "timeout_seconds")


def path():
    return Path(os.environ.get("NH_EMBEDDING_CONFIG") or ROOT / "config" / "embedding.json")


def configured():
    """설정 파일 내용(dict). 파일이 없으면 None(정하지 않음)."""
    file = path()
    if not file.is_file():
        return None
    value = json.loads(file.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or not isinstance(value.get("provider"), str):
        raise ValueError("EMBEDDING_CONFIG_INVALID")
    return value


def normalized(value):
    """JSON에서 정수로 적힌 금액·시간을 실수로 바꾼다(EmbeddingConfig는 형을 엄격히 본다)."""
    return {k: float(v) if k in FLOATS and isinstance(v, int) and not isinstance(v, bool) else v for k, v in value.items()}


def config():
    """설치에서 고른 EmbeddingConfig. 정하지 않았거나 `none`이면 None."""
    value = configured()
    if value is None or value["provider"] == "none":
        return None
    return EmbeddingConfig(**normalized(value))
