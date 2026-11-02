import { resolve } from 'node:path';
import { canonicalURL, cli, config, extract, requireValue } from '../../lib/core.mjs';
import { inventoryOutput } from '../../lib/use-cases.mjs';

await cli(async args => {
  requireValue(args.length === 3, 'Usage: node examples/inventory-alerts/node.mjs PRODUCT_URL HISTORY_FILE RUN_DIR');
  const [url, history, directory] = args;
  const cfg = await config(), runDir = resolve(directory), historyFile = resolve(history);
  const source = canonicalURL(url, 'product', cfg);
  const pages = await extract({ cfg, kind: 'product', url, runDir, identity: { workflow: 'inventory-alerts', historyFile } });
  await inventoryOutput(pages, source, cfg, historyFile, runDir);
  console.log('Saved alert-node.json (local event only; reruns replay the same event).');
});
