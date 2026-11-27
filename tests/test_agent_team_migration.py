"""Migration contracts: existing calls replay, and the worker has no UI dependency."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from doc_understanding.agents.roles import analyst, judge
from doc_understanding.experiments.parser_repair.engine import freeze
from doc_understanding.agents.workflows.axis_review import profiles as axis
from doc_understanding.agents.workflows.evidence_review import profiles as evidence
from doc_understanding.agents.workflows.table_review import profiles as table


def test_persisted_queue_modes_dispatch_to_the_same_team_services():
    from doc_understanding.agents.workflows.registry import get_executor, TEAM_SERVICES
    assert set(TEAM_SERVICES) == {"agents", "agents_v2", "agents_roles"}
    for mode, module in TEAM_SERVICES.items():
        assert get_executor(mode).__module__ == module


def test_existing_prompt_and_schema_fingerprints_are_preserved():
    expected = json.loads(
        Path("tests/fixtures/agent_profiles_before_refactor.json").read_text("utf8")
    )
    current = {
        "legacy": {"analyst": analyst.ROLE.profile, "judge": judge.ROLE.profile},
        "evidence": evidence.PROFILES,
        "table": table.PROFILES,
        "axis": axis.PROFILES,
    }
    for team, profiles in current.items():
        assert profiles.keys() == expected[team].keys()
        for name, (prompt, schema) in profiles.items():
            assert (
                hashlib.sha256(prompt.encode()).hexdigest() == expected[team][name]["prompt_sha256"]
            )
            assert schema.model_json_schema() == expected[team][name]["schema"]


def test_packaged_document_worker_runs_without_web_or_models(tmp_path):
    output = tmp_path / "worker"
    environment = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "doc_understanding.services.documents",
            "reports/physical-fixtures/horizontal.pdf",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    document = json.loads((output / "document.json").read_text("utf8"))
    assert document["physical_tables"]
    assert (output / "nested.json").is_file()
    assert (output / "result.json").is_file()
    assert not (output / "review-queue").exists()


def test_parser_experiment_snapshot_uses_packaged_worker(tmp_path):
    snapshot = tmp_path / "frozen"
    hashes = freeze(snapshot)
    assert "src/doc_understanding/services/documents.py" in hashes
    assert "src/doc_understanding/agents/workflows/table_review/graph.py" in hashes
    assert not (snapshot / "demo").exists()
    assert not (snapshot / ".env").exists()
    assert not (snapshot / "work").exists()


def test_all_browser_assets_remain_available_without_starting_queues():
    from doc_understanding.web import app

    # 화면별 폴더(예: search/의 css·js 모듈)를 포함해, 정적 폴더에는 화면 파일만 둔다.
    for path in app.HERE.rglob("*"):
        assert path.is_dir() or path.suffix in (".html", ".js", ".css"), path
    # include_router로 연결한 라우터(예: RAG 화면 자산)도 펼쳐서 같은 검사를 적용한다.
    def flatten(routes):
        for route in routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                yield from flatten(nested.routes)
            else:
                yield route

    checked = set()
    for route in flatten(app.app.routes):
        if route.path.endswith((".js", ".css")):
            response = route.endpoint()
            assert Path(response.path).is_file(), route.path
            checked.add(route.path)
    assert {"/rag.js", "/rag.css"} <= checked
    assert "__TOKEN__" not in app.home().body.decode()
    assert "__TOKEN__" not in app.monitor().body.decode()
