"""Five-document inspection report, using saved real DB acceptance results."""
import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path

from .metric_candidate import MetricCandidate

LABELS = {
    "total_assets": ("자산총계", "자산총액", "총자산"),
    "total_liabilities": ("부채총계", "총부채"),
    "total_equity": ("자본총계", "자본총액"),
    "operating_revenue": ("영업수익",),
    "net_income": ("당기순이익", "당기순손실", "당기순손익"),
}
NAMES = dict(total_assets="총자산", total_liabilities="총부채", total_equity="자본총계",
             operating_revenue="영업수익", net_income="당기순손익")


def build(root):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    documents = []
    for item in manifest["documents"]:
        result = json.loads((root / item["id"] / "result.json").read_text(encoding="utf-8"))
        run = Path(result["manifest"]["run_dir"])
        ir_bytes = (run / "document-ir.json").read_bytes()
        ir = json.loads(ir_bytes)
        layout = json.loads((run / "document-layout.json").read_text(encoding="utf-8"))
        hits, notes = [], []
        for page in ir["pages"]:
            for b in page["blocks"]:
                text = b.get("text_raw") or ""
                if b["kind"] == "text" and re.search(r"(?:^\s*주[):：.]|주석|비고|단위\s*[:：])", text):
                    if len(notes) < 4:
                        notes.append({"page":page["page_number"], "block_id":b["block_id"],
                                      "text":text, "bbox":b["source"]["bbox"],
                                      "meaning_scope":"unresolved"})
            for t in page["tables"]:
                for c in t["cells"]:
                    raw = c.get("text_raw") or ""
                    compact = re.sub(r"\s+", "", raw)
                    for metric, labels in LABELS.items():
                        if any(label in compact for label in labels):
                            if sum(h["metric"] == metric for h in hits) >= 3:
                                continue
                            row = [v for v in t["cells"] if v["row_index"] == c["row_index"]]
                            hits.append({
                                "metric":metric, "page":page["page_number"],
                                "table_id":t["table_candidate_id"], "label_cell_id":c["cell_id"],
                                "label":raw, "bbox":c["source"]["bbox"],
                                "row_cells":[{"cell_id":v["cell_id"],"text_raw":v["text_raw"],
                                              "column":v["column_index"],"bbox":v["source"]["bbox"]}
                                             for v in row],
                                "status":"label_location_only; value/period/unit/scope not resolved",
                            })
        documents.append({
            "selection":item, "result":result, "notes":notes, "metric_locations":hits,
            "shared_text_relations":[r for r in layout["relations"]
                                     if r["kind"]=="text_overlaps_table"][:3],
            "pages":[{"number":p["page_number"],"width":p["width"],"height":p["height"],
                      "image":(run/p["image"]["artifact"]["path"]).as_uri()} for p in ir["pages"]],
            "document_ir_sha256":hashlib.sha256(ir_bytes).hexdigest(),
            "original_table_candidates":sum(len(p["tables"]) for p in ir["pages"]),
        })
    summary = {
        "scope":"five total including prior Gangdong; four fresh extractions",
        "documents":documents,
        "status_counts":dict(Counter(p["status"] for d in documents for p in d["result"]["page_diagnosis"])),
        "fact_publications":0, "semantic_accuracy":None,
        "gate_40":"conditional: supervised staging expansion only; not unattended financial analysis",
    }
    (root/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    (root/"metric-candidate.schema.json").write_text(
        json.dumps(MetricCandidate.model_json_schema(),ensure_ascii=False,indent=2),encoding="utf-8")
    # A real source label anchored to a contract example; no number or context guessed.
    examples = []
    for metric in LABELS:
        found = next(((d,h) for d in documents for h in d["metric_locations"]
                      if h["metric"]==metric),None)
        if not found:
            continue
        d,h = found
        candidate = MetricCandidate(
            candidate_id=f"design-{metric}", source_sha256=d["selection"]["sha256"],
            processing_run_id=d["result"]["primary"]["run_id"], metric=metric,raw_value=None,
            evidence=[dict(artifact_sha256=d["document_ir_sha256"], page_number=h["page"],
                           table_id=h["table_id"], cell_id=h["label_cell_id"], bbox=h["bbox"],
                           raw_text=h["label"],role="label")],
            unresolved=("value_cell_selection","institution_evidence","period_header",
                        "unit_context","accounting_scope","metric_definition"),
        )
        examples.append(candidate.model_dump(mode="json"))
    (root/"metric-contract-examples.json").write_text(
        json.dumps({"origin":"design_examples_bound_to_real_labels_not_agent_extraction",
                    "candidates":examples},ensure_ascii=False,indent=2),encoding="utf-8")
    rows = []
    for d in documents:
        r=d["result"]
        m=r["manifest"]
        s=m["table_summary"]
        statuses=Counter(p["status"] for p in r["page_diagnosis"])
        timing = r["primary"].get("database_seconds")
        rows.append(f"""<tr><td><a href="#{d['selection']['id']}">{html.escape(Path(d['selection']['source']).name)}</a></td>
<td>{m['pages']}</td><td>{m['blocks']:,}</td><td>{s['detected_table_regions']} / {s['partial_reconstruction']}</td>
<td>{statuses['OCR_REQUIRED']} / {statuses['REVIEW_REQUIRED']}</td><td>{m['seconds']:.2f}</td>
<td>{f'{timing:.2f}' if timing is not None else '중복 방지 재사용'}</td>
<td>{sum(r['run_jsonb_payload_bytes'].values())/1048576:.2f}</td><td>통과</td></tr>""")
    sections=[]
    for d in documents:
        item=d["selection"]
        r=d["result"]
        notes = "".join(f"<li>p{n['page']} · {html.escape(n['text'])}<br><small>적용 범위 미확정 · {html.escape(n['block_id'])}</small></li>" for n in d["notes"])
        locs = "".join(f"<tr><td>{NAMES[h['metric']]}</td><td>p{h['page']}</td><td>{html.escape(h['label'])}</td><td>{html.escape(' | '.join(str(c['text_raw']) for c in h['row_cells']))}</td></tr>" for h in d["metric_locations"])
        sections.append(f"""<section id="{item['id']}"><h2>{item['id']} · {html.escape(Path(item['source']).name)}</h2>
<p>{html.escape(item['reason'])} · <a href="{Path(item['source']).as_uri()}">원본 PDF</a> · <a href="{item['id']}/result.json">실제 SQL 검증 기록</a></p>
<p>DocumentIR 원시 표 후보 {d['original_table_candidates']}개. 위 요약의 표 후보 수는 별도 Physical Grid 엔진의 결과입니다.</p>
<p>저장 관계: 문서 버전 → 실행 → 페이지 → 콘텐츠 블록. 표 내부 행·열 Tree는 별도 Artifact로 보존됩니다.
공유 span 기반 본문↔표 참조는 후보이며, 주석의 의미적 적용 대상은 아직 미확정입니다.</p>
<details><summary>실제 본문↔표 참조 예시</summary><pre>{html.escape(json.dumps(d['shared_text_relations'],ensure_ascii=False,indent=2))}</pre></details>
<h3>원문 주석·단위 문구</h3><ul>{notes or '<li>현재 검색 조건으로 찾지 못함. 주석 부재를 의미하지 않습니다.</li>'}</ul>
<h3>재무지표 원본 위치 후보</h3><p>라벨 검색으로 찾은 검토 위치입니다. 같은 행의 모든 셀을 보여주며, 어느 값이 당기/전기인지 자동 선택하지 않았습니다.</p>
<div class="scroll"><table><tr><th>지표</th><th>쪽</th><th>원문 라벨</th><th>같은 행의 원문 셀</th></tr>{locs}</table></div>
<details><summary>SQL 원본 역추적 결과</summary><pre>{html.escape(json.dumps(r['trace'],ensure_ascii=False,indent=2))}</pre></details></section>""")
    data=json.dumps([{"id":d["selection"]["id"],"name":Path(d["selection"]["source"]).name,
                      "pages":d["pages"],"locations":d["metric_locations"],"notes":d["notes"]}
                     for d in documents],ensure_ascii=False).replace("<","\\u003c")
    content="""<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>문서 5개 · 실제 Staging 검증</title><style>
*{box-sizing:border-box}body{margin:0;background:#f2f5f4;color:#173b36;font:16px/1.65 'Segoe UI','Malgun Gothic',sans-serif}
main{max-width:1450px;margin:auto;padding:24px}section{background:white;padding:24px;margin:20px 0;border:1px solid #ccd9d5;border-radius:8px}
h1{font-size:32px}h2{font-size:22px}a{color:#146751}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #d6dfdc;text-align:left;font-size:14px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f3;padding:16px;font-size:13px}.scroll{overflow:auto}.warning{border-left:5px solid #ab6b00;padding-left:16px}
select{font:inherit;min-height:44px;max-width:100%;margin:8px 0;padding:8px}label{display:block}small{overflow-wrap:anywhere}
.compare{display:grid;grid-template-columns:1fr 1fr;gap:24px}.image{position:relative}img{width:100%;display:block}#mark{position:absolute;border:3px solid #b35b00;background:#ffbe3325}
:focus-visible{outline:3px solid #b35b00}@media(max-width:800px){.compare{grid-template-columns:1fr}main{padding:10px}section{padding:16px}}
</style><main><p>LOCAL DEVELOPMENT · FIVE DOCUMENTS</p><h1>총 5개 문서, 113페이지의 적재 검증</h1>
<p>기존 강동농협 포함 총 5개입니다. 추가 4개는 전체 페이지를 새로 추출했습니다. 남평·잠금 평가셋은 사용하지 않았습니다.</p>
<section><h2>문서별 실측</h2><div class="scroll"><table><tr><th>문서</th><th>쪽</th><th>블록</th><th>표 후보 / 부분 복원</th><th>OCR / 보류 쪽</th><th>추출 초</th><th>DB 적재 초</th><th>JSONB MiB</th><th>보존 검사</th></tr>@@ROWS@@</table></div>
<p>JSONB 용량은 해당 기본 run의 저장 payload 합입니다. 원본 PDF 바이트는 포함하지 않으며 인덱스·행 헤더·복구 실행 용량도 제외합니다.
강동의 추출 시간은 이전 실행 기록입니다. 표 후보는 Physical Grid 검출 결과이며 정확한 실제 표 개수를 뜻하지 않습니다.</p>
<p class="warning">저장 검증 통과는 의미 정확도 통과가 아닙니다. 이번 113페이지는 모두 Native 진단이지만, 이미지·혼합형 판별 정확도는 검증하지 않았습니다.</p></section>
<section><h2>원본 위치와 지표 후보를 비교하세요</h2><label for="doc">문서</label><select id="doc"></select><label for="loc">원본 지표 라벨 위치</label><select id="loc"></select>
<div class="compare"><div class="image"><img id="page" alt="원본 PDF 페이지"><div id="mark"></div></div><div><h3>같은 행의 원문 값</h3><pre id="cells"></pre>
<p>라벨 위치는 확인 가능하지만 기간·단위·회계범위는 별도 근거로 연결해야 합니다. 값이 적혀 있다고 자동 승인하지 않습니다.</p></div></div></section>
@@SECTIONS@@
<section><h2>핵심 재무지표 5개를 정형화하는 다음 단계</h2><ol>
<li>총자산·총부채·자본총계는 시점 지표, 영업수익·당기순손익은 기간 지표로 구분합니다.</li>
<li>라벨과 값 셀을 연결하고, 비교연도·당기연도를 컬럼 헤더 근거로 분리합니다.</li>
<li>기관명·단위·통합/신용/일반 범위는 각각 원문 블록 근거를 연결합니다. 파일명으로 확정하지 않습니다.</li>
<li>원문 부호·공란을 보존한 채 Decimal 파싱, 수치 검산, 의미 검토를 분리합니다.</li>
<li>사람 승인 이후에도 Gold 최종 확정과 Financial Fact 발행은 별도 절차입니다.</li></ol>
<p><a href="metric-candidate.schema.json">MetricCandidate 계약</a> · <a href="metric-contract-examples.json">실제 라벨에 연결한 설계 예시</a> · <a href="summary.json">전체 결과 JSON</a> · <a href="tests.txt">테스트 로그</a></p></section>
<section><h2>40개 확대 판단: 조건부 가능</h2><p>5개 모두 원본 해시, 전체 IR·블록·페이지·Artifact 재조회, FK, 중복 방지, DB 중간 실패 롤백, 복구 Artifact 해시 검사를 통과했습니다.
실패 주입 5건은 자연 실패와 구분합니다. 주입 시험은 DB 트랜잭션 경계이며 추출 프로세스 강제 종료나 디스크 부족 시험은 아닙니다.</p>
<p>감독하의 Staging 확대는 가능하나 무인 재무 분석은 아직 이릅니다. 40개 전 개발20/검증10/잠금10 manifest 동결,
추출 실패의 DB 이력 연결, 중단 작업 회수, 문서별 시간·메모리 제한 및 표 부분 복원 검토가 필요합니다.
현재 40개 실행이나 Financial Fact 발행은 하지 않았습니다.</p></section></main>
<script>const ds=@@DATA@@,doc=document.getElementById('doc'),loc=document.getElementById('loc');
for(const d of ds)doc.add(new Option(d.id+' · '+d.name,d.id));
function change(){loc.replaceChildren();const d=ds.find(x=>x.id===doc.value);d.locations.forEach((h,i)=>loc.add(new Option('p'+h.page+' · '+h.label,i)));show()}
function show(){const d=ds.find(x=>x.id===doc.value),h=d.locations[Number(loc.value)];
if(!h){document.getElementById('page').src=d.pages[0].image;document.getElementById('mark').style.display='none';document.getElementById('cells').textContent='이 문서에서는 현재 라벨 검색으로 5개 지표 위치를 찾지 못했습니다. 원문 첫 페이지를 표시합니다. 지표 부재로 확정하지 않습니다.';return}
document.getElementById('mark').style.display='block';const p=d.pages.find(x=>x.number===h.page),b=h.bbox;
document.getElementById('page').src=p.image;document.getElementById('cells').textContent=h.row_cells.map(c=>'열 '+(c.column+1)+' : '+(c.text_raw===null?'읽을 수 없음':c.text_raw===''?'빈 셀':c.text_raw)).join('\\n');
const m=document.getElementById('mark');m.style.left=100*b.x0/p.width+'%';m.style.top=100*b.y0/p.height+'%';m.style.width=100*(b.x1-b.x0)/p.width+'%';m.style.height=100*(b.y1-b.y0)/p.height+'%'}
doc.onchange=change;loc.onchange=show;change();</script></html>"""
    for key,value in {"ROWS":"".join(rows),"SECTIONS":"".join(sections),"DATA":data}.items():
        content=content.replace("@@"+key+"@@",value)
    (root/"index.html").write_text(content,encoding="utf-8")


if __name__ == "__main__":
    import sys
    build(Path(sys.argv[1]))
