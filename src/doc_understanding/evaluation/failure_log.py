# 역할: 채점 실행마다 실패 기록 파일을 남기고, 누적 장부(실행 한 줄 요약)에 덧붙인다.
"""실패 장부 저장.

- <실행 폴더>/failures.json: 그 실행의 실패 기록 전체(셀·문항 단위).
- work/failures/ledger.jsonl: 실행마다 한 줄(시각·종류·세트·라벨·코드 버전·건수 요약·기록 파일 경로). 지우지 않고 덧붙인다.
문서를 새로 넣거나 규칙을 고칠 때마다 같은 형식으로 쌓아, 유형별 건수와 추이를 본다.
개발용·검증용 세트는 세트 이름으로 구분한다(검증용 실패는 유형만 참고하고 그 문서에 맞춰 고치지 않는다).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path("work/failures/ledger.jsonl")


def record(kind, set_name, label, failures, summary, out_dir, *, commit=None, ledger=LEDGER):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "failures.json"
    path.write_text(json.dumps(dict(kind=kind, set=set_name, label=label, commit=commit, summary=summary,
                                    failures=failures), ensure_ascii=False, indent=1), encoding="utf-8")
    line = dict(at=datetime.now(timezone.utc).isoformat(timespec="seconds"), kind=kind, set=set_name, label=label,
                commit=commit, total=len(failures), summary=summary, file=str(path))
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path


def read_ledger(ledger=LEDGER):
    path = Path(ledger)
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
