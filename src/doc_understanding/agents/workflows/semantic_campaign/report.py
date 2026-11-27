"""Reviewable reports and immutable, candidate-bound human judgments."""

import html
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .inventory import file_hash


def read(p):
    return json.loads(Path(p).read_text(encoding="utf8"))


def render(folder):
    candidates = read(folder / "candidates.json")
    summary = read(folder / "validation.json")
    selection = read(folder / "selection.json")

    def esc(x):
        return html.escape(str(x), quote=True)

    rows = []
    for n, item in enumerate(candidates["tables"]):
        p = item["profile"]
        counts = {
            k: sum(r["route"] == k for r in item["routes"]) for k in ("rule", "llm", "blocked")
        }
        rows.append(
            f"<tr><td>{n + 1}</td><td>{esc(p['job_id'][:8])} · {p['page']}쪽</td>"
            f"<td>{esc(', '.join(p['tags']))}</td><td>규칙 {counts['rule']} / LLM 필요 {counts['llm']} / 보류 {counts['blocked']}</td>"
            f'<td><a href="{esc(item["report"])}">원문·기존·후보 비교</a></td></tr>'
        )
    payload = {
        "campaign_id": read(folder / "spec.json")["campaign_id"],
        "candidate_sha256": file_hash(folder / "candidates.json"),
        "judgments": [
            {"table_id": t["profile"]["table_id"], "verdict": "uncertain", "note": ""}
            for t in candidates["tables"]
        ],
    }
    (folder / "judgment-template.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf8"
    )
    page = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><title>표 구조 다양성 캠페인</title>
<style>body{{font:16px/1.6 system-ui;max-width:1200px;margin:40px auto;padding:20px;color:#203b33;background:#f8faf9}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #cedbd5;padding:12px;text-align:left}}.notice{{padding:18px;background:#fff3d7}}a{{color:#096954}}pre{{white-space:pre-wrap}}</style>
<h1>표 구조 다양성 · 검토 대기</h1><p class="notice">물리 파서와 기존 결과를 보존했습니다. 구조 유형과 의미 경로는 검토 후보입니다.
LLM 미완료 항목도 포함합니다. 정답 참조 미확정으로 정확도 점수는 아직 없습니다.</p>
<p>문서 {read(folder / "inventory.json")["documents"]}개 · 선정 표 {summary["selected_tables"]}개 · 셀 {summary["selected_cells"]}개</p>
<p>규칙 후보 {summary["rule_candidates"]}개 · LLM 필요 {summary["llm_needed"]}개 · LLM 완료 {summary["llm_completed"]}개 · 보류 {summary["blocked_cells"]}개</p>
<p>유형별 부족 표본: {esc(selection["gaps"])}. 동일 양식 판정은 미확정이며 미사용 자료 성능을 주장하지 않습니다.</p>
<table><thead><tr><th>번호</th><th>문서·쪽</th><th>구조 후보</th><th>검토 경로</th><th>근거</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
<h2>실행 기록</h2><p><a href="collection.json">외부 수집 결과</a> · <a href="specialists.json">실제 전문 검토 결과</a> · <a href="validation.json">검사 결과</a></p>
<h2>사람 검토</h2><p>각 표의 원문과 후보를 확인하고 판단을 기록합니다. 현재 버전은 별도 JSON 가져오기를 지원하며 기존 모니터의 승인 버튼과는 연결되지 않았습니다.</p>
<p><a href="judgment-template.json" download>판단 양식 다운로드</a>: 검토한 표만 남기고 verdict를 correct / incorrect / uncertain으로 작성합니다. correct도 규칙 코드 승격이나 금융 사실 승인이 아닙니다.</p>
</html>"""
    (folder / "index.html").write_text(page, encoding="utf8")


def record_judgments(folder, payload):
    """Record explicit human input; never fabricate a reference or promote code."""
    from .service import read as load
    from doc_understanding.agents.workflows.campaign.locking import campaign_lock

    folder = Path(folder)
    with campaign_lock(folder / "run.lock"):
        if payload.get("campaign_id") != load(folder / "spec.json")["campaign_id"]:
            raise ValueError("CAMPAIGN_MISMATCH")
        if payload.get("candidate_sha256") != file_hash(folder / "candidates.json"):
            raise ValueError("CANDIDATE_CHANGED")
        ids = {t["profile"]["table_id"] for t in load(folder / "candidates.json")["tables"]}
        judgments = payload.get("judgments", [])
        seen = set()
        if not judgments:
            raise ValueError("EMPTY_JUDGMENTS")
        for j in judgments:
            if (
                j.get("table_id") not in ids
                or j["table_id"] in seen
                or j.get("verdict") not in ("correct", "incorrect", "uncertain")
                or not isinstance(j.get("note", ""), str)
                or len(j.get("note", "")) > 2000
            ):
                raise ValueError("INVALID_JUDGMENT")
            seen.add(j["table_id"])
        path = folder / "human"
        path.mkdir(exist_ok=True)
        event = {
            **payload,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "reference_confirmed": False,
            "parser_approved": False,
        }
        with (path / (uuid.uuid4().hex + ".json")).open("x", encoding="utf8") as f:
            json.dump(event, f, ensure_ascii=False, indent=2)
        return {
            "recorded_tables": len(seen),
            "reference_confirmed": False,
            "parser_approved": False,
        }
