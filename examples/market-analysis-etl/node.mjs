// Read one search page, collect its result cursors and write CSV.
import { mkdirSync, writeFileSync, renameSync } from 'node:fs';
import { join } from 'node:path';

try {
  if (process.argv.length !== 5) throw new Error('Usage: node.mjs SEARCH_URL REQUEST_KEY OUTPUT_DIR');
  process.umask(0o077);
  const [url, requestKey, outputDir] = process.argv.slice(2);
  const target = new URL(url), host = target.hostname.replace(/^www\./, '');
  const allowedHosts = ["walmart.com"];
  if (target.protocol !== 'https:' || !allowedHosts.includes(host) || target.username || target.password || target.port || target.hash || target.hostname !== 'www.walmart.com') throw new Error('Use an HTTPS Walmart URL without credentials, ports or fragments.');
  const queryKey = 'q';
  const identity = target.pathname === '/search' && [...target.searchParams.keys()].length === 1 && target.searchParams.get(queryKey)?.trim().length > 0 && target.searchParams.get(queryKey).length <= 200;
  if (!identity || !/^[A-Za-z0-9_-]{8,128}$/.test(requestKey)) throw new Error('Use the documented URL shape and a stable 8-128 character request key.');

  // Keep this key on retries. The server returns the same request instead of billing a new one.
  const payload = { capability: 'endpoint', endpoint: 'walmart.search',
    url, max_pages: 1, format: 'structured', max_credits: 5, request_key: requestKey };
  const deadline = Date.now() + 600_000;
  async function request(path = '', body) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new Error('Wait timed out; rerun with the SAME request key. Server work is not cancelled.');
    if (!process.env.DATAWEBOT_API_KEY) throw new Error('Set DATAWEBOT_API_KEY through your environment.');
    const response = await fetch('https://api.datawebot.com/v1/requests' + path, {
      method: body === undefined ? 'GET' : 'POST', redirect: 'error',
      headers: { Authorization: 'Bearer ' + process.env.DATAWEBOT_API_KEY, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(Math.min(30_000, remaining)),
    });
    if (!response.ok) throw new Error('API HTTP ' + response.status + '; wait Retry-After=' + (response.headers.get('retry-after') ?? 'not supplied') + ' before retrying with the SAME key.');
    try { return await response.json(); } catch { throw new Error('API returned invalid JSON; retry with the SAME key.'); }
  }

  // Submit once, then make read-only calls until the answer is ready.
  let answer = await request('', payload);
  const requestId = answer.request_id;
  if (typeof requestId !== 'string' || !/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(requestId)) throw new Error('Missing/invalid request ID; retry with the SAME key.');
  while (answer.status === 'working') {
    if (answer.request_id !== requestId) throw new Error('Request ID changed; no downstream changes.');
    const delay = Math.max(2, answer.check_after_seconds ?? 5);
    if (!Number.isFinite(delay) || Date.now() + delay * 1000 >= deadline) throw new Error('Wait timed out; rerun with the SAME key.');
    await new Promise(resolve => setTimeout(resolve, delay * 1000));
    answer = await request('/' + requestId);
  }
  if (answer.request_id !== requestId || answer.status !== 'done' || answer.error || answer.partial || answer.errors?.length || answer.meta?.partial || answer.meta?.errors?.length) throw new Error('Request failed or is partial; no downstream changes.');
  if (!Array.isArray(answer.products) || answer.products.some(p => !p || typeof p !== 'object' || Array.isArray(p))) throw new Error('Malformed products; no downstream changes.');

  // Collect existing result pages, not additional source pages.
  const pages = [], seen = new Set();
  while (true) {
    if (answer.request_id !== requestId || answer.status !== 'done' || answer.error || answer.partial || answer.errors?.length || answer.meta?.partial || answer.meta?.errors?.length) throw new Error('Failed/partial result page; CSV was not written.');
    if (!Array.isArray(answer.products) || answer.products.some(p => !p || typeof p !== 'object' || Array.isArray(p))) throw new Error('Malformed products; CSV was not written.');
    pages.push(answer);
    const cursor = answer.next_cursor;
    if (cursor == null) break;
    if (typeof cursor !== 'string' || !cursor || seen.has(cursor)) throw new Error('Invalid/repeated cursor; CSV was not written.');
    seen.add(cursor);
    answer = await request('/' + requestId + '?cursor=' + encodeURIComponent(cursor));
  }
  mkdirSync(outputDir, { recursive: true, mode: 0o700 });
  writeFileSync(join(outputDir, 'pages-node.json'), JSON.stringify(pages, null, 2), { mode: 0o600 });
  function csvCell(value) {
    let text = value == null ? '' : String(value);
    if (/^\s*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  }
  const rows = [['url', 'title', 'price', 'currency', 'availability', 'as_of', 'request_id', 'country']];
  for (const page of pages) for (const product of page.products) {
    const price = product.price ?? {}, meta = page.meta ?? {};
    if (price.amount != null && (typeof price.amount !== 'number' || !Number.isFinite(price.amount) || price.amount < 0)) throw new Error('Malformed price; use the archived JSON to investigate.');
    rows.push([product.url, product.title, price.amount, price.currency, product.availability, meta.as_of, page.request_id, meta.country]);
  }
  writeFileSync(join(outputDir, 'products-node.csv.tmp'), rows.map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n', { mode: 0o600 });
  renameSync(join(outputDir, 'products-node.csv.tmp'), join(outputDir, 'products-node.csv'));
  console.log('Saved pages-node.json and products-node.csv');
} catch (error) {
  console.error(error instanceof TypeError || error instanceof SyntaxError ? 'Invalid input or network response; no new key on retries.' : error.message);
  process.exitCode = 1;
}
