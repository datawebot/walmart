import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { canonicalURL, config, extract, readJSON } from '../lib/core.mjs';

const ID = '12345678-1234-1234-1234-123456789abc';
const root = fileURLToPath(new URL('../', import.meta.url));
const cfg = await config();
const search = `https://${cfg.domains[0]}${cfg.brand === 'amazon' ? '/s?k=coffee' : '/search?q=coffee'}`;
const productURL = `https://${cfg.domains[0]}${cfg.brand === 'amazon' ? '/dp/B012345678' : '/ip/coffee/12345'}`;
const product = { title: 'Coffee', url: productURL, price: { amount: null, currency: null }, availability: 'in_stock' };
const page = { request_id: ID, status: 'done', products: [product], meta: { as_of: '2026-10-27T12:00:00Z', country: 'US' } };

for (const workflow of ['inventory-alerts', 'market-analysis-etl', 'llm-agent']) test(`${workflow}: actual CLI reads cached run from different cwd without a key`, async t => {
  const directory = await mkdtemp(join(tmpdir(), 'node-cli-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const runDir = join(directory, 'run'), historyFile = join(directory, 'history-node.json');
  const inventory = workflow === 'inventory-alerts', mcp = workflow === 'llm-agent';
  const raw = mcp ? { data: { products: [product] }, meta: page.meta } : page;
  await extract({ cfg, kind: inventory ? 'product' : 'search', url: inventory ? productURL : search, runDir,
    mode: mcp ? 'mcp' : 'rest', identity: inventory ? { workflow, historyFile } : { workflow },
    transport: { async submit() { return { page: raw }; } },
  });
  const args = inventory ? [productURL, historyFile, runDir] : [search, runDir];
  const result = spawnSync(process.execPath, [join(root, `examples/${workflow}/node.mjs`), ...args], { cwd: tmpdir(), env: {}, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, '');
  assert.deepEqual(await readJSON(join(runDir, 'pages-node.json')), [raw]);
  if (inventory) {
    assert.equal((await readJSON(join(runDir, 'alert-node.json'))).alert, null);
    assert.equal((await readJSON(historyFile)).products[canonicalURL(productURL, 'product', cfg)].availability, 'in_stock');
  } else if (mcp) {
    const research = await readJSON(join(runDir, 'research-node.json'));
    assert.equal(research.request_id, null);
    assert.equal(research.products[0].price, null);
  } else {
    assert.match(await readFile(join(runDir, 'products-node.csv'), 'utf8'), /"Coffee","",""/);
  }
});
