"""SQL 표준입력의 원문 바이트 보존과 오류 정보 최소화 회귀. Docker는 호출하지 않는다."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from doc_understanding.storage.staging import local


@pytest.fixture(autouse=True)
def docker_transport(monkeypatch):
    # 이 파일은 docker psql 전송을 검사한다. 커넥션 풀 전송은 test_pooled_sql_transport.py에서 검사한다.
    monkeypatch.setenv("NH_DB_TRANSPORT", "docker")


def test_sql_stdin_preserves_mixed_newlines_through_actual_pipe(monkeypatch):
    run = subprocess.run
    received = []
    statement = "SELECT '첫 줄\n둘째 줄\r\n셋째 줄\r끝';"

    def pipe(command, **kwargs):
        # 같은 subprocess 옵션으로 실제 자식 프로세스까지 전달된 바이트를 확인한다.
        result = run(
            [sys.executable, "-c", "import sys; print(sys.stdin.buffer.read().hex())"], **kwargs
        )
        output = (
            result.stdout.decode("ascii") if isinstance(result.stdout, bytes) else result.stdout
        )
        received.append(bytes.fromhex(output.strip()))
        binary = isinstance(kwargs["input"], bytes)
        return SimpleNamespace(
            returncode=0,
            stdout=b"SET\nok\n" if binary else "SET\nok\n",
            stderr=b"" if binary else "",
        )

    monkeypatch.setattr(local.subprocess, "run", pipe)
    assert local.sql(statement) == "ok"
    assert received == [("SET standard_conforming_strings=on;\n" + statement).encode("utf-8")]


@pytest.mark.parametrize(
    "stderr,state",
    [
        (b"ERROR:  23514\nDETAIL: private document\n", "23514"),
        (b"psql:<stdin>:42: ERROR:  42601\nLINE 1: secret SQL\n", "42601"),
        (b"WARNING:  01000\nERROR:  23505\nDETAIL: private row\n", "23505"),
        (b"permission denied: private host path", None),
    ],
)
def test_error_exposes_only_sqlstate(monkeypatch, stderr, state):
    def fail(command, **kwargs):
        assert "VERBOSITY=sqlstate" in command
        assert isinstance(kwargs["input"], bytes) and "encoding" not in kwargs
        return SimpleNamespace(returncode=3, stdout=b"", stderr=stderr)

    monkeypatch.setattr(local.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="LOCAL_POSTGRES_STATEMENT_FAILED") as caught:
        local.sql("SELECT 'private source';")
    assert caught.value.sqlstate == state
    assert str(caught.value) == "LOCAL_POSTGRES_STATEMENT_FAILED" + (
        f" SQLSTATE={state}" if state else ""
    )


def test_query_decodes_utf8_without_changing_escaped_json_newlines(monkeypatch):
    import json

    expected = [{"text": "첫 줄\r\n둘째 줄\n"}]
    output = ("SET\n" + json.dumps(expected, ensure_ascii=False) + "\n").encode("utf-8")
    monkeypatch.setattr(
        local.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=output, stderr=b""),
    )
    assert local.query("SELECT synthetic_text") == expected
