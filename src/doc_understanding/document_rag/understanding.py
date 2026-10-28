"""Small question plan, always hypotheses; never SQL filters or semantic approval."""
import json
import os
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from doc_understanding.semantic_resolver.providers import http_transport
from .telemetry import span


class QuestionPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    intent: str = Field(max_length=300)
    institution: str | None = Field(max_length=120)
    periods: list[str] = Field(max_length=10)
    metrics: list[str] = Field(max_length=10)
    question_type: Literal['cell_lookup', 'body', 'comparison', 'unknown']
    search_conditions: list[str] = Field(max_length=10)
    rewritten_query: str = Field(min_length=1, max_length=600)


def understand(question, *, interpreter=None):
    plan = dict(intent='원문 질문으로 검색', institution=None,
                periods=re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question), metrics=[],
                question_type='unknown', search_conditions=[], rewritten_query=question)
    provenance, fallback = 'rule_candidate', None
    if interpreter:
        try:
            plan = QuestionPlan.model_validate(interpreter(question)).model_dump()
            provenance = 'llm_hypothesis'
        except Exception:
            fallback = 'QUESTION_INTERPRETATION_FAILED_USE_ORIGINAL'
    if provenance == 'llm_hypothesis':
        explicit_years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question))
        proposed_years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', ' '.join(plan['periods'])))
        if ((plan['institution'] and plan['institution'] not in question)
                or not proposed_years.issubset(explicit_years)
                or any(metric not in question for metric in plan['metrics'])):
            plan['rewritten_query'] = question
            fallback = 'UNANCHORED_PLAN_CONDITION_USE_ORIGINAL'
    # Guessed institutions/years are NEVER used as DB restrictions. run_id only comes from caller.
    return dict(**plan, original_query=question, provenance=provenance,
                verified_conditions={}, fallback=fallback, mode='agent')


SYSTEM = '''Extract a conservative search plan for the user's question. Return the required JSON.
All inferred conditions are hypotheses, not verified evidence. Preserve the user's requested years,
institution and metric in rewritten_query. Do not invent names/years, answer numbers, or SQL.
Use a short lexical query, not a broad explanation. Null institution when absent. Never answer the question.'''


def live_interpreter(output, *, allow_external=False, transport=http_transport):
    """Exactly one explicit GPT-4.1 call, bounded and separately audited."""
    def interpret(question):
        if not allow_external:
            raise ValueError('EXTERNAL_CALL_NOT_ENABLED')
        key = os.environ.get('OPENAI_API_KEY')
        if not key:
            raise ValueError('API_KEY_UNAVAILABLE')
        payload = dict(model='gpt-4.1', store=False, max_output_tokens=500,
                       input=[dict(role='developer', content=SYSTEM), dict(role='user', content=question)],
                       text=dict(format=dict(type='json_schema', name='question_plan', strict=True,
                                             schema=QuestionPlan.model_json_schema())))
        encoded = json.dumps(payload, ensure_ascii=False).encode('utf8')
        if len(encoded) > 12000:
            raise ValueError('QUESTION_PLAN_BUDGET_EXCEEDED')
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=False)
        def write(name, data):
            (directory/name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
        write('request.json', payload)
        try:
            with span('gpt-4.1.understanding', {'question': question}, generation=True) as record:
                response = transport(payload, key, 45)
                write('response.json', response)
                usage = response.get('usage') or {}
                cost = (usage.get('input_tokens', 0)*2+usage.get('output_tokens', 0)*8)/1_000_000
                # Usage is recorded even when structured output validation fails.
                audit = dict(model=response.get('model', 'gpt-4.1'), usage=usage, estimated_cost_usd=cost)
                write('result.json', audit)
                if response.get('status') != 'completed':
                    raise ValueError('QUESTION_PLAN_INCOMPLETE')
                text = ''.join(c.get('text', '') for item in response.get('output', [])
                               if item.get('type') == 'message' for c in item.get('content', [])
                               if c.get('type') == 'output_text')
                plan = QuestionPlan.model_validate_json(text).model_dump()
                write('plan.json', plan)
                record['output'] = {**audit, 'understanding': plan}
                return plan
        except Exception as error:
            write('failure.json', dict(error=type(error).__name__, attempt=1))
            raise RuntimeError('QUESTION_PLAN_FAILED_SEE_LOCAL_AUDIT') from None
    return interpret
