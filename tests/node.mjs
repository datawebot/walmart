import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { answerPage, canonicalURL, clock, config, extract, http, readJSON, restTransport, retryDelay } from '../lib/core.mjs';
import { mcpClient, mcpTransport, rpcResponse, toolContent } from '../lib/mcp.mjs';
import { csvCell, exactProduct, inventoryOutput, inventoryUpdate, priceArgs, priceProposal, productsCSV, rows } from '../lib/use-cases.mjs';

const ID = '12345678-1234-1234-1234-123456789abc';
const OTHER_ID = '22345678-1234-1234-1234-123456789abc';
const AS_OF = '2026-10-27T12:00:00Z';
const configs = [
  { brand: 'amazon', domains: ['amazon.com', 'amazon.de'], search_endpoint: 'amazon.search_items', product_endpoint: 'amazon.product_details' },
  { brand: 'walmart', domains: ['walmart.com'], search_endpoint: 'walmart.search', product_endpoint: 'walmart.product_details' },
];
const urls = cfg => ({
  product: `https://${cfg.domains[0]}${cfg.brand === 'amazon' ? '/dp/B012345678' : '/ip/sample/12345'}`,
  search: `https://${cfg.domains[0]}${cfg.brand === 'amazon' ? '/s?k=coffee' : '/search?q=coffee'}`,
});
function record(cfg, extra = {}) {
  return { url: urls(cfg).product, title: 'Coffee', price: { amount: 10, currency: 'USD' }, availability: 'in_stock', ...extra };
}
function done(products = [], extra = {}) { return { request_id: ID, status: 'done', products, meta: { as_of: AS_OF, country: 'US' }, ...extra }; }
async function directory(t) {
  const path = await mkdtemp(join(tmpdir(), 'datawebot-node-'));
  t.after(() => rm(path, { recursive: true, force: true }));
  return path;
}
function fakeClock(timeout = 600_000) {
  let time = 0;
  const waits = [];
  const timer = clock({ timeout, now: () => time, sleep: async ms => { waits.push(ms); time += ms; } });
  return { timer, waits };
}

for (const cfg of configs) {
  test(`${cfg.brand}: URL scope and canonical product identity`, () => {
    const { product, search } = urls(cfg);
    const canonical = canonicalURL(product, 'product', cfg);
    const withWWW = product.replace('https://', 'https://www.');
    assert.equal(canonicalURL(cfg.brand === 'amazon' ? withWWW + '?tag=ignored' : withWWW, 'product', cfg), canonical);
    assert.equal(canonicalURL(search, 'search', cfg), cfg.brand === 'walmart' ? search.replace('https://', 'https://www.') : search);
    if (cfg.brand === 'walmart') {
      assert.equal(canonical, 'https://www.walmart.com/ip/sample/12345');
      for (const bad of [product + '?tag=x', product.replace('/sample/', '/'), product.replace('12345', '012345'), product.replace('sample', 'bad%20slug')]) assert.throws(() => canonicalURL(bad, 'product', cfg));
    }
    if (cfg.brand === 'amazon') assert.equal(canonicalURL(product.replace('/dp/', '/gp/product/'), 'product', cfg), canonical);
    for (const bad of [product.replace('https:', 'http:'), product.replace('https://', 'https://user:pass@'), product.replace(cfg.domains[0], `${cfg.domains[0]}.evil.test`), product + '#fragment', product.replace(cfg.domains[0], `${cfg.domains[0]}:8443`), product + '/other']) {
      assert.throws(() => canonicalURL(bad, 'product', cfg));
    }
    for (const bad of [search + '&page=2', search + '&sort=price', search + '&k=second', search.replace('coffee', ''), product]) assert.throws(() => canonicalURL(bad, 'search', cfg));
  });

  test(`${cfg.brand}: checkpoint precedes POST; cache, unchanged pages, owner-only files`, async t => {
    const runDir = await directory(t), { search } = urls(cfg);
    const page = done([record(cfg)]);
    let calls = 0;
    const transport = {
      async submit(payload) {
        calls++;
        assert.deepEqual((await readJSON(join(runDir, 'checkpoint-node.json'))).payload, payload);
        assert.equal(payload.max_pages, 1); assert.equal(payload.max_credits, 5);
        assert.equal(payload.endpoint, cfg.search_endpoint);
        if (cfg.brand === 'walmart') assert.equal(payload.url, 'https://www.walmart.com/search?q=coffee');
        return { page };
      },
      get() { assert.fail('Completed submission must not poll'); },
    };
    const args = { cfg, kind: 'search', url: search, runDir, transport };
    assert.deepEqual(await extract(args), [page]);
    assert.deepEqual(await readJSON(join(runDir, 'pages-node.json')), [page]);
    assert.deepEqual(await extract(args), [page]);
    assert.equal(calls, 1);
    for (const path of ['checkpoint-node.json', 'pages-node.json']) assert.equal((await stat(join(runDir, path))).mode & 0o777, 0o600);
    await assert.rejects(extract({ ...args, identity: { changed: true } }), /inputs changed/);
    await assert.rejects(extract({ ...args, url: search.replace('coffee', 'tea') }), /inputs changed/);
  });

  test(`${cfg.brand}: exact product proposal handles unknowns and mismatch`, () => {
    const source = canonicalURL(urls(cfg).product, 'product', cfg);
    const pages = [done([record(cfg)])];
    assert.equal(priceProposal(pages, source, 20, 'USD', cfg).action, 'review_proposal');
    assert.equal(priceProposal(pages, source, 5, 'USD', cfg).reason, 'no_lower_observed_price');
    assert.equal(priceProposal(pages, source, 20, 'EUR', cfg).reason, 'currency_mismatch');
    assert.equal(priceProposal([done([record(cfg, { price: null })])], source, 20, 'USD', cfg).reason, 'unknown_price');
    assert.equal(priceProposal([done([record(cfg, { price: { amount: 0, currency: null } })])], source, 20, 'USD', cfg).reason, 'unknown_currency');
    assert.equal(priceProposal([done([record(cfg)], { meta: {} })], source, 20, 'USD', cfg).reason, 'missing_as_of');
    assert.throws(() => exactProduct([done([])], source, cfg), /exactly one/);
    assert.throws(() => exactProduct([done([record(cfg), record(cfg)])], source, cfg), /exactly one/);
    assert.throws(() => exactProduct([done([record(cfg, { url: urls(cfg).product.replace(/.$/, '9') })])], source, cfg), /identity/);
  });
}

