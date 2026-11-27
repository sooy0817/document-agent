// 역할: 검색·답변 화면의 시작점. 탭(질문 / 문서)을 바꾸고 각 모듈을 시작한다.
//   dom·api·config: 공용 도구   status: 문서 수   scope·ask·answer·feedback: 질문과 답변
//   viewer·grid·evidence: 원문 패널   documents·reader: 문서 목록과 문서 보기 팝업   uploads: 문서 추가 팝업
import {$} from './dom.js';
import {refreshStatus} from './status.js';
import {initScope} from './scope.js';
import {initAsk, showAskError} from './ask.js';
import {initViewer} from './viewer.js';
import {initDocuments, showDocuments} from './documents.js';
import {initReader} from './reader.js';
import {initUploads} from './uploads.js';

function show(name) {
  const docs = name === 'docs';
  $('view-ask').hidden = docs; $('view-docs').hidden = !docs;
  $('tab-ask').setAttribute('aria-selected', String(!docs)); $('tab-docs').setAttribute('aria-selected', String(docs));
  if (docs) showDocuments();
}
const fromHash = () => show(location.hash === '#docs' ? 'docs' : 'ask');

$('tab-ask').onclick = () => { history.replaceState(null, '', '#ask'); show('ask'); };
$('tab-docs').onclick = () => { history.replaceState(null, '', '#docs'); show('docs'); };
window.addEventListener('hashchange', fromHash);

initScope(error => showAskError(error.message));
initViewer();
initReader();
initDocuments();
initUploads();
initAsk();
refreshStatus(); setInterval(refreshStatus, 5000);
fromHash();
