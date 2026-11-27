"""임베딩 계약·예산·재실행 회귀. 외부 모델은 명시적 모의 응답으로 대체한다."""

import json
from types import SimpleNamespace

import pytest

from doc_understanding.rag.embedding.client import EmbeddingClient
from doc_understanding.rag.embedding.models import EmbeddingConfig, input_hash, validate_vector
from doc_understanding.rag.embedding import indexer, repository


@pytest.fixture(autouse=True)
def no_cross_run_copy(monkeypatch):
    # 실제 DB를 쓰지 않는 단위 테스트에서는 다른 실행의 벡터 복사를 0건으로 둔다.
    monkeypatch.setattr(repository, "copy_identical", lambda *_: 0)


def config(**kwargs):
    return EmbeddingConfig(**(dict(dimensions=3, input_usd_per_million=1.0) | kwargs))


def response(payload, *_):
    return dict(
        model=payload["model"],
        usage={"prompt_tokens": 4},
        data=[
            {"index": i, "embedding": [1.0, float(i), 0.0]}
            for i in reversed(range(len(payload["input"])))
        ],
    )


def test_response_order_and_profile_are_explicit():
    client = EmbeddingClient(config(), allow_external=True, transport=response)
    assert client.embed(["First", "Second"]) == [[1.0, 0.0, 0.0], [1.0, 1.0, 0.0]]
    assert client.audit[0]["input_tokens"] == 4
    assert client.audit[0]["input_hashes"] == [input_hash("First"), input_hash("Second")]
    assert config().profile_id != config(dimensions=2).profile_id
    assert config().profile_id != config(model="another-model").profile_id
    assert config().profile_id != config(profile_version="2").profile_id
    assert config().profile_id == config(max_cost_usd=2.0, input_usd_per_million=2.0).profile_id


@pytest.mark.parametrize(
    "vector",
    [
        [0, 0, 0],
        [1, 0],
        [True, 0, 1],
        [float("nan"), 0, 1],
        [float("inf"), 0, 1],
        [1e39, 0, 1],
        [1e-90, 0, 0],
    ],
)
def test_bad_vectors_are_rejected(vector):
    with pytest.raises(ValueError, match="INVALID_EMBEDDING_VECTOR"):
        validate_vector(vector, 3)


@pytest.mark.parametrize("texts", [[], [""], [" "], ["x" * 7501], ["🌱" * 2000], ["x"] * 33])
def test_invalid_inputs_never_call_transport(texts):
    client = EmbeddingClient(
        config(), allow_external=True, transport=lambda *_: pytest.fail("unexpected external call")
    )
    with pytest.raises(ValueError, match="EMBEDDING_INPUT_LIMIT"):
        client.embed(texts)


def test_authorization_key_and_cumulative_budget_are_checked_before_call(monkeypatch):
    client = EmbeddingClient(config(max_calls=1), transport=response)
    with pytest.raises(ValueError, match="EXTERNAL_CALL_NOT_ENABLED"):
        client.embed(["x"])
    client.allow_external = True
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValueError, match="API_KEY_UNAVAILABLE"):
        client.embed(["x"])
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    client.embed(["x"])
    with pytest.raises(ValueError, match="EMBEDDING_RUN_BUDGET_EXCEEDED"):
        client.embed(["x"])
    expensive = EmbeddingClient(
        config(max_cost_usd=0.000001),
        allow_external=True,
        transport=lambda *_: pytest.fail("budget should block call"),
    )
    with pytest.raises(ValueError, match="EMBEDDING_RUN_BUDGET_EXCEEDED"):
        expensive.embed(["x"])


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "dimension", "model", "usage"])
def test_malformed_responses_do_not_produce_vectors(mutation):
    def bad(payload, *_):
        result = response(payload)
        if mutation == "duplicate":
            result["data"][0]["index"] = result["data"][1]["index"]
        if mutation == "missing":
            result["data"].pop()
        if mutation == "dimension":
            result["data"][0]["embedding"] = [1.0]
        if mutation == "model":
            result["model"] = "wrong"
        if mutation == "usage":
            result["usage"] = {}
        return result

    client = EmbeddingClient(config(), allow_external=True, transport=bad)
    with pytest.raises(RuntimeError, match="EMBEDDING_CALL_FAILED"):
        client.embed(["a", "b"])
    assert client.audit[0]["status"] == "failed"


