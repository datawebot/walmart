import { join, resolve } from 'node:path';
import { atomicWrite, canonicalURL, cli, config, extract, requireValue } from '../../lib/core.mjs';
import { priceArgs, priceProposal } from '../../lib/use-cases.mjs';

await cli(async args => {
  requireValue(args.length === 4, 'Usage: node examples/price-matching/node.mjs PRODUCT_URL OUR_PRICE CURRENCY RUN_DIR');
  const [url, value, currency, directory] = args;
  const ours = priceArgs(value, currency);
  const cfg = await config(), runDir = resolve(directory);
  const source = canonicalURL(url, 'product', cfg);
  const pages = await extract({ cfg, kind: 'product', url, runDir, identity: { workflow: 'price-matching', ours, currency } });
  await atomicWrite(join(runDir, 'proposal-node.json'), priceProposal(pages, source, ours, currency, cfg));
  console.log('Saved proposal-node.json (review only; no repricing).');
});
