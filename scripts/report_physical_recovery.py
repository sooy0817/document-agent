"""Render frozen experiment results. Reporting only; never runs or tunes a parser."""
import html
import json
import os
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/physical-recovery'
OUT = ROOT / 'reports/physical-recovery'
ORDER = ['0', '1', '2', '3', '12', '13', '23', '123']


def read(p):
    return json.loads(Path(p).read_text(encoding='utf8'))


def esc(x):
    return html.escape(str(x))


def href(p):
    return esc(os.path.relpath(Path(p), OUT).replace('\\', '/'))


def link(p, label):
    return f'<a href="{href(p)}">{esc(label)}</a>'


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{esc(x)}</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{x}</td>' for x in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def figure(p, label):
    return f'<figure><a href="{href(p)}"><img loading="lazy" src="{href(p)}" alt="{esc(label)}"></a><figcaption>{esc(label)} · 클릭하여 원본 크기</figcaption></figure>'


NOTES = {
    'D01': ('PNEUMA p.13 Table 1', '1', 'S1은 12pt 미만이어서 탈락했던 세로 조각 56개를 복구했다. 7×5, 35셀의 값·좌표·행·열·span이 원본 참조와 일치한다. 그러나 header=1/stub=1 모호성 때문에 HEADER_REGION_AMBIGUOUS와 unresolved Mapping은 남는다. S3은 단위가 붙은 열을 반복 숫자 열로 인식하지 못해 4열로 줄인다.'),
    'D02': ('PNEUMA p.19 Table 2', '1', 'S1이 94개 짧은 세로 조각을 보존하고 tolerance를 2.1918pt로 조정하여 이중 가로선 사이의 빈 띠를 합친다. 12행·5열·51셀, 8개 병합 셀이 원본과 일치한다. 30개 수치의 Dataset 그룹/Content·Context 및 Retriever 하위 열 연결도 일치한다. LAYOUT_HYPOTHESIS는 유지되며 사람 검증 상태로 승격하지 않는다.'),
    'D03': ('HiTab p.1 Figure 1', '123', '표가 이미지에 들어 있고 native text/cell이 없다. 페이지에 vector 선이 1개 있어서 vector-empty 조건의 S2는 실행되지 않는다. S1/S3에도 사용할 셀 텍스트가 없어 복원되지 않는다. 이미지 영역별 raster 선택과 OCR은 이번 구현 범위 밖이다.'),
    'D04': ('HiTab p.6 Table 4', '3', 'S3이 만든 두 후보는 OVERLAPPING_COLUMN_HYPOTHESES로 거절된다. 페이지 전체 행 묶음에 옆 단의 본문 숫자와 논문 연도가 섞이고 열 extent가 겹친다. S2는 vector 3개가 있어 fill 선을 보완하지 않는다. 독립 표 영역 분리가 해결되지 않았다.'),
    'D05': ('BIS credit p.8 Table 1', '23', 'S2 조건 정합성 수정으로 실제 raster 선 16개를 준비·사용한다. 세로 격자가 없어 단독 S2는 여전히 셀을 만들지 못한다. S2+S3은 21행·6열·102셀을 제안하고 N/R-squared의 두 값은 맞지만, 추정치와 괄호 통계량을 별도 행으로 처리한다. 기대 논리 행 13개와 다르고 열 헤더가 1/공란으로 연결된다.'),
    'D06': ('TUTA p.7 Table 3', '3', 'S1은 기존 큰 구획만 복원하여 6×4가 된다. WebSheet의 하위 열, DeEx/SAUS/CIUS 분할과 헤더/첫 데이터 행 경계가 빠져 여러 숫자가 한 셀에 합쳐진다. S3은 9열과 선택 값은 복구하나 헤더의 시각적 텍스트 줄을 3행으로 나누어 9행(기대 8행)이 되고 열 경로가 공란이다. S1+S3은 먼저 생긴 coarse grid를 보호하여 S3 복원이 차단된다.'),
    'D07': ('TUTA p.7 Table 4', '3', 'S1은 6×2로 큰 구획만 복원한다. 87.6 자체는 남지만 행 라벨에 다른 다섯 값이 붙고 열 경로도 Macro-F1/79.0이 된다. S3은 7×7과 선택 값·행/열 라벨을 복구하지만 HEADER_REGION_AMBIGUOUS로 Mapping은 unresolved다. 전체 49셀의 독립 정답이 없으므로 완전 성공으로 표시하지 않는다.'),
    'D08': ('TableFormer p.7 Table 2', '3', 'S3은 13×4·52셀을 만든다(원본 논리 구조 12×5). 텍스트 열 경계가 Model과 PTN 표시를 합치고 다단 열 헤더를 보존하지 못한다. 선택 숫자는 맞아도 행 경로 TableFormer/PTN 병합과 공란 열 경로로 의미 연결은 실패한다.'),
    'D09': ('TableFormer p.7 Table 3', '3', '반복 숫자 열 2개가 필요하다는 S3 조건을 만족하지 못해 INSUFFICIENT_REPEATED_COLUMNS로 거절한다. 일부 비교 열의 sparse 값과 비숫자 표현을 열 후보로 인정하지 않는다. S1에 필요한 세로 조각도 없다.'),
    'D10': ('BIS inflation p.9 Table 1', '23', 'S2는 fill 기반 raster 선 32개를 준비하지만 단독 셀 복원은 실패한다. S3 단독은 표를 부분 영역 두 개로 나누어 42셀을 만들지만 전체 표 매칭 기준에 미달한다. S2+S3은 전체 영역을 찾은 뒤 OVERLAPPING_COLUMN_HYPOTHESES로 거절한다. 각주/괄호/혼합 숫자의 열 분리 문제이며 42셀을 표 복원 성공으로 세지 않는다.'),
    'H01': ('TAPAS p.7 Table 3', '3', '같은 x 범위의 가로선을 90pt 간격으로 연결하는 규칙이 위 표와 아래 Table 4 일부를 한 영역(y=63.24~308.16)으로 합친다. 라벨 속 인용 연도와 결과 숫자가 열 후보에 함께 들어가 OVERLAPPING_COLUMN_HYPOTHESES로 거절된다.'),
    'H02': ('TAPAS p.7 Table 4', '3', '첫 부분은 위 표와 합쳐지고 마지막 세 행만 별도 영역이 된다. 이 영역에는 Test 숫자 열 하나만 있어 INSUFFICIENT_REPEATED_COLUMNS로 거절된다. 전체 표 12행은 복원되지 않는다.'),
    'H03': ('TAPEX p.5 Table 1', '3', '헤더 아래 선과 하단 결과 구분선의 간격이 90pt를 넘으므로 가로선 후보가 갈라진다. 하단 BART/TAPEX 두 행은 INSUFFICIENT_TEXT_ROWS다. 별도 text-only 후보는 좌우 두 표를 합쳐 x=115.28~485.15가 되고 열 extent 충돌로 거절된다.'),
    'H04': ('TAPEX p.5 Table 2', '3', 'Table 1과 같은 분할/좌우 표 혼합 문제다. 그룹 제목과 두 숫자 열이 있는 표지만 후보 단계에서 거절되어 병합·부모·셀 Mapping까지 도달하지 못했다. 원문 en dash(–) probe도 숫자 0으로 대체하지 않고 실패로 남겼다.'),
}


