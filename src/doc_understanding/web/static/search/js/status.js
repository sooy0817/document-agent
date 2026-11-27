// 역할: 머리의 "검색 가능한 문서 수"와 실행 중인 작업 상태. 바뀌면 'corpus-status' 이벤트로 알린다.
import {$} from './dom.js';
import {call} from './api.js';
import {say} from './config.js';

export const corpus = {status: null};

export async function refreshStatus() {
  try {
    const s = corpus.status = await call('/api/search/status');
    const notes = ['검색 가능한 문서 ' + s.documents + '개'];
    if (s.busy_ingest) notes.push('문서 추가 처리 중');
    if (!s.api_key_available) notes.push('서버에 API 키가 없어 질문할 수 없습니다');
    $('corpus-line').textContent = notes.join(' · ');
    // 개발 정보(색인 버전 등)는 화면에 내놓지 않고 도움말 풍선에만 둔다.
    $('corpus-line').title = s.pages + '쪽 · 표 ' + s.details.tables + '개 · 검색 항목 ' + s.details.entries + '개 · 임베딩 ' + s.details.vectors + '개 · 색인 ' + s.details.index_version
      + (s.stale_documents ? ' · 예전 색인이라 검색되지 않는 문서 ' + s.stale_documents + '개' : '');
    $('docs-count').textContent = s.documents;
  } catch (error) { $('corpus-line').textContent = '문서 상태를 읽지 못했습니다: ' + say(error.message); }
  document.dispatchEvent(new CustomEvent('corpus-status'));
}
