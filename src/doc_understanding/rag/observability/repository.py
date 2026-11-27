# 역할: 검색·원본 조회의 요약을 공통 실행 추적에 연결한다.
# 역할: 검색·셀 조회 전용 추적을 공통 실행 추적과 연결한다. 원본 문서 전체를 전송하지 않는다.
from doc_understanding.runtime.telemetry import ACTIVE, span, candidates_summary

SCORE_METHOD = 'sum(min(term_length,6)*(primary_match?1:.2))/term_count + ts_rank_cd(simple_fts)'


def traced_repository(kind):
    from functools import wraps
    def decorate(function):
        @wraps(function)
        def execute(*args, **kwargs):
            if not ACTIVE.get():
                return function(*args, **kwargs)
            names = ('query', 'run_id', 'limit') if kind == 'search' else (
                ('run_id', 'parent_cell_id') if kind == 'nested_source_lookup' else
                ('run_id', 'table_id', 'cell_id'))
            inputs = dict(zip(names, args)) | kwargs
            if kind == 'search':
                from doc_understanding.rag.retrieval.lexical import terms
                inputs['query_terms'] = terms(inputs.get('query', inputs.get('question', '')))
                inputs['score_method'] = SCORE_METHOD
            with span('postgres.' + kind, inputs) as box:
                result = function(*args, **kwargs)
                box['output'] = {'candidates': candidates_summary(result), 'score_method': SCORE_METHOD} if kind == 'search' else (
                    {'document_id': result.document_id, 'validation': 'canonical_json_validated'} if kind == 'fetch_document'
                    else {'evidence': result})
                if kind == 'nested_source_lookup':
                    box['output'] = {'evidence': [dict(
                        parent_cell_id=row['parent_cell']['cell_id'],
                        span_refs=row['parent_cell']['span_refs'])
                        for row in result]}
                return result
        return execute
    return decorate
