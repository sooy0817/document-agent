from doc_understanding.review import error_basket
from doc_understanding.web import app
import io
import json
import zipfile

import fitz
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from tests.review_fixtures import saved as saved


def example(saved):
    root,folder,job,request=saved
    with fitz.open(stream=(folder/'input.pdf').read_bytes(),filetype='pdf') as pdf:
        pdf.new_page(width=200,height=200).insert_text((20,40),'Target text')
        (folder/'input.pdf').write_bytes(pdf.tobytes())
    doc=json.loads((folder/'document.json').read_text())
    box=dict(x0=10,y0=10,x1=180,y1=100)
    doc['physical_tables'].append(dict(table_id='T2',page_number=2,bounding_box=box,cells=[
        dict(cell_id='C2',original_text='Target text',source_bbox=box,span_refs=['S2'])]))
    doc['document_ir']={'pages':[dict(page_number=2,tables=[],blocks=[dict(spans=[
        dict(span_id='S2',text_raw='Target text',source={'bbox':box})])])]}
    (folder/'document.json').write_text(json.dumps(doc),encoding='utf8');job['pages']=2
    item=error_basket.collect(root,folder,job,request)
    return root,folder,item['id']


def correction():
    return dict(kind='role',certainty='proposed',source_id='C',observed_role='column_header',
        expected_role='row_header',evidence=[dict(source_id='C',quote='17')],reason='Row label in source')


def test_reference_revisions_uncertainty_export_and_immutability(saved):
    root,folder,item=example(saved)
    original={p:p.read_bytes() for p in folder.iterdir()}
    old={p:p.read_bytes() for p in (root/item).iterdir()}
    first=error_basket.save_reference(root,item,folder,error_basket.ReferenceRequest(claims=[correction()]))
    assert first['gold_status']=='reference_draft'
    no_link=dict(kind='row_group',certainty='proposed',source_id='C',target_id='C2',relation='no_link',
        evidence=[dict(source_id='S2',quote='Target')],reason='New label starts on next page')
    second=error_basket.save_reference(root,item,folder,error_basket.ReferenceRequest(base_revision=first['reference']['revision'],
        claims=[correction(),no_link,dict(kind='context',certainty='uncertain',reason='Cannot determine scope')]))
    assert second['gold_status']=='partial_reference'
    assert len(second['reference_history'])==2
    assert second['reference']['claims'][1]['relation']=='no_link'
    source=next(s for s in second['reference']['source_records'] if s['id']=='S2')
    assert source['page']==2 and source['text']=='Target text' and source['bbox']
    assert all(p.read_bytes()==v for p,v in {**original,**old}.items())
    with zipfile.ZipFile(io.BytesIO(error_basket.export_zip(root))) as archive:
        result=json.loads(archive.read(item+'/record.json'))
        assert len(result['reference_history'])==2
        assert result['training_ready'] is False
        assert result['reference']['approval']=='not_adjudicated'
    error_basket.set_active(root,item,False)
    third=error_basket.save_reference(root,item,folder,error_basket.ReferenceRequest(base_revision=second['reference']['revision'],
        claims=[dict(kind='role',certainty='uncertain',reason='Source not clear')]))
    assert third['gold_status']=='uncertain' and third['active'] is False


@pytest.mark.parametrize('change',[{'source_id':'foreign'}, {'evidence':[dict(source_id='C',quote='fabricated')]},
    {'source_id':'T'}, {'evidence':[dict(source_id='foreign',quote='17')]}, {'evidence':[dict(source_id='C',quote='')]}])
def test_invalid_evidence_is_not_saved(saved,change):
    root,folder,item=example(saved)
    with pytest.raises(HTTPException) as error:
        error_basket.save_reference(root,item,folder,error_basket.ReferenceRequest(claims=[{**correction(),**change}]))
    assert error.value.status_code==422
    assert error_basket.detail(root,item)['reference_history']==[]


def test_stale_revision_and_changed_source_rejected(saved):
    root,folder,item=example(saved)
    req=error_basket.ReferenceRequest(claims=[correction()])
    first=error_basket.save_reference(root,item,folder,req)
    with pytest.raises(HTTPException) as error:error_basket.save_reference(root,item,folder,req)
    assert error.value.status_code==409
    (folder/'document.json').write_text('{}')
    with pytest.raises(HTTPException) as error:error_basket.reference_sources(root,item,folder)
    assert error.value.status_code==409
    assert error_basket.detail(root,item)['reference']==first['reference']


def test_incomplete_claims_and_training_claims_rejected():
    for change in [dict(expected_role='unknown'),dict(source_id=None),dict(evidence=[]),dict(reason=' '),
                   dict(kind='context',target_id=None),dict(kind='context',target_id='C')]:
        with pytest.raises(ValidationError):error_basket.ReferenceRequest(claims=[{**correction(),**change}])
    with pytest.raises(ValidationError):error_basket.ReferenceRequest(claims=[correction()],training_ready=True)


def test_page_anchor_supports_missing_extraction_without_fabricating_cells(saved):
    root,folder,item=example(saved)
    catalog=error_basket.reference_sources(root,item,folder)
    source=next(s for s in catalog['sources'] if s['kind']=='page' and s['page']==2)
    out=error_basket.save_reference(root,item,folder,error_basket.ReferenceRequest(claims=[dict(kind='nested_table',certainty='uncertain',
        evidence=[dict(source_id=source['id'],quote='')],reason='Inner table at bottom; no extracted cell ID')]))
    assert out['reference']['claims'][0]['source_id'] is None
    assert out['reference']['source_records'][0]['bbox']['y1']==200


def test_reference_routes_auth_and_roundtrip(saved,monkeypatch):
    pass
    from tests.http_helpers import request as http_request
    root,folder,job,request=saved
    job['id']=folder.name;root=folder.parent/'error-basket'
    monkeypatch.setattr(app,'DATA',folder.parent)
    monkeypatch.setattr(app,'JOBS',{job['id']:job})
    item=error_basket.collect(root,folder,job,request)['id']
    payload=error_basket.ReferenceRequest(claims=[correction()])
    with pytest.raises(HTTPException) as error:app.basket_reference(item,payload,http_request({}))
    assert error.value.status_code==403
    assert app.basket_sources(item)['sources']
    app.basket_reference(item,payload,http_request({'x-demo-token':app.TOKEN}))
    assert app.basket_detail(item)['reference']['claims'][0]['expected_role']=='row_header'
    assert app.reference_script().media_type=='application/javascript'
    assert b'/reference.js' in app.home().body
