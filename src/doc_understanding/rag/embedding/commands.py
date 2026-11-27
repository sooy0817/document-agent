# 역할: 임베딩 실행의 성공·실패와 사용량을 새 로컬 결과 파일에 기록한다.
import json

from doc_understanding.rag.embedding.indexer import embed_run
from doc_understanding.rag.embedding.options import client_from_args


def run_embed(args):
    client = client_from_args(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # 호출 전에 출력 파일을 확보하고, 실패하더라도 이미 사용한 호출·비용 기록을 남긴다.
    with args.output.open("x", encoding="utf-8") as stream:
        result = dict(status="started", run_id=args.run_id, profile_id=client.config.profile_id)
        try:
            result.update(embed_run(args.run_id, client), status="completed")
        except Exception as error:
            result.update(status="failed", error_type=type(error).__name__)
            raise
        finally:
            result.update(
                calls=client.audit,
                reserved_cost_usd=client.reserved_cost_usd,
                cost_complete=all("estimated_cost_usd" in c for c in client.audit),
                estimated_cost_usd=sum(c.get("estimated_cost_usd", 0) for c in client.audit),
            )
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {k: result[k] for k in ("status", "total", "reused", "written", "coverage_complete")}
        )
    )