def main():
    OUT.mkdir(exist_ok=True)
    dev, hold = [read(DATA / n / 'evaluation.json') for n in ['dev-v2', 'holdout-v1']]
    audit = read(DATA / 'audit.json')
    diag = read(DATA / 'pneuma-p19-diagnostic.json')
    manifests = {n: read(DATA / f'{n}.json')['documents'] for n in ['development', 'holdout']}
    refs = read(DATA / 'development-references.json')['cases'] + read(DATA / 'holdout-references.json')['cases']
    h = ['<!doctype html><html lang="ko"><meta charset="utf-8"><title>물리 복원 1차 실험 — 동결 비교</title><style>body{font:16px/1.65 system-ui,sans-serif;color:#192b3c;max-width:1440px;margin:auto;padding:32px}h1,h2,h3{line-height:1.3}h2{margin-top:48px;border-top:2px solid #d5dfe7;padding-top:20px}a{color:#0758a1}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px;border:1px solid #d9e0e5;text-align:left;vertical-align:top}th{background:#eef3f7}.scroll{overflow:auto}.images{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}figure{margin:0}img{width:100%;border:1px solid #ddd}figcaption{font-size:13px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f7;padding:16px;font-size:12px}.note{padding:18px;background:#fff5dd}details{margin:18px 0}summary{cursor:pointer;font-weight:600}code{overflow-wrap:anywhere}@media(max-width:800px){.images{grid-template-columns:1fr}body{padding:16px}}</style><body>']
    h += ['<h1>규칙 기반 물리 복원: 독립 전략과 조합 비교</h1><p>2026-10-23 · 실제 실행 결과 · LLM/VLM 사용 없음 · 기존 계약/운영 파이프라인 변경 없음</p>',
          '<p class="note"><strong>추천: S1을 제한된 물리 복원 후보로 먼저 사용하고 S2는 fallback 조건 수정으로 유지.</strong> S3 및 전체 조합의 자동 채택은 보류한다. PNEUMA에서는 개선됐지만 새 Holdout 4표는 모두 실패했다. 범용 복원 성능이 검증됐다는 결론은 내리지 않는다.</p>',
          '<h2>실험 범위와 분리</h2><ul><li>개발: 기존 실패 10사례는 6개 PDF의 10개 표/그림이다. 이전 Holdout이었던 TUTA·TableFormer·BIS inflation도 이미 열람했으므로 이번에는 development_reused로 분리했다.</li><li>새 Holdout: TAPAS와 TAPEX 2개 PDF. 구현·평가기 130개 파일을 먼저 동결한 뒤 다운로드/열람했다. PDF 읽기 순서의 첫 두 수치 결과표를 선택하여 총 4표, 12개 값/경로 probe를 확정한 후 실행했다.</li><li>농협 회귀: 강동 등 4문서, 12페이지/12표, 1,292셀. 전수 객체 비교는 회귀 보존 검사이며 독립 의미 정확도 평가가 아니다.</li><li>각 전략 8종: S0 기준, S1/S2/S3 단독, S12/S13/S23/S123 조합. 개발 48회, 농협 32회, Holdout 16회 = 문서×전략 96회.</li><li>참조는 원본 PDF 이미지와 텍스트/좌표를 대조한 에이전트 주석이다. 독립 사람 정답이 아니다. PNEUMA 2표만 86셀 전수 참조이며 나머지 표는 선택 probe 평가다.</li></ul>',
          table(['전략', '실제 구현', '후보 상태 / 한계'], [
              ['S1', 'font 크기의 0.25배(최소 2pt) 이상 세로 조각 중 같은 x에서 이웃 조각·텍스트 교차 검사를 통과한 선을 보존. font×0.22, 최대 2.5pt tolerance 적용.', '실제 조각만 보존하며 없는 연속 선을 만들지 않음. inferred / SHORT_FRAGMENT_RECOVERY_CANDIDATE. TUTA처럼 처음부터 없는 하위 구획은 복원 못 함.'],
              ['S2', 'vector 선이 0개이고 fill drawing이 존재할 때 raster 준비와 사용이 동일한 조건을 사용.', 'BIS 두 페이지에서 실제로 실행됨. 페이지에 무관한 vector가 하나라도 있으면 실행 안 됨. 선을 얻는 것과 셀을 복원하는 것은 별개.'],
              ['S3', 'native 표/가로선 묶음/반복 정렬로 영역 후보, 3행 이상에서 반복되는 숫자 오른쪽 끝으로 열 후보, 텍스트 교차로 병합 후보.', '숫자 열 2개 이상 조건. 모든 경계 inferred, evidence_level=missing, line_ids=[]; 관측 선으로 위장하지 않음. 영역·논리 행·부모 정확도 부족.']])]
    h += ['<p>구현: ' + ' · '.join(link(ROOT / p, label) for p, label in [
        ('src/doc_understanding/experiments/physical_recovery/recovery.py', 'S1/S2 및 조합'),
        ('src/doc_understanding/experiments/physical_recovery/text_grid.py', 'S3 영역·열 후보'),
        ('scripts/run_physical_recovery.py', '실험 실행'), ('scripts/evaluate_physical_recovery.py', '동결 평가기'),
        ('tests/test_physical_recovery.py', '추가 회귀 테스트'), ('docs/ADR/031-physical-recovery-experiment.md', 'ADR-031')]) + '</p>']
    for title, data in [('개발용 10표: 독립 성능과 조합 성능', dev), ('미사용 Holdout 4표: 구현 동결 후 평가', hold)]:
        h.append(f'<h2>{title}</h2>')
        rows=[]
        for s in ORDER:
            r=next(x for x in data['summary'] if x['strategy']==s); p=r['probes']; full=r['full_cells']
            rows.append([f'S{s}',f'{r["detected"]}/{r["tables"]}', f'{r["row_count_exact"]}/{r["row_count_denominator"]} · {r["column_count_exact"]}/{r["column_count_denominator"]}',
                f'{full["exact"]}/{full["denominator"]}' if full['denominator'] else '미평가', f'{p["cell_exact"]}/{p["denominator"]}',f'{p["row_path"]}/{p["denominator"]} · {p["column_path"]}/{p["denominator"]}',f'{p["safe_mapping"]}/{p["denominator"]}',f'{p["wrong_values"]} · {p["wrong_row_parents"]} · {p["wrong_column_parents"]}',str(r['false_boundary_segments']) if full['denominator'] else '미평가'])
        h.append(table(['전략','영역 매칭','행수·열수 일치','전수 참조 셀 일치','선택 값 일치','행·열 경로 일치','값+양쪽 경로+비unresolved','잘못된 값·행·열 연결','잘못된 경계 구간'],rows))
    h += ['<p>영역 매칭은 원본 영역 70% 이상과 후보 영역 50% 이상 교집합을 요구한다. 영역 검출이나 행/열 개수만 맞아도 성공으로 보지 않는다. 미검출은 분모에 남는다. 값+경로 지표는 <code>safe_mapping</code>이라는 집계 키를 쓰지만 <strong>승인된 사실</strong>을 뜻하지 않는다.</p>',
          '<p>잘못된 경계 구간은 PNEUMA 전수 86셀 내부를 가르는 선분만 센다. S3/S23은 43개이며 원본 셀과 좌표·문자열이 함께 맞지 않는 출력 셀은 74개다. 다른 8표와 Holdout은 전수 cell/merge 정답이 없어 이 수치를 0으로 해석하면 안 된다. 잘못된 부모 집계에는 비어 있지 않은 경로 객체 안의 공란 라벨도 포함된다.</p>',
          '<p>Holdout에서 S3가 만든 TAPAS 88셀은 평가 대상 Table 3/4가 아니라 Table 5/6의 셀이다. 평가 후 대상을 바꾸지 않았고 4표/12probe 모두 분모에 유지했다. 추가 88셀의 전수 정확도는 평가하지 않았다.</p>']
    h += ['<h2>PNEUMA p.19 단계별 검증</h2>',
          '<ol><li>기준: 표 영역은 있지만 13개 가로 band·0열·0셀. native 추출 셀은 원본 DocumentIR에 보존.</li><li>S1: 세로 조각 94개 보존 → 전체 선 108개 → 12행·5열·51셀. 51셀 모두 좌표·원문·행·열·span이 참조와 일치. 병합 8셀(좌상단 2, Retriever 1, Dataset 그룹 5), 병합으로 비는 slot 9개도 유지.</li><li>Column Tree: Retriever → Pneuma-FTS / Pneuma-VS / Hybrid Retrieval.</li><li>Row Tree: ChEMBL / Adventure Works / Public BI / Chicago Open / FeTaQA 각각 → Content / Context. Dataset은 행 축의 머리글이며 각 Dataset 값이 부모 노드다.</li><li>숫자 셀 30개 모두 원문 값과 양쪽 경로가 일치. 추가 진단이며 기존 14probe 비교 분모는 변경하지 않았다.</li><li>StructuredTableIR은 partial, LAYOUT_HYPOTHESIS 유지. 의미 승인·단위/주석 범위 승인·자동 사실 발행은 하지 않았다.</li></ol>',
          '<p>'+link(DATA/'pneuma-p19-diagnostic.json','30개 값과 경로 전체 진단 JSON')+'</p>']
    h.append(table(['원문 값','Row 경로','Column 경로','실제 셀 ID','상태'],[[esc(x['original_value']),esc(' → '.join(x['actual_paths']['row_path'])),esc(' → '.join(x['actual_paths']['column_path'])),esc(x['cell_id'].split('/')[-1]),esc(x['mapping']['evidence_state'])] for x in diag['values']]))
    can=read(DATA/'dev-v2/pneuma/S1/canonical.json'); tid=diag['table_id']; val=next(x for x in diag['values'] if x['original_value']=='83.00')
    physical=next(t for t in can['physical_tables'] if t['table_id']==tid); structured=next(t for t in can['table_structure'] if t['table_id']==tid)
    example={'artifact_kind':'actual output excerpt; not a new Document schema','content':next(c for c in can['contents'] if c.get('table_id')==tid),'physical_cell':next(c for c in physical['cells'] if c['cell_id']==val['cell_id']),'mapping':next(m for m in structured['cell_mappings'] if m['cell_id']==val['cell_id']),'path_nodes':[n for n in structured['row_tree']+structured['column_tree'] if n['node_id'] in val['mapping']['row_path']+val['mapping']['column_path']]}
    with (OUT/'actual-json-example.json').open('x',encoding='utf8') as f:json.dump(example,f,ensure_ascii=False,indent=2)
    h += ['<h3>실제 CanonicalDocument에서 셀까지</h3><pre>contents[kind=table].table_id\n → physical_tables[table_id].cells[cell_id] → original_text / source_bbox / source_cell_ids / span_refs\n → table_structure[table_id].cell_mappings[cell_id]\n → row_path / column_path의 node_id\n → row_tree / column_tree의 label_raw, parent_id, source_cell_id</pre><p>'+link(OUT/'actual-json-example.json','83.00의 실제 content·cell·mapping·노드 JSON 발췌')+' · '+link(DATA/'dev-v2/pneuma/S1/canonical.json','전체 CanonicalDocument JSON')+'</p>']
    h.append('<h2>14개 사례: 원본 / 개선 전 / 개선 후 및 모든 전략 파일</h2><p>아래 개선 후 열은 해당 사례의 관찰에 유용한 전략이다. 성공으로 승인했다는 뜻이 아니다. Structured JSON 안에 전체 Tree와 Cell Mapping이 있다. 모든 파일은 실제 실행 산출물이며 설계 예시가 아니다.</p>')
    for ref in refs:
        cid=ref['id']; title,shown,note=NOTES[cid]; ishold=cid.startswith('H'); run='holdout-v1' if ishold else 'dev-v2'; manifest=manifests['holdout' if ishold else 'development']; item=next(x for x in manifest if x['id']==ref['source_id']); name=item['id']; p=ref['page']; base=DATA/run/name
        pdf=DATA/'sources'/f'{name}.pdf' if ishold else ROOT/'data/new-pdf-holdout/sources'/f'{name}.pdf'
        sourceimg=OUT/f'{name}-p{p}.png' if ishold else ROOT/'reports/new-pdf-holdout'/f'{name}-p{p}.png'
        h += [f'<h3 id="{cid}">{cid} · {esc(title)} · {esc(ref["split"])}</h3><p>{esc(note)}</p>',
              '<p>'+link(pdf,'원본 PDF')+' · '+link(item['ir'],'전체 원본 DocumentIR')+' · '+link(Path(item['ir']).parent/f'physical/page-{p:03}.json','기존 원본 PhysicalPageResult')+'</p>',
              '<div class="images">'+figure(sourceimg,'원본 PDF 페이지')+figure(base/f'S0/page-{p:03}-overlay.png','S0 기준 오버레이')+figure(base/f'S{shown}/page-{p:03}-overlay.png',f'S{shown} 복원 후보 오버레이')+'</div>']
        rows=[]
        data=hold if ishold else dev
        for s in ORDER:
            r=next(x for x in data['cases'] if x['strategy']==s and x['id']==cid); folder=base/f'S{s}'
            rows.append([f'S{s}',str(r['detected']),f'{r["row_count"]}×{r["column_count"]} / {r["cell_count"]}',str(sum(q['cell_exact'] for q in r['probes']))+'/'+str(len(r['probes'])),
                         link(folder/f'page-{p:03}-physical.json','Physical')+' · '+link(folder/f'page-{p:03}-structured.json','Structured+Mapping')+' · '+link(folder/'canonical.json','Canonical')+' · '+link(folder/f'page-{p:03}-overlay.png','오버레이')+' · '+link(folder/'result.json','조건/거절 로그')])
        h.append(table(['전략','대상 영역 매칭','행×열 / 셀','선택 값 일치','전체 실제 출력'],rows))
        rr=[r for r in data['cases'] if r['id']==cid and r['strategy']==shown]
        h.append('<details><summary>이 사례의 실제 probe 경로·오류·Mapping 상태</summary><pre>'+esc(json.dumps(rr,ensure_ascii=False,indent=2))+'</pre></details>')
    h += ['<h2>농협 회귀·계약·보존 검증</h2><p>4문서의 12표·1,292개 셀 전체 객체(값, ID, 좌표, rowspan/colspan, occupied_slots, 원본 링크, 상태), 행/열 band, Row/Column Tree, Cell Mapping이 S0 대비 모든 7개 개선 조합에서 동일하다. Canonical 전체 바이트는 실행 메타데이터 때문에 같지 않다. 차이는 Physical의 analysis_run_id/analyzer_version, Structured의 analysis_run_id/physical_ir_sha256이다.</p>',
          '<p>'+link(DATA/'nh-v2/regression.json','전체 회귀 집계')+' · '+link(DATA/'audit.json','동결 파일/참조/실행 감사')+' · '+link(ROOT/'reports/physical-recovery-tests.xml','pytest 실제 결과 84 passed')+'</p>',
          '<p>96회 모두 Canonical 참조 무결성·기존 복원 값 보존 검사를 통과했다. 의미 검증 완료는 0회다. all_value_cells_preserved는 “복원된 값 셀 보존”이며 미탐지 PDF 값 전체를 복원했다는 뜻이 아니다. 기존 코드 동결 125파일과 이번 동결 130파일의 변경은 0개다. 입력 IR 해시도 모두 일치한다.</p>',
          '<p class="note">실험 Canonical의 source_artifact_refs는 현재 빈 배열이다. 원본 문서 IR 및 파일 경로/해시는 각 result.json의 input_ir·input_ir_sha256·source_sha256과 원본 추출 폴더에 보존된다. JSON 단독 이동·독립 재배포 패키지는 완성하지 않았다. 이 실험 산출물로 운영 Canonical/DB를 덮어쓰지 않았다. 본문 순서·장절·단위·기간·주석 적용 범위도 이번 물리 복원 성공으로 검증 완료 처리하지 않았다.</p>',
          '<h2>실행 시간·비용·실패 이력</h2>']
    times=[]
    for label,run in [('개발','dev-v2'),('농협 회귀','nh-v2'),('새 Holdout','holdout-v1')]:
        results=read(DATA/run/'results.json')['results']
        for s in ORDER:
            local=[r for r in results if r['strategy']==s]
            times.append([label,'S'+s,f'{sum(r["seconds"] for r in local):.2f}s',f'{mean(r["seconds"] for r in local):.2f}s',str(len(local))])
    h.append(table(['분리','전략','합계','문서당 평균','실행수'],times))
    h += ['<p>시간은 기존 DocumentIR부터 선택 페이지 복원·의미 후보 생성·Canonical 조립·오버레이 저장까지 포함한다. PDF 다운로드/최초 추출은 제외하고 raster 캐시를 전략 간 재사용하므로 cold-start 파서 성능 비교가 아니다. 실제 외부 LLM/VLM/Embedding API 호출 0회, API 비용 $0. PostgreSQL/LangGraph/Langfuse 및 운영 검색은 변경하지 않았다.</p>',
          '<p>개발 v1에서는 S3의 표 ID 중복으로 Canonical 생성이 중단된 실행이 있었다. 원본은 남겨두고 제안 인덱스 기반 ID를 쓰도록 동결 전에 수정했다. 글자 bbox의 수직 겹침 때문에 잘못된 수평 병합이 생기는 문제도 동결 전에 제거했다. 최종 개발 v2/농협 v2/Holdout v1에서는 실행 중단 0건이다. 이는 문서 구조 오류 0건이라는 뜻이 아니다.</p>',
          '<h2>재현 명령</h2><p>작업 폴더는 프로젝트 루트다. 기존 출력 경로가 있으면 덮어쓰지 않고 중단하므로 새 경로를 사용한다. 참조 sidecar는 평가기에만 전달된다.</p><pre>.venv/Scripts/python.exe scripts/run_physical_recovery.py --manifest data/physical-recovery/development.json --output data/physical-recovery/dev-rerun001\n.venv/Scripts/python.exe scripts/run_physical_recovery.py --manifest data/physical-recovery/nh.json --output data/physical-recovery/nh-rerun001\n.venv/Scripts/python.exe scripts/run_physical_recovery.py --manifest data/physical-recovery/holdout.json --output data/physical-recovery/holdout-rerun001\n.venv/Scripts/python.exe scripts/evaluate_physical_recovery.py --run data/physical-recovery/dev-rerun001 --references data/physical-recovery/development-references.json --output data/physical-recovery/dev-rerun001/evaluation.json\n.venv/Scripts/python.exe scripts/evaluate_physical_recovery.py --run data/physical-recovery/holdout-rerun001 --references data/physical-recovery/holdout-references.json --output data/physical-recovery/holdout-rerun001/evaluation.json\n.venv/Scripts/python.exe -m pytest tests/test_physical_recovery.py tests/test_physical_grid.py tests/test_semantic_generic.py tests/test_semantic_real.py tests/test_document_rag.py tests/test_document_rag_graph.py -q -p no:cacheprovider</pre>',
          '<h2>10월 29일 시연 적용 권고와 다음 단계</h2><p><strong>현재 권고는 S1의 선택적 실험 적용이다.</strong> PNEUMA처럼 실제 짧은 선 증거가 충분한 표에서 물리 복원을 개선하고, 기존 검증된 농협 경로를 유지한다. TUTA처럼 coarse grid만 생긴 경우와 의미 경로 불일치는 계속 보류한다. S2는 누락되던 raster 실행 조건을 바로잡지만 정확한 표 완성의 증거는 아니다. S3와 전체 조합은 후보 관찰용으로 남긴다.</p><p>다음 별도 실험에서는 표 영역 단위의 fill/raster 판정, 좌우 단·인접 표 분리, 인용 연도를 제외한 열 역할 판정, 숫자 열 하나인 표, 다중 텍스트 줄과 논리 행 구분을 우선 검토한다. 이는 <strong>후속 설계 제안</strong>이며 이번 동결 실험에 반영하지 않았다. 새 Holdout 2문서는 이제 열람되었으므로 다음 구현의 독립 평가에는 또 다른 미사용 문서가 필요하다. LLM/VLM Agent 연결은 이번 단계에 없다.</p>']
    h.append('<p>집계 원본: '+link(DATA/'dev-v2/evaluation.json','개발 평가 JSON')+' · '+link(DATA/'holdout-v1/evaluation.json','Holdout 평가 JSON')+' · '+link(DATA/'implementation.frozen.json','구현 동결 해시')+' · '+link(DATA/'holdout-references.json','Holdout 원본 참조')+'</p></body></html>')
    with (OUT/'comparison.html').open('x',encoding='utf8') as f:f.write('\n'.join(h))
    print(OUT/'comparison.html')


if __name__ == '__main__':
    main()