def test_transport_error_is_redacted_and_not_retried():
    def fail(*_):
        raise OSError("sensitive source and credential")

    client = EmbeddingClient(config(), allow_external=True, transport=fail)
    with pytest.raises(RuntimeError, match="EMBEDDING_CALL_FAILED") as caught:
        client.embed(["private source"])
    assert "sensitive" not in str(caught.value)
    assert len(client.audit) == 1 and "private source" not in json.dumps(client.audit)


def test_incremental_batches_deduplicate_text_and_report_oversized(monkeypatch):
    entries = [
        dict(entry_id="old", text="old"),
        dict(entry_id="a", text="same"),
        dict(entry_id="b", text="same"),
        dict(entry_id="large", text="x" * 7501),
    ]
    saved = []
    monkeypatch.setattr(repository, "load_entries", lambda _: entries)
    monkeypatch.setattr(repository, "cached_hashes", lambda *_: {"old": input_hash("old")})
    monkeypatch.setattr(repository, "save_batch", lambda *args: saved.extend(args[-1]))
    client = EmbeddingClient(config(), allow_external=True, transport=response)
    result = indexer.embed_run("run", client)
    assert result["reused"] == 1 and result["written"] == 2
    assert result["skipped"] == [{"entry_id": "large", "reason": "EMPTY_OR_OVERSIZED_INPUT"}]
    assert not result["coverage_complete"] and client.audit[0]["inputs"] == 1
    assert [i["entry_id"] for i in saved] == ["a", "b"]


def test_resume_reuses_successful_batches_after_failure(monkeypatch):
    entries = [dict(entry_id=str(i), text=str(i)) for i in range(3)]
    stored = {}
    monkeypatch.setattr(repository, "load_entries", lambda _: entries)
    monkeypatch.setattr(repository, "cached_hashes", lambda *_: stored.copy())
    monkeypatch.setattr(
        repository,
        "save_batch",
        lambda _, cfg, batch: stored.update({e["entry_id"]: input_hash(e["text"]) for e in batch}),
    )
    calls = []

    def fail_second(payload, *_):
        calls.append(payload)
        if len(calls) == 2:
            raise OSError("simulated")
        return response(payload)

    client = EmbeddingClient(config(batch_size=1), allow_external=True, transport=fail_second)
    with pytest.raises(RuntimeError):
        indexer.embed_run("run", client)
    assert list(stored) == ["0"]
    retry = EmbeddingClient(config(batch_size=1), allow_external=True, transport=response)
    result = indexer.embed_run("run", retry)
    assert result["reused"] == 1 and result["written"] == 2 and len(retry.audit) == 2


def test_cached_run_does_not_need_external_permission(monkeypatch):
    monkeypatch.setattr(repository, "load_entries", lambda _: [dict(entry_id="a", text="same")])
    monkeypatch.setattr(repository, "cached_hashes", lambda *_: {"a": input_hash("same")})
    monkeypatch.setattr(repository, "save_batch", lambda *_: pytest.fail("unexpected write"))
    result = indexer.embed_run("run", EmbeddingClient(config()))
    assert result["coverage_complete"] and result["calls"] == []


def test_save_guards_source_and_reindex_inside_transaction(monkeypatch):
    statements = []
    monkeypatch.setattr(repository, "sql", statements.append)
    repository.save_batch(
        "run'1", config(), [dict(entry_id="e'1", text="source's text", vector=[1, 0, 0])]
    )
    statement = statements[0]
    assert statement.startswith("BEGIN;") and statement.endswith("COMMIT;")
    assert "CHECK(ok)" in statement and "count(*)=1" in statement
    assert "run''1" in statement and "source''s text" in statement
    assert "ON CONFLICT(run_id,entry_id,profile_id)" in statement
    assert repository.INDEX_VERSION in statement
    assert input_hash("source's text") in statement


def test_save_validates_entire_batch_before_sql(monkeypatch):
    monkeypatch.setattr(repository, "sql", lambda _: pytest.fail("unexpected SQL write"))
    with pytest.raises(ValueError):
        repository.save_batch(
            "run",
            config(),
            [
                dict(entry_id="a", text="x", vector=[1, 0, 0]),
                dict(entry_id="b", text="x", vector=[0, 0, 0]),
            ],
        )


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"index_version": "old", "status": "COMPLETE"}],
        [{"index_version": repository.INDEX_VERSION, "status": "FAILED"}],
    ],
)
def test_old_or_failed_document_needs_reindex(monkeypatch, rows):
    monkeypatch.setattr(repository, "query", lambda _: rows)
    with pytest.raises(ValueError, match="READY_CURRENT_INDEX_REQUIRED"):
        repository.load_entries("run")


