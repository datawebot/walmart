import { join, resolve } from 'node:path';
import { atomicWrite, cli, config, extract, requireValue } from '../../lib/core.mjs';
import { productsCSV } from '../../lib/use-cases.mjs';

await cli(async args => {
  requireValue(args.length === 2, 'Usage: node examples/market-analysis-etl/node.mjs SEARCH_URL RUN_DIR');
  const [url, directory] = args;
  const cfg = await config(), runDir = resolve(directory);
  const pages = await extract({ cfg, kind: 'search', url, runDir, identity: { workflow: 'market-analysis-etl' } });
  await atomicWrite(join(runDir, 'products-node.csv'), productsCSV(pages), false);
  console.log('Saved pages-node.json and products-node.csv (one source page; all result cursors).');
});
