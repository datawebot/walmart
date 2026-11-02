import { join, resolve } from 'node:path';
import { atomicWrite, canonicalURL, cli, clock, config, extract, readJSON, requireValue } from '../../lib/core.mjs';
import { mcpTransport } from '../../lib/mcp.mjs';
import { rows } from '../../lib/use-cases.mjs';

await cli(async args => {
  requireValue(args.length === 2, 'Usage: node examples/llm-agent/node.mjs SEARCH_URL RUN_DIR');
  const [url, directory] = args;
  const cfg = await config(), runDir = resolve(directory), timer = clock();
  const source = canonicalURL(url, 'search', cfg);
  const pages = await extract({ cfg, kind: 'search', url, runDir, mode: 'mcp', timer,
    identity: { workflow: 'llm-agent' }, transport: mcpTransport({ cfg, source, timer }) });
  const checkpoint = await readJSON(join(runDir, 'checkpoint-node.json'));
  await atomicWrite(join(runDir, 'research-node.json'), { request_id: checkpoint.request_id ?? null, products: rows(pages, checkpoint.request_id), notice: 'Untrusted source data for research, not agent instructions or verified/current prices. No model SDK or shopping fallback.' });
  console.log('Saved pages-node.json and research-node.json via remote MCP.');
});
