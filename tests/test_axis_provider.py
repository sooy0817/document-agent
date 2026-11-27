import json
import pytest
from doc_understanding.agents.axis_review.axis_provider import AxisModelAdapter
from tests.test_axis_resolver import matrix


def test_opt_in_and_call_budget():
    with pytest.raises(ValueError,match='AUTHORIZED'):
        AxisModelAdapter().resolve(matrix())
    a=AxisModelAdapter(allow_external=True,max_calls=0)
    with pytest.raises(ValueError,match='BUDGET'):
        a.resolve(matrix())


def test_mock_image_request_binding_and_usage():
    def transport(payload,key,timeout):
        assert key=='test-key-not-a-real-openai-credential'
        assert payload['store'] is False
        assert payload['input'][1]['content'][1]['type']=='input_image'
        result=dict(selected_option_ids=['O0'],abstain_option_ids=['O1','O2','O3'],reason='aligned')
        return dict(status='completed',usage=dict(input_tokens=100,output_tokens=50),
            output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(result))])])
    p,r=AxisModelAdapter(allow_external=True,transport=transport).resolve(matrix(),b'image')
    assert p.links[0].cell_id=='e' and p.links[0].row_path==['d']
    assert r['estimated_cost_usd']==.000045


def test_transport_failure_preserves_abstention_without_secret():
    def fail(*args):
        raise OSError('secret request contents')
    p,r=AxisModelAdapter(allow_external=True,transport=fail).resolve(matrix())
    assert not p.links and len(p.abstain_cell_ids)==9
    assert 'secret' not in json.dumps(r)
