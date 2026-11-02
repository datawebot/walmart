import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { clock, extract, http, readJSON } from '../lib/core.mjs';
import { mcpClient, rpcResponse } from '../lib/mcp.mjs';
import { exactProduct } from '../lib/use-cases.mjs';

const ID = '12345678-1234-1234-1234-123456789abc';
const cfg = { brand: 'walmart', domains: ['walmart.com'], search_endpoint: 'walmart.search', product_endpoint: 'walmart.product_details' };
const source = 'https://www.walmart.com/ip/sample-slug/12345';
async function dir(t) { const path = await mkdtemp(join(tmpdir(), 'node-recovery-')); t.after(() => rm(path, { recursive: true, force: true })); return path; }
const done = { status: 'done', request_id: ID, products: [], errors: [], meta: { errors: [] } };

test('Walmart outgoing product contract keeps www, slug and item ID; returned slug may differ', async t => {
  const runDir = await dir(t);
  const product = { title: 'Item', url: source.replace('sample-slug', 'updated-slug') };
  const pages = await extract({ cfg, kind: 'product', url: source.replace('www.', ''), runDir, transport: {
    async submit(payload) {
      assert.equal(payload.url, source); assert.equal(payload.endpoint, 'walmart.product_details'); assert.equal(payload.max_credits, 1); assert.equal(payload.max_pages, 1);
      return { page: { ...done, products: [product] } };
    },
  } });
  assert.equal(exactProduct(pages, source, cfg).title, 'Item');
});

test('resume honors durable next_poll_at before any known-ID GET', async t => {
  const runDir = await dir(t);
  let now = 10000;
  const interrupted = clock({ now: () => now, sleep: async () => { throw new Error('process stopped'); } });
  await assert.rejects(extract({ cfg, kind: 'search', url: 'https://www.walmart.com/search?q=coffee', runDir, timer: interrupted, transport: {
    async submit() { return { page: { request_id: ID, status: 'working', check_after_seconds: 5 }, delay: 7000 }; },
  } }));
  assert.equal((await readJSON(join(runDir, 'checkpoint-node.json'))).next_poll_at, 17000);
  const waits = [];
  const timer = clock({ now: () => now, sleep: async ms => { waits.push(ms); now += ms; } });
  await extract({ cfg, kind: 'search', url: 'https://www.walmart.com/search?q=coffee', runDir, timer, transport: {
    submit() { assert.fail('must resume known ID'); },
    async get() { assert.equal(now, 17000); return { page: done }; },
  } });
  assert.deepEqual(waits, [7000]);
});

test('HTTP error Retry-After persists even for an ambiguous POST and controls manual replay', async t => {
  const runDir = await dir(t), posted = [];
  let now = 10000;
  const timer = clock({ now: () => now, sleep: async ms => { now += ms; } });
  const common = { cfg, kind: 'search', url: 'https://www.walmart.com/search?q=coffee', runDir, timer, key: 'offline-test-only' };
  await assert.rejects(extract({ ...common, fetcher: async (url, init) => { posted.push(init.body); return new Response('secret body not logged', { status: 429, headers: { 'retry-after': '8' } }); } }), /HTTP 429/);
  assert.equal(posted.length, 1);
  assert.equal((await readJSON(join(runDir, 'checkpoint-node.json'))).next_poll_at, 18000);
  await extract({ ...common, fetcher: async (url, init) => { assert.equal(now, 18000); posted.push(init.body); return Response.json(done); } });
  assert.equal(posted[0], posted[1]);
});

test('Streamable HTTP SSE returns on matching event without waiting for stream close, handles split CRLF', async () => {
  const encoder = new TextEncoder();
  const message = JSON.stringify({ jsonrpc: '2.0', id: 7, result: { ok: true } });
  let canceled = false;
  const send = http({ key: 'offline-test-only', timer: clock({ timeout: 1000 }), fetcher: async () => new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(': ping\r'));
      controller.enqueue(encoder.encode(`\n\r\ndata: ${message}\r`));
      controller.enqueue(encoder.encode('\n\r\n'));
      // Deliberately remain open like a remote Streamable HTTP response.
    },
    cancel() { canceled = true; },
  }), { headers: { 'content-type': 'text/event-stream' } }) });
  const response = await send('https://mcp.datawebot.com/mcp', { method: 'POST', body: { jsonrpc: '2.0', id: 7 } });
  assert.deepEqual(rpcResponse(response.text, 'text/event-stream', 7), { ok: true });
  assert.equal(canceled, true);
});

test('MCP intermediate Retry-After delays initialize/notification/discover, without double delays', async () => {
  let now = 0;
  const waits = [], calls = [];
  const timer = clock({ now: () => now, sleep: async ms => { waits.push(ms); now += ms; } });
  const client = mcpClient(async (url, { body }) => {
    calls.push([body.method, now]);
    if (body.method === 'notifications/initialized') return { text: '', headers: new Headers(), delay: 2000 };
    const result = body.method === 'initialize' ? { protocolVersion: '2025-06-18' } : { structuredContent: { data: { tools: [] } } };
    return { text: JSON.stringify({ jsonrpc: '2.0', id: body.id, result }), headers: new Headers({ 'content-type': 'application/json' }), delay: 3000 };
  }, timer);
  await client.call('discover');
  assert.deepEqual(waits, [3000, 2000]);
  assert.deepEqual(calls.map(c => c[1]), [0, 3000, 5000]);
});
