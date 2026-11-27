"""커넥션 풀 전송: 접속 정보 해석, 자동 대체, 실제 DB에서 docker 전송과 같은 결과."""
import os

import pytest

from doc_understanding.storage.staging import local


@pytest.fixture(autouse=True)
def fresh_pool():
    local.close_pool()
    yield
    local.close_pool()


def test_conninfo_normalizes_sqlalchemy_url_and_localhost(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5433/nh_document")
    assert local._conninfo() == "postgresql://u:p@127.0.0.1:5433/nh_document"


def test_auto_mode_falls_back_to_docker_when_pool_cannot_connect(monkeypatch):
    monkeypatch.setenv("NH_DB_TRANSPORT", "auto")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nh_document")
    calls = []
    monkeypatch.setattr(local.subprocess, "run", lambda *a, **k: calls.append(1) or type(
        "R", (), dict(returncode=0, stdout=b"SET\n[]", stderr=b""))())
    assert local.query("SELECT 1") == [] and calls and local.transport() == "docker"


def test_pool_mode_raises_instead_of_silently_falling_back(monkeypatch):
    monkeypatch.setenv("NH_DB_TRANSPORT", "pool")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nh_document")
    with pytest.raises(local.LocalPostgresError):
        local.sql("SELECT 1")


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("NH_TEST_LOCAL_POSTGRES") != "1", reason="opt-in local PostgreSQL")
def test_actual_pool_and_docker_return_identical_results(monkeypatch):
    statement = "SELECT E'a\nb' AS t, 'c'||chr(13)||chr(10)||'d' AS u, NULL::text AS n, 1.5::float AS f, '{\"k\":1}'::jsonb AS j"
    monkeypatch.setenv("NH_DB_TRANSPORT", "pool")
    pooled = local.query(statement)
    assert local.transport() == "pool"
    monkeypatch.setenv("NH_DB_TRANSPORT", "docker")
    assert local.query(statement) == pooled
    monkeypatch.setenv("NH_DB_TRANSPORT", "pool")
    with pytest.raises(local.LocalPostgresError) as error:
        local.sql("BEGIN; SELECT 1/0; COMMIT;")
    assert error.value.sqlstate == "22012" and local.query("SELECT 2 AS y") == [{"y": 2}]


def test_render_binds_values_safely_and_checks_count():
    sql = local.render("SELECT %s, %s, %s, %s, '100%%', %s", ["a'b", None, 3, 1.5, ["x", "y"]])
    assert sql == "SELECT 'a''b', NULL, 3, 1.5, '100%', ARRAY['x','y']::text[]"
    with pytest.raises(ValueError, match="SQL_PARAMETER_COUNT_MISMATCH"):
        local.render("SELECT %s, %s", ["only one"])
    with pytest.raises(ValueError, match="SQL_PARAMETER_COUNT_MISMATCH"):
        local.render("SELECT %s", ["a", "extra"])


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("NH_TEST_LOCAL_POSTGRES") != "1", reason="opt-in local PostgreSQL")
@pytest.mark.parametrize("mode", ["pool", "docker"])
def test_actual_parameter_queries_match_across_transports(monkeypatch, mode):
    monkeypatch.setenv("NH_DB_TRANSPORT", mode)
    rows = local.query_params("SELECT %s::text AS t, %s::int AS n, x FROM unnest(%s::text[]) x ORDER BY x",
                              ["it's\n줄바꿈", 7, ["b", "a"]])
    assert rows == [dict(t="it's\n줄바꿈", n=7, x="a"), dict(t="it's\n줄바꿈", n=7, x="b")]