const cfg = configs[0];
function extraction(runDir, extra = {}) { return { cfg, kind: 'search', url: urls(cfg).search, runDir, ...extra }; }

test('ambiguous submission replays identical persisted payload/key, with no automatic retry', async t => {
  const runDir = await directory(t);
  let first, calls = 0;
  const transport = { async submit(payload) { calls++; first = structuredClone(payload); throw new Error('offline'); } };
  await assert.rejects(extract(extraction(runDir, { transport })));
  assert.equal(calls, 1);
  assert.equal((await readJSON(join(runDir, 'checkpoint-node.json'))).request_id, undefined);
  await extract(extraction(runDir, { transport: { async submit(payload) { assert.deepEqual(payload, first); return { page: done() }; } } }));
});

test('ID persisted before polling, resume GET only, bounded polling and all result cursors', async t => {
  const runDir = await directory(t), { timer, waits } = fakeClock();
  let gets = 0;
  await assert.rejects(extract(extraction(runDir, { timer, transport: {
    async submit() { return { page: { request_id: ID, status: 'working', check_after_seconds: 1 }, delay: 3000 }; },
    async get(id) { gets++; assert.equal(id, ID); assert.equal((await readJSON(join(runDir, 'checkpoint-node.json'))).request_id, ID); throw new Error('interrupted'); },
  } })));
  assert.equal(gets, 1); assert.deepEqual(waits, [3000]);
  const requested = [];
  const a = done([], { next_cursor: 'a/b?c' }), b = done([record(cfg)]);
  const pages = await extract(extraction(runDir, { transport: {
    submit() { assert.fail('Known ID must not resubmit'); },
    async get(id, cursor) { requested.push([id, cursor]); return { page: cursor ? b : a }; },
  } }));
  assert.deepEqual(requested, [[ID, undefined], [ID, 'a/b?c']]);
  assert.deepEqual(pages, [a, b]);
});

test('poll working again, minimum interval, result cursor retained while working', async t => {
  const runDir = await directory(t), { timer, waits } = fakeClock();
  const calls = [];
  await extract(extraction(runDir, { timer, transport: {
    async submit() { return { page: done([], { next_cursor: 'more' }) }; },
    async get(id, cursor) { calls.push(cursor); return { page: calls.length === 1 ? { request_id: id, status: 'working' } : done() }; },
  } }));
  assert.deepEqual(calls, ['more', 'more']); assert.deepEqual(waits, [2000]);
});