def test_failed_command_persists_audit_without_secrets(monkeypatch, tmp_path):
    from doc_understanding.rag.embedding import commands

    client = EmbeddingClient(
        config(), allow_external=True, transport=lambda *_: (_ for _ in ()).throw(OSError("secret"))
    )
    monkeypatch.setattr(commands, "client_from_args", lambda _: client)
    monkeypatch.setattr(commands, "embed_run", lambda *_: client.embed(["confidential"]))
    args = SimpleNamespace(run_id="run", output=tmp_path / "audit.json")
    with pytest.raises(RuntimeError):
        commands.run_embed(args)
    result = json.loads(args.output.read_text(encoding="utf-8"))
    assert result["status"] == "failed" and len(result["calls"]) == 1
    assert not result["cost_complete"]
    assert "secret" not in args.output.read_text() and "confidential" not in args.output.read_text()


# --- 로컬(Ollama) 임베딩: 외부 전송 없이 같은 저장·검증 계약을 따른다. ---


def local_config(**kwargs):
    return EmbeddingConfig(
        **(dict(provider="local", model="bge-m3", dimensions=3, input_usd_per_million=0.0) | kwargs)
    )


def local_response(payload, endpoint, timeout):
    return dict(
        model=payload["model"] + ":latest",
        prompt_eval_count=7,
        embeddings=[[1.0, float(i), 0.0] for i in range(len(payload["input"]))],
    )


def test_local_embedding_needs_no_key_or_external_permission(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    seen = []

    def local(payload, endpoint, timeout):
        seen.append((payload, endpoint))
        return local_response(payload, endpoint, timeout)

    client = EmbeddingClient(
        local_config(), transport=lambda *_: pytest.fail("no OpenAI call"), local=local
    )
    assert client.embed(["가", "나"]) == [[1.0, 0.0, 0.0], [1.0, 1.0, 0.0]]
    payload, endpoint = seen[0]
    # 문맥 길이 초과 입력을 몰래 자르지 않도록 truncate=false를 보내고, 차원은 모델이 정한다.
    assert payload == dict(model="bge-m3", input=["가", "나"], truncate=False)
    assert endpoint == "http://127.0.0.1:11434"
    record = client.audit[0]
    assert record["provider"] == "local" and record["input_tokens"] == 7
    assert record["estimated_cost_usd"] == 0.0 and record["status"] == "completed"


def test_local_profile_never_mixes_with_openai_vectors():
    assert local_config().profile["provider"] == "local"
    assert config(dimensions=3).profile["provider"] == "openai"
    assert local_config(model="text-embedding-3-small").profile_id != config().profile_id
    # 같은 모델·차원이면 서버 주소가 달라도 같은 프로필로 재사용한다.
    assert local_config().profile_id == local_config(base_url="http://localhost:11434").profile_id


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:11434",
        "http://example.com:11434",
        "http://10.0.0.5:11434",
        "http://user:pw@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1.example.com",
    ],
)
def test_local_endpoint_must_be_plain_loopback(url):
    with pytest.raises(ValueError, match="LOCAL_EMBEDDING_URL_MUST_BE_LOOPBACK"):
        local_config(base_url=url)


def test_local_price_must_be_zero_and_base_url_is_local_only():
    with pytest.raises(ValueError, match="LOCAL_EMBEDDING_PRICE_MUST_BE_ZERO"):
        local_config(input_usd_per_million=0.1)
    with pytest.raises(ValueError, match="BASE_URL_ONLY_FOR_LOCAL_PROVIDER"):
        config(base_url="http://127.0.0.1:11434")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(model="other-model"),
        lambda r: r.update(embeddings=r["embeddings"][:1]),
        lambda r: r.update(embeddings=[[1.0, 0.0]] * 2),
        lambda r: r.pop("embeddings"),
    ],
)
def test_local_malformed_responses_fail_without_vectors(mutation):
    def bad(payload, endpoint, timeout):
        value = local_response(payload, endpoint, timeout)
        mutation(value)
        return value

    client = EmbeddingClient(local_config(), local=bad)
    with pytest.raises(RuntimeError, match="EMBEDDING_CALL_FAILED_SEE_AUDIT"):
        client.embed(["가", "나"])
    assert client.audit[0]["status"] == "failed"


