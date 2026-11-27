from doc_understanding.web import app
from doc_understanding.review.presentation import document as document_view
from pathlib import Path
import pytest
from fastapi import HTTPException
from starlette.requests import Request

@pytest.mark.parametrize('kind',['png','jpeg'])
def test_image_input_preserves_pixels_without_claiming_ocr(kind):
    import fitz
    pass
    with fitz.open('reports/physical-fixtures/horizontal.pdf') as doc:
        image=doc[0].get_pixmap().tobytes(kind)
    converted,extension=app.normalize_upload(image)
    assert extension==kind
    with fitz.open(stream=converted,filetype='pdf') as result:
        assert len(result)==1
        assert not result[0].get_text().strip()
        assert result[0].get_images()

def test_invalid_image_rejected():
    pass
    for data,code in [(b'\x89PNG\r\n\x1a\nbroken',422),(b'<svg/>',415)]:
        with pytest.raises(HTTPException) as err:app.normalize_upload(data)
        assert err.value.status_code==code


@pytest.mark.parametrize('size', [(8001, 1), (4000, 3001)])
def test_image_dimensions_are_rejected_before_conversion(size):
    from io import BytesIO
    from PIL import Image

    stream = BytesIO()
    with Image.new('RGB', size) as image:
        image.save(stream, format='PNG')
    with pytest.raises(HTTPException) as error:
        app.normalize_upload(stream.getvalue())
    assert error.value.status_code == 413

def test_real_pdf():
    assert app.validate_pdf(Path('reports/physical-fixtures/horizontal.pdf').read_bytes())==1

@pytest.mark.parametrize('data,status',[(b'<html>fake.pdf</html>',415),(b'%PDF-broken',422),(b'%PDF-'+b'0'*(20*1024*1024),413)],ids=['not-pdf','corrupt','oversized'])
def test_invalid_uploads(data,status):
    with pytest.raises(HTTPException) as err:app.validate_pdf(data)
    assert err.value.status_code==status

from tests.http_helpers import request

def test_cross_origin_and_missing_token():
    for headers in ({},{'x-demo-token':app.TOKEN,'origin':'https://untrusted.example'}):
        with pytest.raises(HTTPException) as err:app.authorize(request(headers))
        assert err.value.status_code==403
    app.authorize(request({'x-demo-token':app.TOKEN,'origin':'http://127.0.0.1:8766'}))

def test_download_cannot_escape_job_directory():
    with pytest.raises(HTTPException) as err:app.download('../../src','document')
    assert err.value.status_code==404


def test_continuation_report_is_read_only_and_scoped(tmp_path,monkeypatch):
    pass
    jid='c'*32
    monkeypatch.setattr(app,'DATA',tmp_path)
    monkeypatch.setattr(app,'JOBS',{jid:{'review':{'continuation_report':True}},'plain':{'review':None}})
    folder=tmp_path/jid/'continuation';folder.mkdir(parents=True)
    (folder/'comparison.html').write_text('review',encoding='utf8')
    assert Path(app.continuation_report(jid,'comparison.html').path)==folder/'comparison.html'
    for job,asset in [(jid,'../document.json'),(jid,'.env'),('plain','comparison.html'),('unknown','comparison.html')]:
        with pytest.raises(HTTPException):app.continuation_report(job,asset)

def test_full_page_keeps_inner_table_inside_outer_cell():
    import json
    from html.parser import HTMLParser
    pass
    root=Path('outputs/upstage-benchmark-20261029/final-comparison/hana-nested')
    doc=json.loads((root/'native-document.json').read_text(encoding='utf8'))
    nested=json.loads((root/'native-nested-content.json').read_text(encoding='utf8'))
    view=document_view.page_view(doc,nested,1)
    class Depth(HTMLParser):
        def __init__(self):super().__init__();self.level=0;self.depths=[]
        def handle_starttag(self,tag,attrs):
            if tag=='table':self.level+=1;self.depths.append(self.level)
        def handle_endtag(self,tag):
            if tag=='table':self.level-=1
    parser=Depth();parser.feed(view['html'])
    assert parser.depths==[1,1,2]
    assert view['html'].count('약정이율 1/10')==1
    assert view['html'].count('약정이율 6/10')==1
    assert '현찰수수료' in view['html']
    assert view['top_level_tables']==2 and view['nested_tables']==1
    assert not view['unresolved_parent_links']


def test_saved_review_opens_without_rerunning_parser(tmp_path,monkeypatch):
    import json
    pass
    monkeypatch.setattr(app,'DATA',tmp_path)
    monkeypatch.setattr(app,'JOBS',{})
    monkeypatch.setattr(app,'worker',lambda *a:pytest.fail('Saved reviews must not run extraction'))
    folder=tmp_path/('a'*32);folder.mkdir()
    (folder/'result.json').write_text(json.dumps({'pages':[{'number':1}],'tables':[]}),encoding='utf8')
    (folder/'review.json').write_text(json.dumps({'name':'frozen','cases':[{'id':'T1'}]}),encoding='utf8')
    (folder/'assessment.json').write_text(json.dumps({'T1':{'source_verdict':'pending'}}),encoding='utf8')
    app.recover_jobs()
    assert app.status(folder.name)['review']['cases'][0]['assessment']['source_verdict']=='pending'
    assert app.reviews()[0]['id']==folder.name


def test_page_json_keeps_raw_and_canonical_structure_separate(tmp_path,monkeypatch):
    import json
    pass
    monkeypatch.setattr(app,'DATA',tmp_path)
    jid='b'*32;folder=tmp_path/jid;folder.mkdir();physical=tmp_path/'physical';physical.mkdir()
    doc={'document_id':'D','physical_tables':[{'table_id':'T','page_number':1}],
         'table_structure':[{'table_id':'T','analysis_state':'canonical_candidate'}],
         'document_ir':{'pages':[{'tables':[]}]}}
    (folder/'document.json').write_text(json.dumps(doc),encoding='utf8')
    (physical/'page-001-table-0-structured.json').write_text(json.dumps({'table_id':'T','analysis_state':'raw_candidate'}),encoding='utf8')
    monkeypatch.setattr(app,'JOBS',{jid:{'status':'done','pages':1,'review':{'extraction_run':str(tmp_path)}}})
    result=app.page_json(jid,1)
    assert result['raw_structured_tables'][0]['analysis_state']=='raw_candidate'
    assert result['canonical_table_structure'][0]['analysis_state']=='canonical_candidate'
    with pytest.raises(HTTPException):app.page_json(jid,2)
