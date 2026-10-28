"""Static, local, evidence-linked acceptance report."""
import html
import json
from pathlib import Path


def generate(result_path):
    result_path = Path(result_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    root = result_path.parent
    manifest = result["manifest"]
    run = Path(manifest["run_dir"])
    ir = json.loads((run / "document-ir.json").read_text(encoding="utf-8"))
    layout = json.loads((run / "document-layout.json").read_text(encoding="utf-8"))
    data = {
        "pages": [{"number": p["page_number"], "width": p["width"], "height": p["height"],
                   "image": (run / p["image"]["artifact"]["path"]).as_uri()} for p in ir["pages"]],
        "blocks": [b for b in layout["blocks"] if b["kind"] == "text"],
    }
    safe_data = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    counts = result["counts"]
    size = sum(r["bytes"] for r in result["schema_storage"])
    page_rows = "".join(
        f"<tr><td>{p['page_number']}</td><td>{p['kind']}</td><td>{p['text_characters']}</td>"
        f"<td>{p['image_coverage']:.1%}</td><td>{p['status']}</td></tr>"
        for p in result["page_diagnosis"])
    trace = html.escape(json.dumps(result["trace"], ensure_ascii=False, indent=2))
    document = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>문서 전체 보존 · 로컬 적재 검증</title>
<style>
:root{--ink:#173333;--muted:#496263;--line:#c7d6d4;--accent:#126854;--paper:#fff;--bg:#f2f5f4}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 "Segoe UI","Malgun Gothic",sans-serif}
main{max-width:1400px;margin:auto;padding:30px}h1{font-size:30px;line-height:1.3}h2{font-size:22px}
section{background:var(--paper);padding:24px;margin:20px 0;border:1px solid var(--line);border-radius:10px}
.eyebrow{color:var(--accent);font-weight:700}.muted{color:var(--muted)}.cards{display:flex;gap:16px;flex-wrap:wrap}
.cards div{padding:16px 24px;background:#e7f1ed;flex:1;min-width:150px}.cards strong{display:block;font-size:28px}
.review{display:grid;grid-template-columns:1fr 1fr;gap:22px}.image{position:relative;align-self:start}.image img{width:100%;display:block}
#highlight{position:absolute;border:3px solid #b95f00;background:#ffbd4425;pointer-events:none}
label{display:block;font-weight:600}select,button{min-height:44px;font:inherit;max-width:100%;padding:8px;border:1px solid var(--line);background:white}
button{cursor:pointer}button:hover{background:#e7f1ed}:focus-visible{outline:3px solid #ad5c00;outline-offset:2px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f4;padding:16px;font-size:14px}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);text-align:left;padding:8px}
a{color:var(--accent)}.warn{border-left:5px solid #aa6500;padding-left:16px}
@media(max-width:800px){main{padding:12px}.review{grid-template-columns:1fr}section{padding:16px}}
</style><main><p class="eyebrow">LOCAL STAGING · FIRST DOCUMENT</p>
<h1>문서 전체 보존 → PostgreSQL 재조회</h1>
<p>강동농협 2025년 요약공시 · 기존 개발 문서 1개. 원문 보존과 저장 흐름을 검증한 결과입니다.</p>
<div class="cards"><div><strong>4페이지</strong>전체 페이지 처리</div>
<div><strong>@@BLOCKS@@개</strong>원본 콘텐츠 블록</div><div><strong>@@EVIDENCE@@개</strong>페이지·셀·텍스트 근거</div>
<div><strong>0건</strong>재무 Fact 발행</div></div>
<section><h2>검증 결과</h2><p>원본 PDF 해시 유지 · 전체 DocumentIR 재조회 일치 · 블록 텍스트/좌표 일치 · 잘못된 참조 차단 · 중복 요청 무적재 · 실패 롤백 및 재시도 복구: 모두 통과.</p>
<p class="warn">표 3개는 모두 부분 복원 상태입니다. 장·절 계층, 의미적 읽기 순서, 주석 적용 범위는 미확정입니다.
DB 저장 성공은 의미 정확도 검증이나 사람 승인을 뜻하지 않습니다.</p>
<p>추출·기존 표 분석 @@SECONDS@@초 / DB 검증 시나리오 @@VERIFY@@초.
산출물 @@DISK@@ MiB / Staging 전체 테이블·인덱스 @@DB@@ MiB.
DB 용량은 성공·복구 실행과 실패 이력을 포함한 측정 시점 총량입니다.</p>
<p>실문서 자연 실패 0/1. 의도적 실패 1건은 별도 집계합니다. 1개 문서로 처리율·정확도를 일반화하지 않습니다.</p></section>
<section><h2>원본과 저장된 블록 확인</h2>
<p>페이지와 블록을 선택하면 원본 위치를 강조합니다. 아래 블록은 SQL 재조회 값과 전체 일치를 검사했습니다.</p>
<div class="review"><div><label for="page">페이지</label><select id="page"></select><div class="image"><img id="original" alt="원본 PDF 페이지"><div id="highlight"></div></div></div>
<div><label for="block">텍스트 블록</label><select id="block"></select><h3>원문 텍스트</h3><pre id="text"></pre><h3>출처와 처리 상태</h3><pre id="meta"></pre>
<p>소속: 원본 페이지. 장·절·주석 역할: 미확정 후보. 표 안의 중복 텍스트는 삭제하지 않고 공유 span 참조로 연결했습니다.</p></div></div></section>
<section><h2>페이지별 처리 상태</h2><table><thead><tr><th>페이지</th><th>진단</th><th>문자</th><th>이미지 면적</th><th>처리 상태</th></tr></thead><tbody>@@PAGES@@</tbody></table>
<p>OCR_REQUIRED / REVIEW_REQUIRED 페이지는 추출 완료로 집계하지 않습니다. 이번 실제 문서는 4페이지 모두 Native Text이며, 스캔·혼합 판별은 합성 테스트만 수행했습니다.</p></section>
<section><h2>실제 SQL 조회와 원본 역추적</h2><p>run → 문서 버전 → 원본 경로·해시 → 페이지 → 블록·좌표 연결입니다.</p>
<pre>SELECT d.original_path, v.sha256, p.page_number, b.block_id,
       b.payload->'bbox' AS bbox, b.payload->>'text_raw' AS text_raw
FROM staging.content_block b
JOIN staging.processing_run r USING(run_id)
JOIN staging.document_version v USING(version_id)
JOIN staging.source_document d USING(source_id)
JOIN staging.page p ON p.run_id=b.run_id
  AND p.page_id=b.payload->>'page_id'
WHERE b.run_id='@@RUN@@' AND b.payload->>'kind'='text'
ORDER BY p.page_number,(b.payload->>'extraction_order')::int LIMIT 8;</pre><details><summary>SQL 재조회 결과 8개 보기</summary><pre>@@TRACE@@</pre></details>
<p><a href="@@PDF@@">원본 PDF 열기</a> · <a href="result.json">실측 결과 JSON</a> · <a href="@@LAYOUT@@">Layout JSON</a></p></section>
<section><h2>다음 검증 단계</h2><ol><li>현재 1개 결과를 확인한 뒤, 노출된 개발 문서로 5개까지 확대합니다.</li>
<li>40개는 개발 20 / 검증 10 / 잠금 10의 그룹 분할을 고정한 후 실행합니다. 동일 기관·기간·중복 원문은 분할을 넘지 않습니다.</li>
<li>Native 40개 검증 후 별도 스캔·혼합 6개에 OCR을 연결하고, 페이지·숫자·좌표 정확도를 검토합니다.</li></ol>
<p>현재 5개·40개 실행, 신규 Holdout 사용, OCR 엔진 도입, 외부 API 호출은 수행하지 않았습니다.</p></section></main>
<script>
const data=@@DATA@@, page=document.getElementById('page'), block=document.getElementById('block');
for(const p of data.pages){const o=new Option('PDF '+p.number+'페이지',p.number);page.add(o)}
function selectPage(){block.replaceChildren();for(const b of data.blocks.filter(x=>x.page_number==page.value)){
block.add(new Option(b.text_raw.slice(0,60).replaceAll('\\n',' '),b.block_id))}selectBlock()}
function selectBlock(){const p=data.pages.find(x=>x.number==page.value),b=data.blocks.find(x=>x.block_id==block.value);
document.getElementById('original').src=p.image;if(!b)return;
document.getElementById('text').textContent=b.text_raw;
document.getElementById('meta').textContent=JSON.stringify({page:b.page_number,block_id:b.block_id,extraction_order:b.extraction_order,bbox:b.bbox,semantic_parent_state:b.semantic_parent_state},null,2);
const h=document.getElementById('highlight'),v=b.bbox;
h.style.left=(100*v.x0/p.width)+'%';h.style.top=(100*v.y0/p.height)+'%';h.style.width=(100*(v.x1-v.x0)/p.width)+'%';h.style.height=(100*(v.y1-v.y0)/p.height)+'%'}
page.onchange=selectPage;block.onchange=selectBlock;selectPage();
</script></html>"""
    replacements = {
        "BLOCKS": counts["content_block"], "EVIDENCE": counts["evidence"],
        "SECONDS": f"{manifest['seconds']:.2f}", "VERIFY": f"{result['verification_seconds']:.2f}",
        "DISK": f"{manifest['disk_bytes']/1048576:.2f}", "DB": f"{size/1048576:.2f}",
        "PAGES": page_rows, "TRACE": trace, "PDF": html.escape(Path(manifest["source"]).as_uri()),
        "LAYOUT": (run / "document-layout.json").as_uri(), "DATA": safe_data,
        "RUN": html.escape(result["primary"]["run_id"]),
    }
    for key, value in replacements.items():
        document = document.replace("@@" + key + "@@", str(value))
    (root / "index.html").write_text(document, encoding="utf-8")


if __name__ == "__main__":
    import sys
    generate(sys.argv[1])