def test_local_missing_token_count_keeps_zero_cost_complete():
    client = EmbeddingClient(
        local_config(),
        local=lambda p, e, t: dict(model="bge-m3", embeddings=[[1.0, 0.0, 0.0]]),
    )
    client.embed(["가"])
    assert client.audit[0]["input_tokens"] is None
    assert client.audit[0]["estimated_cost_usd"] == 0.0


def test_local_cli_defaults_need_no_price():
    from doc_understanding.rag.embedding.options import client_from_args

    args = SimpleNamespace(
        embedding_provider="local", embedding_base_url=None, embedding_model=None,
        embedding_dimensions=None, embedding_profile_version="1",
        embedding_input_usd_per_million=None, max_embedding_cost_usd=1.0,
        max_embedding_calls=64, allow_external=False,
    )
    client = client_from_args(args)
    assert (client.config.model, client.config.dimensions) == ("bge-m3", 1024)
    assert client.config.input_usd_per_million == 0.0 and not client.config.external


def test_vectors_copied_from_other_runs_are_reused_without_api_calls(monkeypatch):
    # 다른 실행에서 같은 텍스트 벡터를 복사하면 cached_hashes에 나타나고 API는 호출하지 않는다.
    monkeypatch.setattr(repository, "copy_identical", lambda *_: 2)
    monkeypatch.setattr(repository, "load_entries", lambda _: [dict(entry_id="a", text="x"), dict(entry_id="b", text="y")])
    monkeypatch.setattr(repository, "cached_hashes", lambda *_: {"a": input_hash("x"), "b": input_hash("y")})
    monkeypatch.setattr(repository, "save_batch", lambda *_: pytest.fail("unexpected write"))
    client = EmbeddingClient(config(), allow_external=True, transport=lambda *_: pytest.fail("no API call"))
    result = indexer.embed_run("run", client)
    assert result["copied_from_other_runs"] == 2 and result["reused"] == 2 and result["coverage_complete"]


def compatible_config(**kwargs):
    return EmbeddingConfig(**(dict(provider="compatible", base_url="http://127.0.0.1:9000/v1", model="any-model",
                                   dimensions=3, input_usd_per_million=0.0) | kwargs))


def test_any_openai_format_server_and_model_can_be_chosen(monkeypatch):
    seen = []

    def server(payload, endpoint, key, timeout):
        seen.append((payload, endpoint, key))
        return dict(model=payload["model"], data=[{"index": i, "embedding": [1.0, float(i), 0.0]}
                                                    for i in range(len(payload["input"]))])

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = EmbeddingClient(compatible_config(), compatible=server, transport=lambda *_: pytest.fail("not OpenAI"))
    assert client.embed(["a", "b"]) == [[1.0, 0.0, 0.0], [1.0, 1.0, 0.0]]
    payload, endpoint, key = seen[0]
    # 주소와 모델은 설정 그대로, 키는 없고, 차원 축소 인자는 보내지 않는다(모델이 차원을 정한다).
    assert (endpoint, key, payload["model"]) == ("http://127.0.0.1:9000/v1", None, "any-model") and "dimensions" not in payload
    # 토큰 수를 주지 않는 서버도 완료로 본다.
    assert client.audit[0]["status"] == "completed" and client.audit[0]["input_tokens"] is None
    # 응답 벡터 길이가 설정한 차원과 다르면 저장하지 않는다.
    wrong = EmbeddingClient(compatible_config(dimensions=4), compatible=server)
    with pytest.raises(RuntimeError, match="EMBEDDING_CALL_FAILED_SEE_AUDIT"):
        wrong.embed(["a"])
    assert compatible_config().profile_id not in (config().profile_id, compatible_config(model="other").profile_id)


def test_a_chosen_server_off_this_computer_needs_consent_and_its_own_key(monkeypatch):
    remote = compatible_config(base_url="https://embed.internal.example/v1", api_key_env="INTERNAL_EMBED_KEY")
    assert remote.external and remote.key_env == "INTERNAL_EMBED_KEY" and not compatible_config().external
    calls = []

    def server(payload, endpoint, key, timeout):
        calls.append(key)
        return dict(data=[{"index": 0, "embedding": [1.0, 0.0, 0.0]}])

    monkeypatch.delenv("INTERNAL_EMBED_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-sent")
    with pytest.raises(ValueError, match="EXTERNAL_CALL_NOT_ENABLED"):
        EmbeddingClient(remote, compatible=server).embed(["a"])
    with pytest.raises(ValueError, match="API_KEY_UNAVAILABLE"):
        EmbeddingClient(remote, allow_external=True, compatible=server).embed(["a"])
    monkeypatch.setenv("INTERNAL_EMBED_KEY", "k")
    assert EmbeddingClient(remote, allow_external=True, compatible=server).embed(["a"]) == [[1.0, 0.0, 0.0]]
    assert calls == ["k"]  # OpenAI 키는 다른 서버로 가지 않는다


