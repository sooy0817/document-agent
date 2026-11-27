# 역할: 임베딩 CLI 설정을 구성한다. 단가는 호출자가 명시하고 키는 환경변수에서만 읽는다.
from doc_understanding.rag.embedding.client import EmbeddingClient
from doc_understanding.rag.embedding.models import EmbeddingConfig


LOCAL_DEFAULTS = dict(model="bge-m3", dimensions=1024)


def add_arguments(parser):
    # local은 이 컴퓨터의 Ollama(기본 http://127.0.0.1:11434)를 사용하며 외부 전송·단가가 필요 없다.
    # compatible은 OpenAI 형식 서버(`<base-url>/embeddings`)다. 모델·차원·주소를 직접 지정한다.
    parser.add_argument("--embedding-provider", choices=["openai", "local", "compatible"], default="openai")
    parser.add_argument("--embedding-base-url")
    parser.add_argument("--embedding-api-key-env", help="compatible 서버의 키가 든 환경변수 이름")
    parser.add_argument("--embedding-model")
    parser.add_argument("--embedding-dimensions", type=int)
    parser.add_argument("--embedding-profile-version", default="1")
    parser.add_argument("--embedding-input-usd-per-million", type=float)
    parser.add_argument("--max-embedding-cost-usd", type=float, default=1.0)
    parser.add_argument("--max-embedding-calls", type=int, default=64)


def client_from_args(args):
    provider = getattr(args, "embedding_provider", "openai")
    local = provider == "local"
    price = args.embedding_input_usd_per_million
    if provider != "openai":
        price = 0.0 if price is None else price
    elif price is None:
        raise ValueError("EXPLICIT_EMBEDDING_PRICE_REQUIRED")
    if provider == "compatible" and not (args.embedding_model and args.embedding_dimensions):
        raise ValueError("COMPATIBLE_EMBEDDING_NEEDS_MODEL_AND_DIMENSIONS")
    defaults = LOCAL_DEFAULTS if local else dict(model="text-embedding-3-small", dimensions=1536)
    config = EmbeddingConfig(
        provider=provider,
        base_url=getattr(args, "embedding_base_url", None),
        api_key_env=getattr(args, "embedding_api_key_env", None),
        model=args.embedding_model or defaults["model"],
        dimensions=args.embedding_dimensions or defaults["dimensions"],
        profile_version=args.embedding_profile_version,
        input_usd_per_million=price,
        max_cost_usd=args.max_embedding_cost_usd,
        max_calls=args.max_embedding_calls,
        # CPU 로컬 모델은 배치당 처리가 느려 45초 기본값으로는 시간 초과가 난다.
        **(dict(batch_size=8, timeout_seconds=120.0) if local else {}),
    )
    return EmbeddingClient(config, allow_external=args.allow_external)


def retriever_from_args(args):
    if args.retrieval_mode == "lexical":
        return None
    from doc_understanding.rag.retrieval.runtime import Retriever

    return Retriever(args.retrieval_mode, client_from_args(args))
