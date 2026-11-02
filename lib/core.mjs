import { readFile, mkdir, open, rename, unlink } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

export class SampleError extends Error {}
export function requireValue(condition, message) {
  if (!condition) throw new SampleError(message);
}
export async function config() {
  return JSON.parse(await readFile(new URL('../config.json', import.meta.url), 'utf8'));
}
export function canonicalURL(value, kind, cfg) {
  requireValue(typeof value === 'string' && value.length <= 2048 && !/[\s\x00-\x1f\x7f]/u.test(value), 'Source URL must be at most 2048 characters, without whitespace/control characters.');
  let u;
  try { u = new URL(value); } catch { throw new SampleError('Invalid source URL.'); }
  const host = u.hostname.replace(/^www\./, '');
  requireValue(u.protocol === 'https:' && !u.username && !u.password && !u.port && !u.hash && cfg.domains.includes(host), 'Use HTTPS on an exact configured marketplace hostname, without credentials, port or fragment.');
  const outputHost = cfg.brand === 'walmart' ? `www.${host}` : host;
  let path;
  if (kind === 'product') {
    const match = cfg.brand === 'amazon'
      ? u.pathname.match(/^\/(?:dp|gp\/product)\/([A-Za-z0-9]{10})\/?$/)
      : u.pathname.match(/^\/ip\/([A-Za-z0-9_-]{1,400})\/([1-9][0-9]{0,19})$/);
    requireValue(match, 'Use a supported product path.');
    requireValue(cfg.brand !== 'walmart' || !u.search, 'Walmart product URLs must not contain query parameters.');
    path = cfg.brand === 'amazon' ? `/dp/${match[1].toUpperCase()}` : u.pathname;
    return `https://${outputHost}${path}`;
  }
  const key = cfg.brand === 'amazon' ? 'k' : 'q';
  requireValue(u.pathname === (cfg.brand === 'amazon' ? '/s' : '/search') && [...u.searchParams.keys()].length === 1 && u.searchParams.getAll(key).length === 1 && u.searchParams.get(key).trim().length > 0, 'Use one search query only; filters and source pagination are not allowed.');
  const query = u.searchParams.get(key).trim();
  requireValue([...query].length <= 200 && !/[\x00-\x1f\x7f\uFFFD]/u.test(query), 'Search query exceeds 200 characters or contains control characters.');
  return `https://${outputHost}${u.pathname}?${new URLSearchParams({ [key]: query })}`;
}
export function productIdentity(value, cfg) {
  const canonical = canonicalURL(value, 'product', cfg);
  return cfg.brand === 'walmart' ? canonical.replace(/\/ip\/[^/]+\//, '/ip/') : canonical;
}
export async function readJSON(path) {
  try { return JSON.parse(await readFile(path, 'utf8')); }
  catch (e) { if (e.code === 'ENOENT') return null; throw new SampleError('Cannot read local JSON state; preserve it for recovery.'); }
}
async function syncDirectory(path) {
  const directory = await open(dirname(path), 'r');
  try { await directory.sync(); } finally { await directory.close(); }
}
export async function atomicWrite(path, value, json = true) {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const temp = `${path}.${randomUUID()}.tmp`;
  const file = await open(temp, 'wx', 0o600);
  try {
    await file.writeFile(json ? `${JSON.stringify(value, null, 2)}\n` : value);
    await file.sync();
  } finally { await file.close(); }
  try { await rename(temp, path); await syncDirectory(path); }
  finally { await unlink(temp).catch(() => {}); }
}
export async function exclusiveJSON(path, value) {
  const f = await open(path, 'wx', 0o600);
  try { await f.writeFile(`${JSON.stringify(value, null, 2)}\n`); await f.sync(); }
  finally { await f.close(); }
  await syncDirectory(path);
}
// shortcut: killed processes leave locks; use OS-managed locks for unattended crash recovery.
export async function withLock(path, fn) {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  let f;
  try { f = await open(path, 'wx', 0o600); }
  catch { throw new SampleError('Local state is locked. Stop other runs; after a crashed process, remove the .lock file manually and resume the SAME run directory.'); }
  try { return await fn(); }
  finally { await f.close(); await unlink(path); }
}
export function clock({ now = Date.now, sleep = ms => new Promise(r => setTimeout(r, ms)), timeout = 600_000 } = {}) {
  const end = now() + Math.min(timeout, 600_000);
  function remaining() {
    const ms = end - now();
    requireValue(ms > 0, 'Ten-minute wait budget exhausted. Resume the SAME run directory.');
    return ms;
  }
  return {
    remaining,
    now,
    async wait(ms) {
      requireValue(Number.isFinite(ms) && ms >= 0 && ms < remaining(), 'Wait exceeds ten-minute budget. Resume the SAME run directory later.');
      await this.bound(() => sleep(ms));
    },
    async bound(fn) {
      const controller = new AbortController();
      let timer;
      try {
        return await Promise.race([
          Promise.resolve().then(() => fn(controller.signal)),
          new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new SampleError('Network/wait timeout. Resume the SAME run directory.')); }, remaining()); }),
        ]);
      } finally { clearTimeout(timer); }
    },
  };
}
export function retryDelay(value, now = Date.now()) {
  if (!value) return 0;
  const seconds = /^\d+(?:\.\d+)?$/.test(value) ? Number(value) : (Date.parse(value) - now) / 1000;
  requireValue(Number.isFinite(seconds), 'Invalid Retry-After header.');
  return Math.max(0, seconds * 1000);
}
async function responseText(response, requestID) {
  if (!response.headers.get('content-type')?.includes('text/event-stream') || !response.body || requestID === undefined) return response.text();
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let text = '', scanned = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      text = (text + decoder.decode(value, { stream: !done })).replace(/\r\n/g, '\n');
      let boundary;
      while ((boundary = text.indexOf('\n\n', scanned)) !== -1) {
        const block = text.slice(scanned, boundary);
        scanned = boundary + 2;
        const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        if (data) {
          let message;
          try { message = JSON.parse(data); } catch { throw new SampleError('Malformed MCP SSE data.'); }
          if (message.id === requestID) return text.slice(0, scanned);
        }
      }
      if (done) return text;
    }
  } finally { await reader.cancel().catch(() => {}); }
}
export function http({ key = process.env.DATAWEBOT_API_KEY, fetcher = fetch, timer = clock() } = {}) {
  requireValue(typeof key === 'string' && key.trim().length > 0 && !/[\r\n]/.test(key), 'Set DATAWEBOT_API_KEY in your environment.');
  return async (url, { method = 'GET', body, headers = {} } = {}) => {
    const u = new URL(url);
    requireValue((u.origin === 'https://api.datawebot.com' && u.pathname.startsWith('/v1/requests')) || (u.origin === 'https://mcp.datawebot.com' && u.pathname === '/mcp'), 'Refusing non-public API destination.');
    return timer.bound(async signal => {
      let response;
      try {
        response = await fetcher(url, { method, headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json', ...headers }, body: body === undefined ? undefined : JSON.stringify(body), redirect: 'error', signal });
      } catch { throw new SampleError('Transport failed; submission may be ambiguous. Resume the SAME run directory with its unchanged checkpoint/key.'); }
      const delay = retryDelay(response.headers.get('retry-after'), timer.now());
      if (!response.ok) {
        const error = new SampleError(`Public API HTTP ${response.status}. Retry-After milliseconds: ${delay}. Preserve checkpoint; resume the SAME run directory, never create a replacement run to retry.`);
        error.retryAt = timer.now() + delay;
        throw error;
      }
      const text = await responseText(response, body?.id);
      return { text, headers: response.headers, delay };
    });
  };
}
export const validID = id => typeof id === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(id);
export function answerPage(raw, mode = 'rest', requestID) {
  if (mode !== 'mcp') return raw;
  requireValue(raw?.data && typeof raw.data === 'object' && !Array.isArray(raw.data), 'Malformed MCP data envelope.');
  return { ...raw, ...raw.data, meta: raw.meta, status: raw.data.status ?? 'done', request_id: raw.data.request_id ?? requestID ?? null, error: raw.error ?? raw.data.error, errors: raw.errors ?? raw.data.errors, partial: raw.partial ?? raw.data.partial };
}
function pageOK(page, kind, mode = 'rest') {
  requireValue(page && typeof page === 'object' && (validID(page.request_id) || (mode === 'mcp' && page.status === 'done' && page.request_id === null && page.next_cursor == null)), 'Malformed request ID/result.');
  const issues = value => Array.isArray(value) ? value.length > 0 : Boolean(value);
  requireValue(![page.error, page.errors, page.partial, page.partial_complete, page.meta?.partial, page.meta?.errors, page.meta?.error, page.meta?.partial_complete].some(issues), 'Failed/partial extraction; downstream output was not updated.');
  requireValue(['working', 'done'].includes(page.status), 'Extraction failed or returned unsupported/partial status.');
  if (page.status === 'working') return;
  requireValue(page.next_cursor === undefined || page.next_cursor === null || (typeof page.next_cursor === 'string' && page.next_cursor.length > 0), 'Malformed result cursor.');
  requireValue((Array.isArray(page.products) && page.products.every(p => p && typeof p === 'object' && !Array.isArray(p))) || (kind === 'product' && Object.hasOwn(page, 'product') && (page.product === null || (typeof page.product === 'object' && !Array.isArray(page.product)))), 'Malformed final records.');
}
export function restTransport(send) {
  return {
    async submit(payload) { return parse(await send('https://api.datawebot.com/v1/requests', { method: 'POST', body: payload })); },
    async get(id, cursor) { return parse(await send(`https://api.datawebot.com/v1/requests/${id}${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`)); },
  };
}
function parse(result) {
  try { return { page: JSON.parse(result.text), delay: result.delay }; }
  catch { throw new SampleError('Malformed JSON response; preserve checkpoint and resume.'); }
}
export async function extract({ cfg, kind, url, runDir, identity = {}, mode = 'rest', transport, timer = clock(), key, fetcher }) {
  const source = canonicalURL(url, kind, cfg);
  runDir = resolve(runDir);
  await mkdir(runDir, { recursive: true, mode: 0o700 });
  return withLock(join(runDir, 'checkpoint-node.lock'), async () => {
    const checkpoint = join(runDir, 'checkpoint-node.json');
    const input = { mode, kind, source, identity };
    let state = await readJSON(checkpoint);
    const fields = { ...(mode === 'mcp' ? {} : { capability: 'endpoint' }), endpoint: kind === 'product' ? cfg.product_endpoint : cfg.search_endpoint, url: source, format: 'structured', max_pages: 1, max_credits: kind === 'product' ? 1 : 5 };
    if (!state) {
      state = { input, payload: { ...fields, request_key: randomUUID() } };
      await exclusiveJSON(checkpoint, state);
    }
    requireValue(JSON.stringify(state.input) === JSON.stringify(input) && JSON.stringify({ ...state.payload, request_key: undefined }) === JSON.stringify({ ...fields, request_key: undefined }) && /^[a-zA-Z0-9_-]{8,128}$/.test(state.payload.request_key), 'Run inputs changed. Do not edit/reuse this checkpoint for different inputs.');
    if (state.request_id) requireValue(validID(state.request_id), 'Invalid saved request ID.');
    if (state.complete) {
      const pages = await readJSON(join(runDir, 'pages-node.json'));
      requireValue(Array.isArray(pages) && pages.length > 0 && (state.request_id || mode === 'mcp'), 'Completed cache is missing/corrupt; preserve checkpoint.');
      for (const raw of pages) { const p = answerPage(raw, mode, state.request_id); pageOK(p, kind, mode); requireValue(p.status === 'done' && (p.request_id ?? null) === (state.request_id ?? null), 'Invalid completed cache.'); }
      return pages;
    }
    try {
      transport ??= restTransport(http({ key, fetcher, timer }));
      if (state.next_poll_at > timer.now()) await timer.wait(state.next_poll_at - timer.now());
      let { page: raw, delay = 0 } = state.request_id ? await transport.get(state.request_id) : await transport.submit(state.payload);
      let page = answerPage(raw, mode, state.request_id);
      // Save a valid ID before inspecting status or attempting any poll.
      if (validID(page?.request_id) && !state.request_id) {
        state.request_id = page.request_id;
        const hint = page.status === 'working' && Number.isFinite(page.check_after_seconds) && page.check_after_seconds >= 0 ? page.check_after_seconds * 1000 : 0;
        state.next_poll_at = timer.now() + Math.max(delay, hint, page.status === 'working' ? 2000 : 0);
        await atomicWrite(checkpoint, state);
      }
      const pages = [], cursors = new Set();
      let cursor;
      for (;;) {
        timer.remaining();
        pageOK(page, kind, mode);
        requireValue((page.request_id ?? null) === (state.request_id ?? null), 'Response request ID changed.');
        if (page.status === 'working') {
          requireValue(page.check_after_seconds === undefined || (Number.isFinite(page.check_after_seconds) && page.check_after_seconds >= 0), 'Malformed polling interval.');
          const wait = Math.max(2000, (page.check_after_seconds ?? 2) * 1000, delay);
          state.next_poll_at = timer.now() + wait;
          await atomicWrite(checkpoint, state);
          await timer.wait(wait);
        } else {
          pages.push(raw);
          cursor = page.next_cursor;
          if (cursor == null) break;
          requireValue(!cursors.has(cursor), 'Repeated result cursor; refusing an infinite loop.');
          cursors.add(cursor);
          if (delay) {
            state.next_poll_at = timer.now() + delay;
            await atomicWrite(checkpoint, state);
            await timer.wait(delay);
          }
        }
        ({ page: raw, delay = 0 } = await transport.get(state.request_id, cursor));
        page = answerPage(raw, mode, state.request_id);
      }
      await atomicWrite(join(runDir, 'pages-node.json'), pages);
      state.complete = true;
      await atomicWrite(checkpoint, state);
      return pages;
    } catch (error) {
      if (Number.isFinite(error.retryAt)) {
        state.next_poll_at = error.retryAt;
        await atomicWrite(checkpoint, state);
      }
      const message = error instanceof SampleError ? error.message : 'Local/transport operation failed; response details are not logged.';
      throw new SampleError(`${message} Recovery: preserve ${checkpoint}; rerun with unchanged inputs. Request ID: ${state.request_id ?? 'not yet known; replay uses the saved request_key'}.`);
    }
  });
}
export async function cli(fn) {
  try { await fn(process.argv.slice(2)); }
  catch (error) {
    console.error(error instanceof SampleError ? error.message : 'Local or protocol operation failed. Preserve checkpoint-node.json; resume the SAME run directory. No response body is logged.');
    process.exitCode = 1;
  }
}