test('repeated cursor, changed IDs, malformed, failed and exposed partial results fail without complete output', async t => {
  for (const page of [done([], { status: 'failed', error: { message: 'PRIVATE RESPONSE' } }), done([], { partial: true }), done([], { meta: { errors: ['bad'] } }), done([], { status: 'PARTIAL_COMPLETE' }), done([], { request_id: 'bad' }), { status: 'done', request_id: ID }, done([], { next_cursor: 2 })]) {
    const runDir = await directory(t);
    await assert.rejects(extract(extraction(runDir, { transport: { async submit() { return { page }; } } })));
    assert.equal(await readJSON(join(runDir, 'pages-node.json')), null);
    assert.ok(!(await readJSON(join(runDir, 'checkpoint-node.json'))).complete);
  }
  for (const next of [done([], { request_id: OTHER_ID }), done([], { next_cursor: 'repeat' })]) {
    const runDir = await directory(t);
    await assert.rejects(extract(extraction(runDir, { transport: { async submit() { return { page: done([], { next_cursor: 'repeat' }) }; }, async get() { return { page: next }; } } })), /ID changed|Repeated/);
    assert.equal(await readJSON(join(runDir, 'pages-node.json')), null);
  }
});

test('ten-minute budget includes polling, fetch and response-body reads', async t => {
  const runDir = await directory(t), { timer } = fakeClock();
  await assert.rejects(extract(extraction(runDir, { timer, transport: { async submit() { return { page: { request_id: ID, status: 'working', check_after_seconds: 600 } }; } } })), /budget/);
  for (const fetcher of [() => new Promise(() => {}), async () => ({ ok: true, headers: new Headers(), text: () => new Promise(() => {}) })]) {
    const send = http({ key: 'offline-test-only', timer: clock({ timeout: 15 }), fetcher });
    await assert.rejects(send('https://api.datawebot.com/v1/requests'), /timeout/);
  }
});

test('REST uses public fixed destination, redirects disabled, cursor escaping, no response/error secrets', async () => {
  const seen = [];
  const send = http({ key: 'offline-test-only', fetcher: async (url, init) => { seen.push([url, init]); return new Response(JSON.stringify(done()), { headers: { 'retry-after': '3' } }); } });
  const transport = restTransport(send);
  assert.equal((await transport.get(ID, 'a&b')).delay, 3000);
  assert.ok(seen[0][0].endsWith('?cursor=a%26b'));
  assert.equal(seen[0][1].redirect, 'error');
  assert.equal(seen[0][1].headers.Authorization, 'Bearer offline-test-only');
  await assert.rejects(send('https://evil.test/v1/requests'), /destination/);
  let calls = 0;
  const rejected = http({ key: 'offline-test-only', fetcher: async () => { calls++; return new Response('DO NOT LOG THIS SECRET', { status: 429, headers: { 'retry-after': '60' } }); } });
  await assert.rejects(rejected('https://api.datawebot.com/v1/requests', { method: 'POST', body: {} }), error => !error.message.includes('SECRET') && error.message.includes('60000'));
  assert.equal(calls, 1);
  assert.equal(retryDelay('Wed, 21 Oct 2015 07:28:00 GMT', Date.parse('Wed, 21 Oct 2015 07:27:59 GMT')), 1000);
  await assert.rejects(http({ key: 'offline-test-only', fetcher: async () => { throw new Error('SECRET'); } })('https://api.datawebot.com/v1/requests'), error => !error.message.includes('SECRET'));
});

test('numeric price args reject before network; malformed record field types never become zero', () => {
  for (const input of ['', ' ', 'NaN', 'Infinity', '-1', '1abc', '0x10']) assert.throws(() => priceArgs(input, 'USD'));
  assert.throws(() => priceArgs('10', 'usd'));
  assert.equal(priceArgs('0', 'USD'), 0);
  for (const extra of [{ title: 10 }, { url: 2 }, { price: '10' }, { price: { amount: '10' } }, { price: { amount: Infinity } }, { price: { amount: -1 } }, { price: { currency: 'bad' } }, { availability: true }]) assert.throws(() => productsCSV([done([record(cfg, extra)])]));
  assert.throws(() => productsCSV([done([record(cfg)], { meta: { country: 1 } })]));
  assert.throws(() => productsCSV([done([record(cfg)], { meta: { as_of: 'yesterday' } })]));
  assert.match(productsCSV([done([record(cfg, { price: null })])]), /"",""/);
  assert.equal(productsCSV([done([])]).split('\r\n').length, 2);
});

