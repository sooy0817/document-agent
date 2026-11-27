// 역할: 문서 보기 팝업. 문서를 쪽 단위로 넘겨 본다(양옆 버튼, 좌우 화살표 키, 쪽 번호 입력). HTML 원문은 격리된 틀에 통째로 보여 준다.
import {$, h} from './dom.js';
import {call} from './api.js';
import {REASONS, say} from './config.js';

const BASE_WIDTH = 860;  // 100%일 때 쪽 그림 너비(px)
let doc = null, page = 1, token = 0;

const stage = () => $('reader-stage');
const imageUrl = number => '/api/search/source/page?run_id=' + encodeURIComponent(doc.run_id) + '&number=' + number;
const width = () => Math.round(BASE_WIDTH * $('reader-zoom').value / 100) + 'px';

// 새 쪽을 옆에서 밀어 넣고 이전 쪽은 반대쪽으로 내보낸다. direction: 1(다음), -1(이전), 0(그냥 바꿈).
function swap(slide, direction) {
  const old = [...stage().querySelectorAll('.slide')];
  if (direction) slide.classList.add(direction > 0 ? 'from-right' : 'from-left');
  stage().append(slide);
  if (direction) { slide.getBoundingClientRect(); slide.classList.remove('from-right', 'from-left'); }
  for (const s of old) {
    if (!direction) { s.remove(); continue; }
    s.classList.add(direction > 0 ? 'to-left' : 'to-right');
    setTimeout(() => s.remove(), 280);
  }
}

function tools(paged) {
  $('reader-tools').hidden = !paged;
  $('reader-prev').hidden = $('reader-next').hidden = !paged;
  if (!paged) return;
  $('reader-page').value = page; $('reader-page').max = doc.pages;
  $('reader-total').textContent = '/ ' + doc.pages + '쪽';
  $('reader-prev').disabled = page <= 1; $('reader-next').disabled = page >= doc.pages;
}

async function show(direction) {
  const mine = ++token;
  let info;
  try { info = await call('/api/search/source?run_id=' + encodeURIComponent(doc.run_id) + '&page=' + page); }
  catch (error) { if (mine === token) { tools(false); swap(h('div', {class: 'slide'}, h('div', {class: 'notice error'}, '문서를 열지 못했습니다: ' + say(error.message))), 0); } return; }
  if (mine !== token) return;
  if (info.kind === 'pdf' && info.image) {
    tools(true);
    swap(h('div', {class: 'slide'}, h('img', {alt: doc.name + ' ' + page + '쪽', src: info.image, style: 'width:' + width() + ';max-width:none'})), direction);
    // 앞뒤 쪽을 미리 받아 두어 넘길 때 끊기지 않게 한다.
    for (const n of [page + 1, page - 1]) if (n >= 1 && n <= doc.pages) new Image().src = imageUrl(n);
  } else if (info.kind === 'html') {
    tools(false);
    swap(h('div', {class: 'slide html'}, h('iframe', {title: doc.name + ' 원문', src: info.html, sandbox: ''})), 0);
  } else {
    tools(false);
    swap(h('div', {class: 'slide'}, h('div', {class: 'notice warn'}, info.page_found === false && info.available ? REASONS.PAGE_NOT_FOUND
      : '이 문서의 원문 파일을 찾지 못했습니다. 검색과 답변에는 색인된 내용이 쓰이지만, 여기서 원문을 넘겨 볼 수는 없습니다.')), 0);
  }
}

function go(next) {
  if (!doc) return;
  const target = Math.min(Math.max(1, next), doc.pages);
  if (target === page) { $('reader-page').value = page; return; }
  const direction = target > page ? 1 : -1;
  page = target; show(direction);
}

export function openReader(d) {
  doc = d; page = 1;
  $('reader-title').textContent = d.name; $('reader-title').title = d.name;
  stage().querySelectorAll('.slide').forEach(s => s.remove());
  $('reader-zoom').value = 100;
  tools(false);
  if (!$('reader').open) $('reader').showModal();
  show(0);
  $('reader-next').focus();
}

export function initReader() {
  const dialog = $('reader');
  $('reader-prev').onclick = () => go(page - 1);
  $('reader-next').onclick = () => go(page + 1);
  $('reader-close').onclick = () => dialog.close();
  $('reader-page').addEventListener('change', () => go(Number($('reader-page').value) || page));
  $('reader-zoom').oninput = () => stage().querySelectorAll('.slide img').forEach(img => { img.style.width = width(); });
  dialog.addEventListener('keydown', e => {
    if (e.target === $('reader-page') || e.target === $('reader-zoom') || $('reader-tools').hidden) return;
    if (e.key === 'ArrowRight' || e.key === 'PageDown') { e.preventDefault(); go(page + 1); }
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); go(page - 1); }
    if (e.key === 'Home') { e.preventDefault(); go(1); }
    if (e.key === 'End') { e.preventDefault(); go(doc.pages); }
  });
  dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });  // 바깥(어두운 부분)을 누르면 닫는다
  dialog.addEventListener('close', () => { doc = null; token++; stage().querySelectorAll('.slide').forEach(s => s.remove()); });
}
