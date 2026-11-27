import json
import pytest
from tests.test_axis_generation import merged
from tests.test_axis_judge import result
from doc_understanding.agents.axis_review.axis_judge_b import ExplicitJudgeResult, ComparisonJudgeAdapter, validate_explicit
from doc_understanding.agents.axis_review.axis_judge import judge_request


def explicit():
    d=result().model_dump()
    d.update(axes=[dict(axis=axis,existence='present',direct_header_ids=direct,
        ancestor_header_ids=anc,missing_header_ids=[],extraneous_header_ids=[],
        evidence_cell_ids=direct+anc,reason='source') for axis,direct,anc in
        [('row',['label'],[]),('column',['h1'],['year'])]],
        context_status='not_needed',context_evidence_ids=[],context_reason='local headers sufficient')
    return ExplicitJudgeResult.model_validate(d)


def test_complete_and_omitted_ancestor_are_distinct():
    r,g=merged();j=explicit();assert validate_explicit(r,g,'v1',j)['valid']
    bad=g.model_copy(update={'links':[g.links[0].model_copy(update={'column_path':['h1']}),g.links[1]]})
    j.axes[1]=j.axes[1].model_copy(update={'missing_header_ids':['year']})
    assert 'OMISSION_SCORED_COMPLETE' in validate_explicit(r,bad,'v1',j)['errors']
    j.grades[2]=j.grades[2].model_copy(update={'score':1});j=j.model_copy(update={'verdict':'partial'})
    assert validate_explicit(r,bad,'v1',j)['valid']


def test_absent_is_not_unknown_or_invented_header():
    r,g=merged();g.links[0]=g.links[0].model_copy(update={'row_path':[],'row_axis':'absent'})
    j=explicit();j.axes[0]=j.axes[0].model_copy(update={'existence':'absent','direct_header_ids':[]})
    assert validate_explicit(r,g,'v1',j)['valid']
    j.axes[0]=j.axes[0].model_copy(update={'existence':'unknown'})
    assert 'UNKNOWN_AXIS_SCORE_CONFLICT' in validate_explicit(r,g,'v1',j)['errors']
    j.grades[1]=j.grades[1].model_copy(update={'score':None});j=j.model_copy(update={'verdict':'unjudgeable'})
    assert validate_explicit(r,g,'v1',j)['valid']


def test_unknown_source_and_inconsistent_omission_list_rejected():
    r,g=merged();j=explicit();j.axes[0]=j.axes[0].model_copy(update={'ancestor_header_ids':['fake']})
    a=validate_explicit(r,g,'v1',j)
    assert 'UNKNOWN_INVENTORY_ID' in a['errors'] and 'MISSING_INVENTORY_INCONSISTENT' in a['errors']


def test_ab_identical_inputs_and_single_b_call():
    r,g=merged();aliases,_=judge_request(r,g,'v1');rev={v:k for k,v in aliases.items()};seen=[]
    def transport(payload,key,timeout):
        seen.append(payload);d=explicit().model_dump() if len(seen)==2 else result().model_dump()
        def bind(o):
            if isinstance(o,dict):return {k:([rev[i] for i in v] if k.endswith('_ids') else bind(v)) for k,v in o.items()}
            if isinstance(o,list):return [bind(v) for v in o]
            return o
        return dict(status='completed',usage=dict(input_tokens=10,output_tokens=10),output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(bind(d)))])])
    a=ComparisonJudgeAdapter(allow_external=True,max_calls=2,transport=transport)
    ja,ra=a.judge(r,g,'v1',b'image','A');jb,rb=a.judge(r,g,'v1',b'image','B')
    assert ra['request_sha256']==rb['request_sha256'] and seen[0]['input'][1]==seen[1]['input'][1]
    assert validate_explicit(r,g,'v1',jb)['valid'] and a.calls==2
    assert 'data:image' not in json.dumps(rb)
    with pytest.raises(ValueError,match='CALL_BUDGET'):a.judge(r,g,'v1',b'image','B')
