"""Evaluation-only source selection; no parser, prompt or inference changes."""
import datetime
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from doc_understanding.parser.pipeline import prepare
from run_axis_resolution import run_analysis

if __name__=='__main__':
    data=ROOT/'data/period-decisions-holdout-20261030-acquired'
    out=ROOT/'reports/period-decisions-20261030/holdout-sources'
    out.mkdir(exist_ok=False)
    selection=dict(selected_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        rules='Selected from original PDF images before extraction/model results. No forms or diagrams in selected pages. Other pages remain in original PDF but are outside this focused evaluation.',
        acquisition_correction='W01 is Korea Investment Savings Bank ISA product hosted by Woori; bank attribution follows PDF, not URL.',
        pages={'S01':[1],'W01':[2]},
        references=[
            dict(id='S01-eligibility',page=1,value='제한없음',status='not_needed',reason='가입대상은 기간 기준이 필요한 값이 아님'),
            dict(id='S01-currency',page=1,value='USD, JPY, EUR, CNY, CAD, GBP, AUD, HKD, SGD, NZD, CHF(1 계좌1 통화)',status='not_needed',reason='가입통화 목록, 별도 기간 정의 없음'),
            dict(id='W01-rate',page=2,value='약정이율 * 50%',status='not_needed',reason='보유기간 1개월이상~12개월미만 구간은 있지만 별도 기간 계산 기준 설명은 인쇄되어 있지 않음. 신규일/최종재예치일은 고시 이율 선택 기준이며 자동으로 기간 정의로 연결하지 않음'),
            dict(id='W01-minimum',page=2,value='보통예금 이율 (현재 연0.20%)',status='not_needed',reason='보유기간 1개월미만과 연결되나 별도 시작/종료 설명은 보이지 않음'),
        ],reference_status='assistant_source_review_draft_pending_human',
        limitation='No unseen positive explicit period-definition reference in this selection; cannot claim required-relation generalization.')
    (out/'selection-before-extraction.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=[]
    for id,pages in selection['pages'].items():
        run,m=prepare(data/(id+'.pdf'),out/(id+'-extraction'))
        for page in pages:
            result=run_analysis(run,page,out/f'{id}-p{page}')
            rows.append(dict(id=id,page=page,run=str(run.relative_to(ROOT)),regions=len(result['regions'])))
            print(json.dumps(rows[-1]),flush=True)
    (out/'extraction.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