@pytest.mark.parametrize("fields", [
    dict(base_url=None), dict(base_url="ftp://host/v1"), dict(base_url="http://user:pw@host/v1"),
    dict(base_url="http://host/v1?x=1"), dict(api_key_env="lower_case")])
def test_chosen_server_settings_are_checked(fields):
    with pytest.raises(ValueError):
        compatible_config(**fields)
    with pytest.raises(ValueError):
        config(api_key_env="SOME_KEY")  # OpenAI·local에는 키 이름을 따로 주지 않는다


def test_installed_choice_is_read_from_the_settings_file(tmp_path, monkeypatch):
    from doc_understanding.rag.embedding import settings

    file = tmp_path / "embedding.json"
    monkeypatch.setenv("NH_EMBEDDING_CONFIG", str(file))
    assert settings.configured() is None and settings.config() is None
    file.write_text(json.dumps(dict(provider="compatible", base_url="http://10.0.0.5:8000/v1", model="m", dimensions=8,
                                    input_usd_per_million=0)))
    chosen = settings.config()
    assert (chosen.provider, chosen.model, chosen.dimensions, chosen.input_usd_per_million) == ("compatible", "m", 8, 0.0)
    file.write_text(json.dumps(dict(provider="none")))
    assert settings.config() is None and settings.configured() == dict(provider="none")
    file.write_text("[]")
    with pytest.raises(ValueError, match="EMBEDDING_CONFIG_INVALID"):
        settings.configured()


def rejecting_server(too_long, status=400):
    import urllib.error

    def server(payload, endpoint, key, timeout):
        if any(text in too_long for text in payload["input"]):
            raise urllib.error.HTTPError(endpoint, status, "rejected", None, None)
        return dict(data=[{"index": i, "embedding": [1.0, 0.0, 0.0]} for i in range(len(payload["input"]))])
    return server


def test_an_input_the_server_rejects_is_skipped_and_the_rest_of_the_document_is_embedded(monkeypatch):
    entries = [dict(entry_id=f"e{i}", text=text) for i, text in enumerate(["a", "TOO LONG", "c", "d", "TOO LONG"])]
    saved = []
    monkeypatch.setattr(repository, "load_entries", lambda _: entries)
    monkeypatch.setattr(repository, "cached_hashes", lambda *_: {})
    monkeypatch.setattr(repository, "save_batch", lambda _run, _cfg, items: saved.extend(i["entry_id"] for i in items))
    client = EmbeddingClient(compatible_config(batch_size=4), compatible=rejecting_server({"TOO LONG"}, 413))
    result = indexer.embed_run("run", client)
    assert saved == ["e0", "e2", "e3"] and result["written"] == 3 and result["coverage_complete"] is False
    assert sorted((s["entry_id"], s["reason"]) for s in result["skipped"]) == [
        ("e1", "PROVIDER_REJECTED_INPUT"), ("e4", "PROVIDER_REJECTED_INPUT")]
    assert [c.get("http_status") for c in client.audit if c["status"] == "failed"] == [413, 413]


def test_other_failures_and_a_server_that_accepts_nothing_still_fail(monkeypatch):
    entries = [dict(entry_id="e0", text="a"), dict(entry_id="e1", text="b")]
    monkeypatch.setattr(repository, "load_entries", lambda _: entries)
    monkeypatch.setattr(repository, "cached_hashes", lambda *_: {})
    monkeypatch.setattr(repository, "save_batch", lambda *_: pytest.fail("nothing to save"))
    # 서버 오류(500)는 입력 거부가 아니다. 건너뛰지 않고 실패한다.
    with pytest.raises(RuntimeError, match="EMBEDDING_CALL_FAILED_SEE_AUDIT"):
        indexer.embed_run("run", EmbeddingClient(compatible_config(), compatible=rejecting_server({"a", "b"}, 500)))
    # 모든 입력이 거부되면 설정 문제로 보고 실패한다(모델 이름·차원이 틀린 경우).
    with pytest.raises(RuntimeError, match="EMBEDDING_CALL_FAILED_SEE_AUDIT"):
        indexer.embed_run("run", EmbeddingClient(compatible_config(), compatible=rejecting_server({"a", "b"}, 400)))
