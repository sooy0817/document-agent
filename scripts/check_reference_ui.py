"""Opt-in local UI smoke check in an isolated copy; no model or user-basket writes."""
from doc_understanding.review import error_basket
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'work/reference-ui-tools')]
from playwright.sync_api import sync_playwright


def main():
    out=ROOT/'reports/human-reference-20261103'/sys.argv[1]
    out.mkdir(exist_ok=False)
    data=out/'data';jid='f'*32;folder=data/jid;folder.mkdir(parents=True)
    original=ROOT/'work/document-explorer/461ac7b1eff94125b6549ad3a64246a9'
    for name in ['input.pdf','document.json','result.json','nested.json']:
        shutil.copyfile(original/name,folder/name)
    (folder/'review.json').write_text(json.dumps(dict(name='UI 검증용 복사 · LH',cases=[])),encoding='utf8')
    doc=json.loads((folder/'document.json').read_text(encoding='utf8'))
    source_table=next(t for t in doc['physical_tables'] if t['page_number']==23)
    source=next(c for c in source_table['cells'] if (c.get('original_text') or '').strip()=='입주안내')
    target=next(t for t in doc['physical_tables'] if t['page_number']==24)['cells'][0]
    item=error_basket.collect(data/'error-basket',folder,dict(id=jid,name='UI 검증용 복사',pages=30,status='done'),
        error_basket.CollectionRequest(page=23,scope='cell',table_id=source_table['table_id'],cell_id=source['cell_id'],error_type='column_path'))
    initial=(data/'error-basket'/item['id']/'snapshot.json').read_bytes()
    server_code="import asyncio;asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy());from pathlib import Path;from doc_understanding.web import app as server;import uvicorn;server.DATA=Path("+repr(str(data))+ ");server.recover_jobs();uvicorn.run(server.app,host='127.0.0.1',port=8767,access_log=False,loop='asyncio')"
    log=(out/'server.log').open('w',encoding='utf8')
    proc=subprocess.Popen([sys.executable,'-c',server_code],cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        for _ in range(100):
            if 'Uvicorn running' in (out/'server.log').read_text(encoding='utf8',errors='replace'):break
            if proc.poll() is not None:raise RuntimeError('Test server exited; inspect server.log')
            time.sleep(.2)
        else:raise RuntimeError('Test server startup timed out')
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(viewport={'width':1500,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith('http://127.0.0.1:8767/') else route.abort())
            page.goto('http://127.0.0.1:8767/?job='+jid+'&page=23')
            page.locator('[data-tab="structure"]').click()
            page.locator('[data-cell="'+source['cell_id']+'"]').click()
            page.locator('#collect-cell').click()
            page.locator('#basket-note').fill('이전 메모를 다시 입력하지 않아도 됨')
            page.locator('#basket-save').click()
            page.wait_for_function("document.getElementById('basket-message').textContent.startsWith('메모와 원본을 저장했습니다')")
            assert not page.locator('#reference-editor').is_visible()
            page.get_by_role('button',name='연결까지 지정하기 (선택)',exact=True).click()
            page.locator('#reference-editor').wait_for(state='visible')
            assert page.locator('#ref-original-note').inner_text()=='이전 메모를 다시 입력하지 않아도 됨'
            assert page.locator('#ref-reason').input_value()=='이전 메모를 다시 입력하지 않아도 됨'
            assert '입주안내' in page.locator('#ref-from-label').inner_text()
            page.locator('[data-ref-box="'+source['cell_id']+'"]').click()
            page.locator('#ref-from').click()
            page.locator('#ref-observed').select_option('column_header')
            page.locator('#ref-expected').select_option('row_header')
            page.locator('#ref-proof').click()
            page.locator('#ref-reason').fill('왼쪽 항목 셀이며 열 전체의 제목이 아님')
            page.locator('#reference-editor').evaluate('(el)=>el.scrollTop=0')
            page.screenshot(path=str(out/'editor-desktop.png'))
            page.set_viewport_size({'width':780,'height':900})
            page.locator('#reference-editor').evaluate('(el)=>el.scrollTop=0')
            page.screenshot(path=str(out/'editor-narrow.png'))
            page.set_viewport_size({'width':1500,'height':1000})
            page.locator('#ref-stage').click()
            page.locator('#ref-claim-kind').select_option('row_group')
            page.locator('#ref-from').click()
            page.locator('#ref-page').select_option('24')
            page.locator('#ref-sources').select_option(target['cell_id'])
            page.locator('#ref-to').click();page.locator('#ref-proof').click()
            page.locator('#ref-relation').select_option('no_link')
            page.locator('#ref-reason').fill('다음 페이지에 별도 행 이름이 있어 이전 행을 상속하지 않음')
            page.locator('#ref-stage').click()
            page.locator('#ref-certainty').select_option('uncertain')
            page.locator('#ref-reason').fill('기타 설명 범위는 아직 검토하지 않음')
            # Saving also stages the current unfinished claim.
            page.locator('#ref-save').click()
            page.wait_for_function("!document.getElementById('ref-save').disabled")
            assert page.locator('#ref-message').inner_text().startswith('저장했습니다'),page.locator('#ref-message').inner_text()
            page.locator('#ref-close').click()
            page.reload();page.locator('#basket-shelf > summary').click()
            page.locator('[data-reference-open]').click()
            page.locator('#ref-claims .ref-claim').nth(2).wait_for()
            page.locator('[data-ref-edit="0"]').click()
            assert page.locator('#ref-expected').input_value()=='row_header'
            page.locator('#ref-reason').fill('수정 이력 검증: 원본의 행 이름')
            page.locator('#ref-save').click()
            page.wait_for_function("document.getElementById('ref-message').textContent.startsWith('저장했습니다')")
            page.screenshot(path=str(out/'saved-result.png'))
            final=error_basket.detail(data/'error-basket',item['id'])
            assert len(final['reference_history'])==2 and len(final['reference']['claims'])==3
            assert final['reference']['claims'][1]['target_id']==target['cell_id']
            assert final['reference']['claims'][2]['certainty']=='uncertain'
            assert final['training_ready'] is False
            assert (data/'error-basket'/item['id']/'snapshot.json').read_bytes()==initial
            page.locator('#ref-close').click()
            page.locator('[data-tab="structure"]').click()
            page.locator('#basket-mode').select_option('table')
            page.locator('[data-cell="'+source['cell_id']+'"]').click()
            assert page.locator('#collect-cell').is_disabled()
            assert '전체' in page.locator('#basket-selection').inner_text()
            assert page.locator('#result [data-cell].selected').count()==len(source_table['cells'])
            page.locator('#collect-table').click()
            page.locator('#basket-note').fill('표 전체 범위 검증')
            page.locator('#basket-save').click()
            page.wait_for_function("document.getElementById('basket-message').textContent.startsWith('메모와 원본을 저장했습니다')")
            assert not page.locator('#reference-editor').is_visible()
            collected=error_basket.list_items(data/'error-basket')
            table_item=next(i for i in collected if i['scope']=='table')
            snap=error_basket.detail(data/'error-basket',table_item['id'])['snapshot']
            assert snap['selection']['cell_id'] is None
            assert len(snap['physical_tables'][0]['cells'])==len(source_table['cells'])
            page.screenshot(path=str(out/'whole-table.png'))
            assert not errors,errors
            summary=dict(status='passed',browser_errors=errors,revisions=2,claims=3,
                cross_page_no_link=True,uncertain_preserved=True,snapshot_unchanged=True,training_ready=False,
                no_automatic_dialog=True,memo_reused=True,whole_table_saved=True)
            (out/'result.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
            print(json.dumps(summary));browser.close()
    finally:
        proc.terminate();proc.wait(timeout=10);log.close()


if __name__=='__main__':main()
