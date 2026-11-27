# 역할: 파서 수정 제안·격리 실행·재추출 큐를 관리한다. 정답 비교는 evaluation.parser에 위임한다.
"""Run a pinned, locally prepared patch and source checks through the repair queue."""
from pathlib import Path
from doc_understanding.experiments.parser_repair import engine


def execute(record, data, folder, progress, plan):
    if record['snapshot']['source_pdf_sha256'] != plan['reference']['source_pdf_sha256']:
        raise ValueError('PLAN_SOURCE_MISMATCH')
    edits=plan['proposal']['edits']
    if edits and all(e['path'] in engine.ALLOWED and (engine.ROOT/e['path']).read_text(encoding='utf8')==e['after'] for e in edits):
        from doc_understanding.experiments.parser_repair.parser_refresh import execute as refresh
        progress('already_applied','동일 수정안이 이미 반영돼 있습니다. 다시 수정하지 않고 최신 결과를 비교합니다.')
        return refresh(record,data,folder,progress)
    for name, expected in plan['baseline_hashes'].items():
        if name not in engine.ALLOWED or engine.sha(engine.ROOT/name) != expected:
            raise ValueError('PLAN_BASELINE_CHANGED')
    for test in plan['tests']:
        relative=Path(test.split('::')[0])
        if relative.is_absolute() or '..' in relative.parts or not str(relative).replace('\\','/').startswith('tests/'):
            raise ValueError('PLAN_TEST_SCOPE_INVALID')
    engine.save(folder/'registered-plan.json',plan)
    engine.save(folder/'reference.json',plan['reference'])
    progress('source_criteria','원본 참조와 성공 조건을 고정했습니다. 추출 입력에는 정답을 전달하지 않습니다.')
    def proposer(packet, output):
        return plan['proposal'],dict(api_calls=0,estimated_cost_usd=0,provider='registered_local_patch')
    result=engine.execute(record,data,folder,progress,proposer=proposer,
                          reference=plan['reference'],test_paths=plan['tests'])
    result['mode']='registered_patch'
    if result.get('acceptance_passed'):
        result.update(status='awaiting_source_review', human_review_required=True,
                      next_action='검사한 후보를 보존했습니다. 마지막 사람 검증 전에는 파서에 반영하지 않습니다.')
    engine.save(folder/'result.json',result)
    return result


def apply_verified(folder, plan, result):
    """Explicit registered plans only; never push, deploy, or rewrite old IRs."""
    if not result.get('acceptance_passed'):
        raise ValueError('ACCEPTANCE_REQUIRED')
    # Legacy callers have no immutable, candidate-bound human approval receipt.
    # Fail closed until the promotion transaction is connected (ADR-071).
    raise ValueError('HUMAN_APPROVAL_WORKFLOW_NOT_CONNECTED')
