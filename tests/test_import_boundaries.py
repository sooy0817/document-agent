"""패키지 의존 방향: 파서와 런타임이 에이전트 내부 구현을 가져다 쓰는 곳을 고정한다(늘어나면 실패).

공용 자료형·유틸은 domain/과 runtime/openai_http.py에 둔다. 아래 '남은 의존'은 모듈 자체를 옮겨야 풀리는 것으로,
목록을 줄이는 것은 좋지만 새로 추가하지 않는다.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "doc_understanding"
IMPORT = re.compile(r"^\s*(?:from|import)\s+doc_understanding\.(\w+)((?:\.\w+)*)", re.M)

# 남은 의존(2026-11-12): 기간 문맥의 모델 호출이 에이전트의 요청 형식·모델 이름을 쓰고,
# 검토 실행기(model_client, review_queue)가 에이전트 역할·작업 흐름을 직접 부른다.
REMAINING = {
    "parser/context/period_context.py": {"agents.axis_review.axis_generation", "agents.axis_review.axis_judge"},
    "runtime/model_client.py": {"agents.roles", "agents.workflows.legacy_review"},
    "runtime/review_queue.py": {"agents.workflows.registry", "agents.workflows.table_review.service"},
}


def agent_imports(package):
    found = {}
    for path in sorted((SRC / package).rglob("*.py")):
        names = {top + rest for top, rest in IMPORT.findall(path.read_text(encoding="utf8")) if top == "agents"}
        if names:
            found[path.relative_to(SRC).as_posix()] = names
    return found


def test_parser_and_runtime_do_not_gain_dependencies_on_agent_internals():
    found = {**agent_imports("parser"), **agent_imports("runtime")}
    assert found == REMAINING


def test_shared_modules_do_not_depend_on_higher_layers():
    for name in ("domain/axis_regions.py", "domain/text_markers.py", "runtime/openai_http.py"):
        tops = {top for top, _ in IMPORT.findall((SRC / name).read_text(encoding="utf8"))}
        assert tops <= {"domain"}, (name, tops)


def test_moved_names_are_the_same_objects_at_their_old_paths():
    from doc_understanding.agents.axis_review import axis_resolver
    from doc_understanding.agents.semantic_resolver import features, models, providers
    from doc_understanding.domain import axis_regions, text_markers
    from doc_understanding.runtime import openai_http, structured_call

    for name in ("AxisRegion", "CellEvidence", "area", "digest", "encloses", "overlap"):
        assert getattr(axis_resolver, name) is getattr(axis_regions, name)
    for name in ("compact", "number_marker", "roles"):
        assert getattr(features, name) is getattr(text_markers, name)
    assert models.NumberMarker is text_markers.NumberMarker
    assert providers.http_transport is openai_http.http_transport is structured_call.http_transport
    assert structured_call.RESPONSES_URL == openai_http.RESPONSES_URL