test('inventory only explicit known transitions, product isolation, stable event/dedup, unknown preserves known', () => {
  const history = { products: {} }, source = 'https://example.test/product/1';
  const row = { availability: 'in_stock', as_of: AS_OF };
  assert.equal(inventoryUpdate(history, row, source, ID).alert, null);
  const transition = inventoryUpdate(history, { ...row, availability: 'out_of_stock' }, source, OTHER_ID);
  assert.equal(transition.alert.from, 'in_stock'); assert.equal(transition.alert.to, 'out_of_stock');
  assert.deepEqual(inventoryUpdate(history, row, source, OTHER_ID), transition);
  const before = history.products[source].availability;
  for (const availability of ['available', null, undefined]) {
    assert.equal(inventoryUpdate(history, { availability, as_of: AS_OF }, source, String(availability)).alert, null);
    assert.equal(history.products[source].availability, before);
  }
  assert.equal(inventoryUpdate(history, row, source + '2', ID).alert, null);
  assert.equal(history.products[source].availability, 'out_of_stock');
});

test('inventory retries replay event from history after failed alert delivery, without re-extraction', async t => {
  const root = await directory(t), history = join(root, 'history.json'), source = canonicalURL(urls(cfg).product, 'product', cfg);
  const runDir = join(root, 'run');
  await inventoryOutput([done([record(cfg)])], source, cfg, history, runDir);
  const pages = [done([record(cfg, { availability: 'out_of_stock' })], { request_id: OTHER_ID })];
  const event = await inventoryOutput(pages, source, cfg, history, runDir);
  await rm(join(runDir, 'alert-node.json'));
  assert.deepEqual(await inventoryOutput(pages, source, cfg, history, runDir), event);
  assert.deepEqual(await readJSON(join(runDir, 'alert-node.json')), event);
});

test('CSV quoting, embedded newlines, formulas and missing values', () => {
  assert.equal(csvCell('a,"b"\nc'), '"a,""b""\nc"');
  for (const value of ['=SUM(1,2)', '+1', '-2', '@cmd', '  =cmd', '\tcmd', '\rcmd', '\ncmd']) assert.ok(csvCell(value).startsWith('"\''));
  assert.equal(csvCell(null), '""'); assert.equal(csvCell(0), '"0"');
});

test('MCP JSON and SSE response matching/errors and structured/JSON-text wrapping', () => {
  const message = { jsonrpc: '2.0', id: 1, result: { ok: true } };
  assert.deepEqual(rpcResponse(JSON.stringify(message), 'application/json', 1), { ok: true });
  assert.deepEqual(rpcResponse(`: ping\r\n\r\nevent: message\r\ndata: ${JSON.stringify(message)}\r\n\r\n`, 'text/event-stream', 1), { ok: true });
  for (const input of [{ jsonrpc: '2.0', id: 1, error: { message: 'secret' } }, { ...message, id: 2 }]) assert.throws(() => rpcResponse(JSON.stringify(input), 'application/json', 1));
  assert.deepEqual(toolContent({ structuredContent: { data: { products: [] } } }), { data: { products: [] } });
  assert.deepEqual(toolContent({ content: [{ type: 'text', text: '{"data":{"products":[]}}' }] }), { data: { products: [] } });
  assert.throws(() => toolContent({ isError: true, structuredContent: { secret: true } }));
});

