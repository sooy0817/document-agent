// 역할: 서버 호출. 쓰기 요청에는 화면 토큰을 붙인다.
export const rid = () => crypto.randomUUID().replaceAll('-', '');
export const tokenHeader = () => ({'X-Demo-Token': window.token});

export async function call(path, options) {
  const response = await fetch(path, options);
  let value = null;
  try { value = await response.json(); } catch (_) { /* 본문이 JSON이 아닌 오류 */ }
  if (!response.ok) {
    const detail = value && value.detail;
    const error = Error(response.status === 404 && detail === 'Not Found' ? '새 기능을 쓰려면 서버를 재시작하세요.' : typeof detail === 'string' ? detail : '요청 오류 ' + response.status);
    error.status = response.status; error.code = typeof detail === 'string' ? detail : null;
    throw error;
  }
  return value;
}
export const post = (path, body) => call(path, {method: 'POST', headers: {'Content-Type': 'application/json', ...tokenHeader()}, body: JSON.stringify(body)});
