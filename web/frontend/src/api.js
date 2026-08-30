export async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.status + ' ' + res.statusText;
    try {
      const d = await res.json();
      if (d && d.detail) detail = d.detail;
    } catch (e) { /* non-JSON error */ }
    throw new Error(detail);
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res.text();
}

export function apiJson(path, method, body) {
  return api(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
