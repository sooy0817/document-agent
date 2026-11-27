import copy
import json
from pathlib import Path

import pytest

from doc_understanding.agents.workflows.semantic_campaign.inventory import profile, select, file_hash
from doc_understanding.agents.workflows.semantic_campaign.report import record_judgments


def raw():
    cells = [
        dict(
            cell_id=str(i),
            row_index=i // 2,
            column_index=i % 2,
            rowspan=1,
            colspan=1,
            original_text="label",
            status="observed",
        )
        for i in range(4)
    ]
    return dict(
        document_ir={"source_sha256": "sha"},
        table_structure=[],
        physical_tables=[
            dict(
                table_id="T",
                page_number=1,
                bounding_box=dict(x0=0, y0=0, x1=100, y1=100),
                rows=[0, 1],
                columns=[0, 1],
                cells=cells,
            )
        ],
    )


def test_profile_preserves_sources_and_is_only_a_signal():
    doc = raw()
    before = copy.deepcopy(doc)
    p = profile(doc, "job")[0]
    assert p["tags"] == ["simple"] and not p["semantic_verified"]
    assert doc == before


def test_unknown_grid_is_triaged_not_selected_as_semantic_success():
    doc = raw()
    doc["physical_tables"][0]["cells"][0]["rowspan"] = None
    p = profile(doc, "job")
    selection = select(p)
    assert not selection["selected"] and selection["physical_triage"]


def test_shape_deduplication_does_not_claim_verified_family_or_holdout():
    p = profile(raw(), "job")[0]
    other = {**p, "table_id": "OTHER", "document_sha256": "another"}
    result = select([p, other], per_type=3)
    assert len(result["selected"]) == 1 and result["gaps"]["simple"] == 2
    assert not result["family_verified"] and not result["holdout_evaluated"]


def test_multiple_tags_and_nested_evidence():
    doc = raw()
    child = copy.deepcopy(doc["physical_tables"][0])
    child["table_id"] = "CHILD"
    child["bounding_box"] = dict(x0=10, y0=10, x1=90, y1=90)
    doc["physical_tables"].append(child)
    assert all("nested" in p["tags"] for p in profile(doc, "job"))


def test_human_judgment_bound_to_candidate_no_promotion(tmp_path):
    (tmp_path / "spec.json").write_text('{"campaign_id":"run"}')
    (tmp_path / "candidates.json").write_text('{"tables":[{"profile":{"table_id":"T"}}]}')
    payload = {
        "campaign_id": "run",
        "candidate_sha256": file_hash(tmp_path / "candidates.json"),
        "judgments": [{"table_id": "T", "verdict": "correct"}],
    }
    result = record_judgments(tmp_path, payload)
    assert not result["reference_confirmed"] and not result["parser_approved"]
    payload["candidate_sha256"] = "changed"
    with pytest.raises(ValueError, match="CANDIDATE_CHANGED"):
        record_judgments(tmp_path, payload)
    assert len(list((tmp_path / "human").glob("*.json"))) == 1


def test_duplicate_judgment_rejected(tmp_path):
    (tmp_path / "spec.json").write_text('{"campaign_id":"run"}')
    (tmp_path / "candidates.json").write_text('{"tables":[{"profile":{"table_id":"T"}}]}')
    row = {"table_id": "T", "verdict": "correct"}
    with pytest.raises(ValueError, match="INVALID_JUDGMENT"):
        record_judgments(
            tmp_path,
            {
                "campaign_id": "run",
                "candidate_sha256": file_hash(tmp_path / "candidates.json"),
                "judgments": [row, row],
            },
        )


def test_real_kb_profile_keeps_nested_and_variable_columns():
    path = Path("work/document-explorer/331a24bbcd404065b06605ee777fc3b2/document.json")
    if not path.exists():
        pytest.skip("local KB evidence")
    before = file_hash(path)
    profiles = profile(json.loads(path.read_text(encoding="utf8")), path.parent.name)
    p = next(p for p in profiles if p["table_id"].endswith("p0004/physical001"))
    assert {"nested", "row_groups", "variable_columns"} <= set(p["tags"])
    assert not p["physical_issues"] and file_hash(path) == before