test('MCP initialize, initialized notification, headers/session; JSON then SSE tools', async () => {
  const calls = [];
  const client = mcpClient(async (url, init) => {
    calls.push(init);
    assert.equal(url, 'https://mcp.datawebot.com/mcp');
    assert.equal(init.headers.Accept, 'application/json, text/event-stream');
    assert.equal(init.headers['MCP-Protocol-Version'], '2025-03-26');
    if (calls.length > 1) assert.equal(init.headers['Mcp-Session-Id'], 'test-session');
    const result = init.body.method === 'initialize' ? { protocolVersion: '2025-03-26' } : { structuredContent: { data: { tools: [] } } };
    if (init.body.method === 'notifications/initialized') { assert.equal(init.body.id, undefined); return { text: '', headers: new Headers(), delay: 0 }; }
    const message = JSON.stringify({ jsonrpc: '2.0', id: init.body.id, result });
    const sse = calls.length > 2;
    return { text: sse ? `data: ${message}\n\n` : message, headers: new Headers({ 'content-type': sse ? 'text/event-stream' : 'application/json', 'mcp-session-id': 'test-session' }), delay: 0 };
  });
  await client.call('discover');
  assert.deepEqual(calls.map(c => c.body.method), ['initialize', 'notifications/initialized', 'tools/call']);
});

for (const immediate of [true, false]) test(`MCP realistic data envelope, ${immediate ? 'immediate done without ID' : 'working then cursor pages, final without ID'}, raw cache`, async t => {
  const runDir = await directory(t), { timer } = fakeClock(), calls = [];
  const final = { data: { products: [record(cfg)] }, meta: { as_of: AS_OF, country: 'US' } };
  const client = { async call(name, args) {
    calls.push([name, args]);
    if (name === 'discover') return { page: { data: { tools: ['find_endpoints', 'submit_job', 'get_results'].map(name => ({ name })) } } };
    if (name === 'find_endpoints') return { page: { data: { endpoints: [{ endpoint: cfg.search_endpoint, available: true }] } } };
    if (name === 'submit_job') { assert.equal(args.capability, undefined); assert.equal(args.max_pages, 1); assert.equal(args.max_credits, 5); return { page: immediate ? final : { data: { status: 'working', request_id: ID, check_after_seconds: 2 } } }; }
    assert.equal(name, 'get_results'); assert.equal(args.request_id, ID);
    return { page: args.cursor ? final : { data: { products: [], request_id: ID, next_cursor: 'next' }, meta: { as_of: AS_OF } } };
  } };
  const args = extraction(runDir, { mode: 'mcp', timer, transport: mcpTransport({ cfg, source: urls(cfg).search, timer, client }) });
  const pages = await extract(args);
  assert.deepEqual(pages.at(-1), final);
  assert.equal(rows(pages).length, 1);
  assert.equal(answerPage(final, 'mcp', immediate ? undefined : ID).request_id, immediate ? null : ID);
  const before = calls.length;
  assert.deepEqual(await extract(args), pages);
  assert.equal(calls.length, before);
  assert.equal(calls.filter(([name]) => name === 'submit_job').length, 1);
});

test('MCP missing tool/unavailable endpoint does not submit or fall back', async () => {
  for (const available of [undefined, false]) {
    const calls = [];
    const transport = mcpTransport({ cfg, source: urls(cfg).search, client: { async call(name) {
      calls.push(name);
      return { page: name === 'discover' ? { data: { tools: (available === undefined ? [] : ['find_endpoints', 'submit_job', 'get_results']).map(name => ({ name })) } } : { data: { endpoints: [{ endpoint: cfg.search_endpoint, available }] } } };
    } } });
    await assert.rejects(transport.submit({}));
    assert.ok(!calls.includes('submit_job'));
  }
});

test('real CLI validation and config resolution independent of cwd; cached run needs no key', async t => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const actual = await config(), url = urls(actual).product;
  const runDir = await directory(t);
  const environment = {};
  const invalid = spawnSync(process.execPath, [join(root, 'examples/price-matching/node.mjs'), url, 'Infinity', 'USD', runDir], { cwd: tmpdir(), env: environment, encoding: 'utf8' });
  assert.equal(invalid.status, 1); assert.match(invalid.stderr, /finite/);
  assert.equal(await readJSON(join(runDir, 'checkpoint-node.json')), null);
  await extract({ cfg: actual, kind: 'product', url, runDir, identity: { workflow: 'price-matching', ours: 20, currency: 'USD' }, transport: { async submit(payload) { assert.equal(payload.max_credits, 1); return { page: done([record(actual)]) }; } } });
  const cached = spawnSync(process.execPath, [join(root, 'examples/price-matching/node.mjs'), url, '20', 'USD', runDir], { cwd: tmpdir(), env: environment, encoding: 'utf8' });
  assert.equal(cached.status, 0, cached.stderr);
  assert.equal((await readJSON(join(runDir, 'proposal-node.json'))).action, 'review_proposal');
});
