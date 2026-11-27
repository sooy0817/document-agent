# 역할: 행열 조상 관계를 명시적으로 나열하는 별도 평가 방식이며 후보를 자동 수정하지 않는다.
"""B: explicit axis ancestry inventory, no candidate repair (ADR-048)."""
import base64
import hashlib
import json
import os
import time
from typing import Literal
from doc_understanding.agents.axis_review.axis_judge import JudgeResult, MODEL, PROMPT as PROMPT_A, judge_request, validate_judgement, RUBRIC_VERSION as VERSION_A
from doc_understanding.agents.axis_review.axis_resolver import digest
from doc_understanding.domain.models import EvidenceModel
from doc_understanding.agents.semantic_resolver.providers import http_transport

VERSION_B='source-axis-judge/2.0-explicit-ancestry'
PROMPT_B=PROMPT_A+'''
Before scoring, explicitly inventory BOTH axes using the source, including cells omitted from
the candidate. For each axis return:
- existence: present / absent / unknown. absent requires source support that the local structure
  has no such heading; a missing extraction or unreadable evidence is unknown, not absent.
- direct_header_ids: the nearest applicable explicit row label or column heading.
- ancestor_header_ids: ALL applicable higher-level headings, outermost to innermost, excluding
  direct_header_ids. Examine merged cells and local group boundaries, not just the nearest label.
- missing_header_ids: applicable direct/ancestor IDs absent from the proposed path.
- extraneous_header_ids: proposed path IDs that do not apply to this value.
- evidence_cell_ids and a short reason, identifying evidence for absence/uncertainty too.
Do not infer an ancestry merely from alignment/merging: source text and local scope must support
it. A merged group heading that governs the target's row is part of that row's ancestry.
A table's caption, parent-cell prose, units and notes may instead be context, not axis ancestors.
For a heading-less or continuation region do not invent a heading or promote its first body row.
If earlier-page context is needed but unavailable, use unknown. Only use supplied IDs.
Context status is not_needed / supported / insufficient / conflicting, with source IDs and reason.
No document-specific vocabulary or reference verdict is provided. Evaluate the source itself.
The row/column grade MUST agree with this inventory: an omitted ancestor cannot receive 2;
extraneous IDs require 0; unknown existence requires null; a supported absent axis with empty
candidate path can receive 2. Score 1 for a correct but incomplete ancestry. Preserve the existing
four rubric dimensions. Never modify the candidate. This is one assessment, not a repair loop.
'''


class AxisInventory(EvidenceModel):
    axis: Literal['row','column']
    existence: Literal['present','absent','unknown']
    direct_header_ids: list[str]
    ancestor_header_ids: list[str]
    missing_header_ids: list[str]
    extraneous_header_ids: list[str]
    evidence_cell_ids: list[str]
    reason: str


class ExplicitJudgeResult(JudgeResult):
    axes: list[AxisInventory]
    context_status: Literal['not_needed','supported','insufficient','conflicting']
    context_evidence_ids: list[str]
    context_reason: str


def validate_explicit(region, generated, target_id, result):
    base=validate_judgement(region,result);errors=list(base['errors'])
    if result is None:return base
    if sorted(a.axis for a in result.axes)!=['column','row']:errors.append('AXIS_INVENTORY_MISSING_OR_DUPLICATE')
    ids={c.id for c in region.cells};link=next(p for p in generated.links if p.cell_id==target_id)
    grades={g.dimension:g.score for g in result.grades}
    for a in result.axes:
        path=link.row_path if a.axis=='row' else link.column_path
        refs=a.direct_header_ids+a.ancestor_header_ids
        allrefs=refs+a.missing_header_ids+a.extraneous_header_ids+a.evidence_cell_ids
        if any(i not in ids for i in allrefs):errors.append('UNKNOWN_INVENTORY_ID')
        if target_id in refs or len(refs)!=len(set(refs)):errors.append('SELF_OR_DUPLICATE_ANCESTOR')
        if not a.evidence_cell_ids or not a.reason.strip():errors.append('INVENTORY_EVIDENCE_MISSING')
        if a.existence=='present' and not refs:errors.append('PRESENT_WITHOUT_HEADER')
        if a.existence=='absent' and refs:errors.append('ABSENT_WITH_HEADERS')
        if a.existence!='unknown':
            if set(a.missing_header_ids)!=set(refs)-set(path):errors.append('MISSING_INVENTORY_INCONSISTENT')
            if set(a.extraneous_header_ids)!=set(path)-set(refs):errors.append('EXTRA_INVENTORY_INCONSISTENT')
        score=grades.get(a.axis+'_path')
        if a.missing_header_ids and score==2:errors.append('OMISSION_SCORED_COMPLETE')
        if a.extraneous_header_ids and score!=0:errors.append('EXTRA_HEADER_SCORE_CONFLICT')
        if a.existence=='unknown' and score is not None:errors.append('UNKNOWN_AXIS_SCORE_CONFLICT')
    if any(i not in ids for i in result.context_evidence_ids):errors.append('UNKNOWN_CONTEXT_ID')
    if not result.context_reason.strip():errors.append('CONTEXT_REASON_MISSING')
    if result.context_status in {'supported','conflicting'} and not result.context_evidence_ids:
        errors.append('CONTEXT_EVIDENCE_MISSING')
    return {**base,'valid':not errors,'errors':sorted(set(errors))}