@pytest.fixture
def pinned_project(tmp_path):
    import hashlib
    import zipfile

    project = tmp_path / "project"
    project.mkdir()
    manifest = project / "reports/parser-freeze-20261105/manifest.json"
    manifest.parent.mkdir(parents=True)
    archive = manifest.with_name('baseline.zip')
    with zipfile.ZipFile(archive, 'w'):
        pass
    manifest.write_text(json.dumps({
        'protected_hashes': {}, 'artifact_hashes': {},
        'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
    }))
    data = project / "data"
    job = data / ("a" * 32)
    job.mkdir(parents=True)
    pdf = b"%PDF-test"
    sha = hashlib.sha256(pdf).hexdigest()
    (job / "input.pdf").write_bytes(pdf)
    (job / "document.json").write_text(json.dumps({"document_ir": {"source_sha256": sha}}))
    for name in ("nested.json", "result.json"):
        (job / name).write_text("{}")
    return project, data, job, sha


def test_input_changes_detected_before_reuse(pinned_project):
    from doc_understanding.agents.workflows.semantic_campaign.service import pin_job, verify_job

    _, _, job, _ = pinned_project
    pin = pin_job(job)
    (job / "result.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="PINNED_JOB_CHANGED"):
        verify_job(pin)


def test_registered_holdout_is_not_opened_as_development(pinned_project):
    import sqlite3
    from doc_understanding.agents.workflows.semantic_campaign.service import create

    project, data, job, sha = pinned_project
    work = project / "work"
    work.mkdir()
    with sqlite3.connect(work / "campaigns.sqlite3") as db:
        db.execute("CREATE TABLE samples(sha256 TEXT, role TEXT)")
        db.execute("INSERT INTO samples VALUES (?, ?)", (sha, "holdout"))
    with pytest.raises(ValueError, match="PROTECTED_DATASET_SOURCE"):
        create(project, data, work / "out", [job.name])


def test_duplicate_source_runs_and_path_escape_are_rejected(pinned_project):
    import shutil
    from doc_understanding.agents.workflows.semantic_campaign.service import create

    project, data, job, _ = pinned_project
    other = data / ("b" * 32)
    shutil.copytree(job, other)
    with pytest.raises(ValueError, match="DUPLICATE_SOURCE"):
        create(project, data, project / "out", [job.name, other.name])
    with pytest.raises(ValueError, match="INVALID_JOB_ID"):
        create(project, data, project / "out", ["../escape"])


def test_source_code_change_blocks_resume_before_dispatch(pinned_project):
    from doc_understanding.agents.workflows.semantic_campaign.service import create, run

    project, data, job, _ = pinned_project
    out = project / "out"
    cid = create(project, data, out, [job.name])
    src = project / "src/doc_understanding"
    src.mkdir(parents=True)
    (src / "changed.py").write_text("VERSION = 2")
    with pytest.raises(ValueError, match="CODE_CHANGED_NEW_CAMPAIGN_REQUIRED"):
        run(out, cid)
    assert not (out / cid / "state.json").exists()


def test_real_cycle_resume_reuses_artifacts_and_rejects_tampering(tmp_path):
    from doc_understanding.agents.workflows.semantic_campaign.service import create, run, read

    project = Path.cwd()
    data = project / "work/document-explorer"
    job = "331a24bbcd404065b06605ee777fc3b2"
    if not (data / job / "document.json").exists():
        pytest.skip("local KB evidence")
    original = file_hash(data / job / "document.json")
    cid = create(project, data, tmp_path, [job], max_tables=1, max_llm_cells=0)
    first = run(tmp_path, cid)
    folder = tmp_path / cid
    before = {f.name: file_hash(f) for f in folder.glob("*.json") if f.name != "state.json"}
    assert run(tmp_path, cid) == first
    assert before == {f.name: file_hash(f) for f in folder.glob("*.json") if f.name != "state.json"}
    assert file_hash(data / job / "document.json") == original
    assert first["selected_tables"] == 1 and first["accuracy_score"] is None
    assert read(folder / "specialists.json")["items"] == []
    (folder / "candidates.json").write_text("{}", encoding="utf8")
    with pytest.raises(ValueError, match="STAGE_ARTIFACT_CHANGED"):
        run(tmp_path, cid)
