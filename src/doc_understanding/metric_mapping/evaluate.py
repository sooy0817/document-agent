"""Same anchored evaluation contract for Rule/Agent/Hybrid; absent arms are N/A."""
import json
from pathlib import Path

from .core import compact

# Assisted annotations from original source text/images, never inferred from mapper decisions.
# These are development annotations requiring independent human review.
CASES = [
    ("D1",2,"자산총계","total_assets",True,"자산 영역의 전체 금액"),
    ("D1",2,"부채총계","total_liabilities",True,"자본을 제외한 부채 합계"),
    ("D1",2,"자본총계","total_equity",True,"부채와 별도인 자본 총계"),
    ("D1",2,"부채및자본총계","total_equity",False,"부채와 자본 합산, 자본 단독이 아님"),
    ("D1",3,"Ⅰ.영업수익","operating_revenue",True,"신용·경제사업을 포함하는 상위 영업수익"),
    ("D1",3,"1.신용사업영업수익","operating_revenue",False,"신용사업 구성항목, 전체 영업수익 아님"),
    ("D1",3,"Ⅸ.법인세차감전당기순손익","net_income",False,"법인세 차감 전 단계"),
    ("D1",3,"Ⅺ.법인세차감후당기순손익","net_income",True,"법인세 차감 후 최종 손익"),
    ("D1",4,"총자산순이익률","total_assets",False,"금액이 아닌 수익률"),
    ("D3",9,"1좌당당기순이익","net_income",False,"좌당 금액, 전체 순손익 아님"),
    ("D3",10,"Ⅸ.법인세차감전당기순손익","net_income",False,"세전 손익"),
    ("D3",10,"Ⅺ.당기순손익","net_income",True,"최종 손익 단계"),
    ("D2",7,"자산총계","total_assets",True,"재무상태표 연속 페이지의 총자산"),
    ("D2",8,"자본총계","total_equity",True,"재무상태표 연속 페이지의 자본총계"),
    ("D2",9,"Ⅰ.영업수익","operating_revenue",True,"손익계산서의 영업수익 합계"),
    ("D5",1,"Ⅰ.영업수익","operating_revenue",True,"통합 손익계산서의 영업수익"),
    ("D5",4,"ⅩⅢ.당기순이익","net_income",True,"최종 세후 손익. 당기/전기 이익·손실 분할 주의"),
    ("D4",1,"당기순손익","net_income",True,"자본변동에 반영된 손익; 기간과 중복 열은 별도 판단"),
]


def metrics(cases, predictions):
    tp=fp=fn=tn=abstain=missing=0
    for c in cases:
        decision=predictions.get(c["row_id"])
        positive=decision=="PROPOSE"
        tp+=int(positive and c["expected_target"])
        fp+=int(positive and not c["expected_target"])
        fn+=int(not positive and c["expected_target"])
        tn+=int(not positive and not c["expected_target"])
        abstain+=int(decision=="ABSTAIN")
        missing+=int(decision is None)
    return dict(n=len(cases),tp=tp,fp=fp,fn=fn,tn=tn,
                precision=tp/(tp+fp) if tp+fp else None,
                recall=tp/(tp+fn) if tp+fn else None,
                abstentions=abstain,silent_missing=missing,
                scope="target identity only; abstentions count as recall misses, not wrong mappings")


def evaluate(data,report):
    docs={p.stem:json.loads(p.read_text(encoding="utf-8")) for p in data.glob("D*.json")}
    gold=[]
    for doc,page,label,metric,expected,reason in CASES:
        # Resolve annotated labels against ORIGINAL DocumentIR, not prediction.
        prior=json.loads(Path(f"reports/staging-five/{doc}/result.json").read_text(encoding="utf-8"))
        ir=json.loads((Path(prior["manifest"]["run_dir"])/"document-ir.json").read_bytes())
        cells=[c for t in ir["pages"][page-1]["tables"] for c in t["cells"]
               if compact(c["text_raw"])==label]
        if len(cells)!=1:
            raise ValueError(f"Annotation source ambiguity: {doc}/{page}/{label}")
        cell=cells[0]
        import hashlib
        row_id=hashlib.sha256((cell["cell_id"]+metric).encode()).hexdigest()[:24]
        gold.append(dict(document=doc,page=page,label=cell["text_raw"],metric=metric,
                         row_id=row_id,expected_target=expected,reason=reason,
                         source_sha256=ir["source_sha256"],source=cell["source"],
                         cell_id=cell["cell_id"],review_status="provisional_assisted",
                         independent_human_review=False))
    baseline={c["row_id"]:"PROPOSE" for c in gold}
    contextual={r["row_id"]:r["decision"] for d in docs.values() for r in d["rows"]}
    controls=[
        ("total_assets",2024,"155422"),("total_assets",2025,"169382"),
        ("total_liabilities",2024,"144162"),("total_liabilities",2025,"157331"),
        ("total_equity",2024,"11260"),("total_equity",2025,"12051"),
        ("operating_revenue",2024,"26542"),("operating_revenue",2025,"31638"),
        ("net_income",2024,"664"),("net_income",2025,"665"),
    ]
    bindings=[]
    for metric,year,value in controls:
        match=[(r,b) for r in docs["D1"]["rows"] if r["metric"]==metric and r["decision"]=="PROPOSE"
               for b in r["bindings"] if b["column_role"]=="amount_candidate"
               and b["period"]["year"]==year]
        ok=len(match)==1 and match[0][1]["parsed_literal"]==value and match[0][1]["unit"]=="백만원"
        bindings.append(dict(document="D1",metric=metric,year=year,expected_value=value,
            expected_unit="백만원",correct=ok,evidence=match[0][1]["evidence"] if match else [],
            scope="display year/value/unit only; exact dates/accounting scope not evaluated",
            review_status="provisional_assisted"))
    output=dict(
        gold_status="provisional assisted development annotations; not independent Gold",
        baseline=metrics(gold,baseline),contextual_rule=metrics(gold,contextual),
        agent=None,hybrid=None,independent_accuracy=None,document_recall=None,
        binding_controls=bindings,binding_correct=sum(b["correct"] for b in bindings),
        binding_total=len(bindings),
        failures=[c|{"actual":contextual.get(c["row_id"])} for c in gold
                  if c["expected_target"] and contextual.get(c["row_id"])!="PROPOSE"],
        evaluation_contract=dict(join_key="row_id bound to original cell + target metric",
            output_decisions=["PROPOSE","OTHER_METRIC","ABSTAIN"],
            absent_strategy="N/A; never substitute mock for actual Agent",
            identity_vs_binding="score separately; all candidates remain unreviewed"))
    report.mkdir(parents=True,exist_ok=True)
    (report/"provisional-gold.json").write_text(json.dumps(gold,ensure_ascii=False,indent=2),encoding="utf-8")
    (report/"evaluation.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print({k:output[k] for k in ("baseline","contextual_rule","binding_correct","binding_total")})


if __name__=="__main__":
    import sys
    evaluate(Path(sys.argv[1]),Path(sys.argv[2]))
