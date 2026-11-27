// 역할: 화면을 그리는 작은 도구. 문서 내용은 항상 글자(textContent)로만 넣는다.
export const $ = id => document.getElementById(id);

// 비어 있는 자식(null·false)은 건너뛴다(DOM의 append는 null을 글자 "null"로 넣는다).
const clean = children => children.flat().filter(c => c !== null && c !== undefined && c !== false);

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null) continue;
    if (k === 'class') el.className = v; else if (k === 'data') Object.assign(el.dataset, v);
    else if (k in el) el[k] = v; else el.setAttribute(k, v);
  }
  el.append(...clean(children));
  return el;
}
export const fill = (el, ...children) => { el.replaceChildren(...clean(children)); return el; };
export const add = (el, ...children) => { el.append(...clean(children)); return el; };

// index.html에 정의해 둔 아이콘(#i-이름)을 쓴다.
export function icon(name) {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg'); svg.setAttribute('class', 'icon'); svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(NS, 'use'); use.setAttribute('href', '#i-' + name); svg.append(use);
  return svg;
}
export const one = text => (text || '').replace(/\s+/g, ' ').trim();
export const sleep = ms => new Promise(r => setTimeout(r, ms));
export const store = (area, key, fallback) => { try { return JSON.parse(area.getItem(key)) || fallback; } catch (_) { return fallback; } };
export const keep = (area, key, value) => { try { area.setItem(key, JSON.stringify(value)); } catch (_) { /* 저장할 수 없는 환경 */ } };
