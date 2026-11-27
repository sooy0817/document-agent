"""저장소에 없는 로컬 자료가 필요한 테스트를 기본 실행에서 건너뛴다.

tests/local-data-tests.txt의 테스트는 원본 PDF, 과거 실행 결과, 평가 산출물
(data/, reports/, outputs/ 아래. Git에 포함하지 않음)을 읽는다. 그 자료가 있는
환경에서는 RUN_LOCAL_DATA_TESTS=1로 함께 실행한다.
"""
import os
from pathlib import Path

import pytest

LOCAL_DATA_TESTS = Path(__file__).parent / "tests" / "local-data-tests.txt"


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LOCAL_DATA_TESTS") == "1":
        return
    listed = set(LOCAL_DATA_TESTS.read_text(encoding="utf-8").split("\n"))
    skip = pytest.mark.skip(reason="needs local data not in this repository (RUN_LOCAL_DATA_TESTS=1)")
    for item in items:
        if item.nodeid in listed:
            item.add_marker(skip)
