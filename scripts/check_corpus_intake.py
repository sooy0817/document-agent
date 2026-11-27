"""적재 점검: 코퍼스의 각 문서에서 매핑 이전 단계의 문제(글자 없음·표가 아닌 표·조각난 표)를 모아 실패 장부에 남긴다.

정답표 없이 돌릴 수 있다. 새 문서를 넣을 때마다 실행한다. 모델 호출 없음.
  python scripts/check_corpus_intake.py            # 현재 코퍼스 전체
"""
import argparse
from datetime import datetime
from pathlib import Path

from doc_understanding.evaluation import failure_log, intake
from doc_understanding.services.corpus_rag import corpus_documents


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    issues = []
    for doc in corpus_documents().values():
        issues += intake.document_issues(doc)
    summary = intake.summarize(issues)
    out = args.out or Path("work/failures") / f"intake-{datetime.now():%Y%m%d-%H%M%S}"
    path = failure_log.record("intake", "corpus", "intake", issues, summary, out)
    for doc, kinds in summary["by_doc"].items():
        print(doc[:40], kinds)
    print("total", summary["total"], summary["by_kind"], "->", path)


if __name__ == "__main__":
    main()
