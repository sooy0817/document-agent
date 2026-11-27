// 역할: 답변마다 문제(답변 오류·근거 누락·잘못된 인용)를 기록한다. 검토 전 의견으로만 저장되며 정답 데이터를 바꾸지 않는다.
import {h, add, fill} from './dom.js';
import {call, post} from './api.js';
import {say} from './config.js';

const KINDS = [['answer_error', '답변 오류'], ['missing_evidence', '근거 누락'], ['wrong_citation', '잘못된 인용']];

export function feedbackBox(task, used) {
  const checks = KINDS.map(([value, label]) => [value, h('input', {type: 'checkbox', value}), label]);
  const cited = used.map((e, i) => [e.evidence_id, h('input', {type: 'checkbox', value: e.evidence_id}), '인용 ' + (i + 1)]);
  const comment = h('input', {type: 'text', maxLength: 2000, placeholder: '무엇이 문제인지 적어 주세요 (선택)', 'aria-label': '의견'});
  const message = h('span', {class: 'muted', role: 'status'});
  const list = h('div', {});
  const summary = h('summary', {}, '문제 기록');

  async function load() {
    try {
      const found = await call('/api/search/feedback?request_id=' + task.request_id);
      summary.textContent = found.items.length ? '문제 기록 (' + found.items.length + '건 기록됨)' : '문제 기록';
      fill(list, found.items.map(i => h('p', {class: 'hint'}, '기록됨 · ' + i.labels.join(', ') + (i.evidence_ids.length ? ' · 표시한 인용 ' + i.evidence_ids.length + '개' : '') + (i.comment ? ' · ' + i.comment : '')
        + ' · ' + (i.review.status === 'unreviewed' ? '검토 전 의견' : '검토됨'))));
    } catch (_) { /* 목록은 보조 정보 */ }
  }
  const send = h('button', {class: 'btn small', onclick: async () => {
    const picked = checks.filter(c => c[1].checked).map(c => c[0]);
    if (!picked.length) { message.textContent = '문제 종류를 하나 이상 고르세요.'; return; }
    send.disabled = true;
    try {
      await post('/api/search/feedback', {request_id: task.request_id, kinds: picked, comment: comment.value.trim() || null, evidence_ids: cited.filter(c => c[1].checked).map(c => c[0])});
      [...checks, ...cited].forEach(c => { c[1].checked = false; }); comment.value = '';
      message.textContent = '기록했습니다.'; await load();
    } catch (error) { message.textContent = '기록하지 못했습니다: ' + say(error.message); } finally { send.disabled = false; }
  }}, '기록');

  const body = add(h('div', {class: 'body'}),
    h('div', {class: 'row'}, checks.map(c => h('label', {}, c[1], ' ' + c[2]))),
    cited.length ? h('div', {class: 'row'}, h('span', {class: 'muted'}, '문제가 있는 인용'), cited.map(c => h('label', {}, c[1], ' ' + c[2]))) : null,
    h('div', {class: 'row', style: 'flex-wrap:nowrap'}, comment, send),
    message, h('p', {class: 'hint', style: 'margin:0'}, '검토 전 의견으로만 저장됩니다. 정답 데이터나 분석 결과를 자동으로 바꾸지 않습니다.'), list);
  load();
  return h('details', {class: 'feedback more'}, summary, body);
}
