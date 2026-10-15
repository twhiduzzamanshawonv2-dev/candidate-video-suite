// Streams a remote video through Vercel so the browser can download links
// whose servers don't send CORS headers. Only callable from this site.
export const config = { runtime: 'edge' };

const PRIVATE_HOST = /^(localhost|.*\.local|.*\.internal|0\.0\.0\.0|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|169\.254\.\d+\.\d+|\[.*\])$/i;

function parseTarget(raw) {
  let u;
  try { u = new URL(raw); } catch { return null; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
  if (PRIVATE_HOST.test(u.hostname)) return null;
  return u;
}

const fail = (status, msg) => new Response(msg, { status, headers: { 'Cache-Control': 'no-store' } });

export default async function handler(req) {
  if (req.headers.get('sec-fetch-site') !== 'same-origin') return fail(403, 'Forbidden');

  let url = parseTarget(new URL(req.url).searchParams.get('url') || '');
  if (!url) return fail(400, 'Invalid or disallowed URL');

  let upstream;
  for (let hop = 0; hop < 5; hop++) {
    try { upstream = await fetch(url, { redirect: 'manual' }); }
    catch { return fail(502, 'Could not reach the source server'); }
    if (upstream.status >= 300 && upstream.status < 400 && upstream.headers.get('location')) {
      url = parseTarget(new URL(upstream.headers.get('location'), url).href);
      if (!url) return fail(400, 'Redirect to a disallowed URL');
      continue;
    }
    break;
  }
  if (!upstream.ok) return fail(upstream.status === 404 ? 404 : 502, 'Source returned HTTP ' + upstream.status);

  const headers = new Headers({ 'Cache-Control': 'no-store' });
  headers.set('Content-Type', upstream.headers.get('content-type') || 'application/octet-stream');
  const len = upstream.headers.get('content-length');
  if (len) headers.set('Content-Length', len);
  return new Response(upstream.body, { status: 200, headers });
}
