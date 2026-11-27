from doc_understanding.review import error_basket
from doc_understanding.tools import evidence_context
from doc_understanding.web import app
import hashlib
import io
import json
import zipfile

import fitz
import pytest
from fastapi import HTTPException


from tests.review_fixtures import saved


def test_collect_deduplicates_and_preserves_source_with_history(saved):
    root,folder,job,request=saved
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    first=error_basket.collect(root,folder,job,request)
    second=error_basket.collect(root,folder,job,request.model_copy(update={'note':'Revised note'}))
    assert first['id']==second['id']
    assert len(error_basket.list_items(root))==1
    detail=error_basket.detail(root,first['id'])
    assert len(detail['history'])==2
    assert detail['snapshot']['selection']['cell_id']=='C'
    assert detail['snapshot']['selected_cell']['original_text']=='17'
    assert detail['training_ready'] is False
    assert detail['gold_status']=='unreviewed'
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}


def test_exclude_restore_and_export_only_active_local_evidence(saved):
    root,folder,job,request=saved
    item=error_basket.collect(root,folder,job,request)
    error_basket.set_active(root,item['id'],False)
    with zipfile.ZipFile(io.BytesIO(error_basket.export_zip(root))) as archive:
        assert json.loads(archive.read('manifest.json'))['items']==[]
    error_basket.set_active(root,item['id'],True)
    with zipfile.ZipFile(io.BytesIO(error_basket.export_zip(root))) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        assert manifest['training_ready'] is False
        assert len(manifest['items'])==1
        assert any(n.endswith('/source.png') for n in archive.namelist())
        assert not any(n.endswith('.pdf') for n in archive.namelist())
        record=json.loads(archive.read(item['id']+'/record.json'))
        assert record['history'][0]['error_type']=='column_path'


@pytest.mark.parametrize('changes',[{'cell_id':'foreign'},{'page':2},{'table_id':'foreign'},{'scope':'table','cell_id':'C'}])
def test_rejects_wrong_source_membership(saved,changes):
    root,folder,job,request=saved
    with pytest.raises(HTTPException):error_basket.collect(root,folder,job,request.model_copy(update=changes))
    assert error_basket.list_items(root)==[]


def test_missing_table_region_preserves_no_cell_identity(saved):
    root,folder,job,request=saved
    job['review']={'cases':[{'id':'K','page':1,'review_bbox':[10,10,100,100]}]}
    req=error_basket.CollectionRequest(page=1,scope='case_region',case_id='K',error_type='table_missing')
    item=error_basket.collect(root,folder,job,req)
    snap=error_basket.detail(root,item['id'])['snapshot']
    assert snap['selected_cell'] is None
    assert snap['selection']['cell_id'] is None
    assert snap['selection']['case_id']=='K'
    with pytest.raises(HTTPException):error_basket.detail(root,'../../outside')


def test_routes_require_token_and_ignore_client_gold_claim(saved,monkeypatch):
    pass
    from tests.http_helpers import request as http_request
    from pydantic import ValidationError
    root,folder,job,request=saved
    job['id']=folder.name
    monkeypatch.setattr(app,'DATA',folder.parent)
    monkeypatch.setattr(app,'JOBS',{job['id']:job})
    with pytest.raises(HTTPException):app.basket_collect(job['id'],request,http_request({}))
    with pytest.raises(ValidationError):error_basket.CollectionRequest(**request.model_dump(),training_ready=True)
    response=app.basket_collect(job['id'],request,http_request({'x-demo-token':app.TOKEN}))
    assert response['training_ready'] is False
    item=response['id']
    assert app.basket_image(item).media_type=='image/png'
    assert app.basket_export().status_code==200
    with pytest.raises(HTTPException):app.basket_active(item,error_basket.ActiveRequest(active=False),http_request({}))


def test_changed_source_hash_cannot_be_collected(saved):
    root,folder,job,request=saved
    job['review']={'document':{'sha256':'incorrect'}}
    with pytest.raises(HTTPException) as caught:error_basket.collect(root,folder,job,request)
    assert caught.value.status_code==409
    assert error_basket.list_items(root)==[]


def test_whole_table_and_cell_are_distinct_collection_scopes(saved):
    root,folder,job,request=saved
    cell=error_basket.collect(root,folder,job,request)
    table=error_basket.collect(root,folder,job,request.model_copy(update={'scope':'table','cell_id':None,'error_type':'nested_table'}))
    assert cell['id']!=table['id']
    assert len(error_basket.list_items(root))==2
    snap=error_basket.detail(root,table['id'])['snapshot']
    assert snap['selected_cell'] is None
    assert snap['physical_tables'][0]['table_id']=='T'
    assert snap['canonical_table_structure'][0]['cell_mappings'][0]['cell_id']=='C'


def test_native_physical_parent_identity_and_child_context_are_saved(saved):
    root,folder,job,request=saved
    doc=json.loads((folder/'document.json').read_text())
    parent=doc['physical_tables'][0]['cells'][0]
    native=dict(cell_id='native-C',span_refs=['S'],source={'bbox':parent['source_bbox']})
    doc['document_ir']={'pages':[dict(page_number=1,blocks=[{'spans':[dict(span_id='S',text_raw='17')]}],
        tables=[dict(table_candidate_id='native-T',cells=[native])])]}
    child=dict(table_id='child',page_number=1,bounding_box=parent['source_bbox'],cells=[])
    doc['physical_tables'].append(child);doc['table_structure'].append({'table_id':'child'})
    (folder/'document.json').write_text(json.dumps(doc),encoding='utf8')
    link=dict(parent_cell_id='native-C',parent_native_table_id='native-T',page_number=1,child_table_ids=['child'])
    (folder/'nested.json').write_text(json.dumps({'cells':[link]}),encoding='utf8')
    first=error_basket.collect(root,folder,job,request)
    snap=error_basket.detail(root,first['id'])['snapshot']
    assert snap['nested_context']==[link]
    assert snap['nested_parent_identity'][0]['status']=='matched'
    assert snap['related_physical_tables'][0]['table_id']=='child'
    old=(root/first['id']/'snapshot.json').read_bytes()
    path=error_basket.supplement_context(root,first['id'],folder)
    assert path==error_basket.supplement_context(root,first['id'],folder)
    assert (root/first['id']/'snapshot.json').read_bytes()==old
    assert len(error_basket.detail(root,first['id'])['evidence_supplements'])==1


def test_same_box_without_span_identity_does_not_invent_parent():
    pass
    box=dict(x0=0,y0=0,x1=20,y1=20)
    doc=dict(document_ir={'pages':[dict(page_number=1,blocks=[{'spans':[dict(span_id='A',text_raw='a'),dict(span_id='B',text_raw='b')]}],
        tables=[dict(cells=[dict(cell_id='N',source={'bbox':box},span_refs=['A'])])])]},
        physical_tables=[dict(table_id='T',page_number=1,cells=[dict(cell_id='P',source_bbox=box,span_refs=['B'])])])
    result=evidence_context.parent_identities(doc,{'cells':[dict(parent_cell_id='N',page_number=1)]})
    assert result[0]['status']=='unresolved'
    assert not result[0]['candidates'][0]['identity_supported']
