"""Copy an immutable comparison into a NEW explorer job, preserving old runs."""
import argparse
import json
import shutil
import uuid
from pathlib import Path


def publish(job, report):
    new=job.parent/uuid.uuid4().hex
    new.mkdir()
    for name in ('input.pdf','document.json','nested.json','result.json'):
        shutil.copy2(job/name,new/name)
    review=json.loads((job/'review.json').read_text(encoding='utf8'))
    review.update(name=review['name']+' · LangGraph 관계 검토',baseline_job_id=job.name,
                  continuation_report=True,continuation_report_asset='comparison-reviewed.html',status='human_review_pending')
    (new/'review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf8')
    destination=new/'continuation';destination.mkdir()
    for p in report.iterdir():
        if p.is_file() and p.suffix in ('.png','.html','.json'):
            shutil.copy2(p,destination/p.name)
    record=dict(job_id=new.name,explorer_url=f'http://127.0.0.1:8766/?job={new.name}&page=12',
                report_url=f'http://127.0.0.1:8766/api/jobs/{new.name}/continuation/comparison-reviewed.html')
    with (report/'review-location.json').open('x',encoding='utf8') as f:
        json.dump(record,f,ensure_ascii=False,indent=2)
    print(json.dumps(record))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();publish(a.job,a.report)