class ComparisonJudgeAdapter:
    """A and B share identical source/candidate input, model and transport; schema/rubric vary."""
    def __init__(self,*,allow_external=False,max_calls=1,transport=http_transport):
        self.allow_external=allow_external;self.max_calls=max_calls;self.calls=0;self.transport=transport

    def judge(self,region,generated,target_id,image_bytes,variant):
        if variant not in {'A','B'}:raise ValueError('UNKNOWN_VARIANT')
        if not self.allow_external:raise ValueError('EXTERNAL_CALL_NOT_AUTHORIZED')
        if self.calls>=self.max_calls:raise ValueError('CALL_BUDGET_EXCEEDED')
        aliases,data=judge_request(region,generated,target_id)
        encoded=json.dumps(data,ensure_ascii=False)
        if len(region.cells)>180 or len(encoded.encode())>100000:raise ValueError('INPUT_BUDGET_EXCEEDED')
        if len(image_bytes)>4000000:raise ValueError('IMAGE_BUDGET_EXCEEDED')
        key=os.getenv('OPENAI_API_KEY')
        if not key:raise ValueError('API_KEY_UNAVAILABLE')
        cls=JudgeResult if variant=='A' else ExplicitJudgeResult
        prompt=PROMPT_A if variant=='A' else PROMPT_B
        payload=dict(model=MODEL,store=False,temperature=0,max_output_tokens=4000,
            input=[dict(role='system',content=prompt),dict(role='user',content=[
                dict(type='input_text',text=encoded),dict(type='input_image',detail='high',
                image_url='data:image/png;base64,'+base64.b64encode(image_bytes).decode())])],
            text=dict(format=dict(type='json_schema',name='axis_judge',strict=True,schema=cls.model_json_schema())))
        record=dict(variant=variant,model=MODEL,rubric_version=VERSION_A if variant=='A' else VERSION_B,
            prompt=prompt,prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            source_sha256=digest(region),request_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
            image_sha256=hashlib.sha256(image_bytes).hexdigest(),request=data,aliases=aliases,
            status='failed',usage=None,estimated_cost_usd=None,max_output_tokens=4000)
        self.calls+=1;start=time.perf_counter();result=None
        try:
            response=self.transport(payload,key,90);record['response']=response
            usage=response.get('usage') or {};record['usage']=usage or None
            if 'input_tokens' in usage and 'output_tokens' in usage:
                cached=(usage.get('input_tokens_details') or {}).get('cached_tokens',0)
                record['estimated_cost_usd']=((usage['input_tokens']-cached)*2+cached*.5+usage['output_tokens']*8)/1e6
            if response.get('status')!='completed':raise ValueError('INCOMPLETE_RESPONSE')
            parts=[c for i in response.get('output',[]) if i.get('type')=='message' for c in i.get('content',[])]
            if any(c.get('type')=='refusal' for c in parts):raise ValueError('MODEL_REFUSAL')
            parsed=cls.model_validate_json(''.join(c.get('text','') for c in parts if c.get('type')=='output_text'))
            def bind(obj):
                if isinstance(obj,dict):return {k:([aliases.get(i,'INVALID_ALIAS:'+i) for i in v]
                    if k.endswith('_ids') else bind(v)) for k,v in obj.items()}
                if isinstance(obj,list):return [bind(v) for v in obj]
                return obj
            result=cls.model_validate(bind(parsed.model_dump(mode='json')));record['status']='completed'
        except Exception as exc:
            record['error_type']=type(exc).__name__;record['error_code']='CALL_OR_SCHEMA_FAILED'
        record['elapsed_seconds']=time.perf_counter()-start
        return result,record
