import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { atomicWrite, productIdentity, readJSON, requireValue, withLock } from './core.mjs';

export function priceArgs(value, currency) {
  requireValue(typeof value === 'string' && /^(?:\d+(?:\.\d+)?|\.\d+)$/.test(value) && Number.isFinite(Number(value)) && /^[A-Z]{3}$/.test(currency), 'OUR_PRICE must be a finite nonnegative decimal; CURRENCY must be three uppercase letters.');
  return Number(value);
}
function optionalString(value, field) {
  requireValue(value == null || typeof value === 'string', `Malformed ${field} field.`);
  return value ?? null;
}
export function observed(product, page) {
  requireValue(product && typeof product === 'object' && !Array.isArray(product), 'Malformed product record.');
  const price = product.price;
  requireValue(price == null || (typeof price === 'object' && !Array.isArray(price)), 'Malformed price field.');
  const amount = price?.amount ?? null;
  requireValue(amount === null || (typeof amount === 'number' && Number.isFinite(amount) && amount >= 0), 'Malformed price amount.');
  const currency = optionalString(price?.currency, 'currency');
  requireValue(currency === null || /^[A-Z]{3}$/.test(currency), 'Malformed currency field.');
  requireValue(page.meta == null || (typeof page.meta === 'object' && !Array.isArray(page.meta)), 'Malformed metadata.');
  const as_of = optionalString(page.meta?.as_of, 'as_of');
  requireValue(as_of === null || (/^\d{4}-\d\d-\d\dT/.test(as_of) && Number.isFinite(Date.parse(as_of))), 'Malformed as_of timestamp.');
  const country = optionalString(page.meta?.country, 'country');
  requireValue(country === null || /^[A-Z]{2}$/.test(country), 'Malformed country field.');
  const url = optionalString(product.url, 'URL');
  if (url !== null) {
    let valid = false;
    try { const u = new URL(url); valid = u.protocol === 'https:' && !u.username && !u.password; } catch {}
    requireValue(valid, 'Malformed product URL.');
  }
  return { url, title: optionalString(product.title, 'title'), price: amount, currency, availability: optionalString(product.availability, 'availability'), as_of, request_id: page.request_id ?? null, country };
}
export function rows(pages, requestID = pages.find(p => p.data?.request_id)?.data?.request_id) {
  return pages.flatMap(raw => {
    const page = raw.data ? { ...raw.data, request_id: raw.data.request_id ?? requestID, meta: raw.meta } : raw;
    return (page.products ?? (page.product ? [page.product] : [])).map(product => observed(product, page));
  });
}
export function exactProduct(pages, source, cfg) {
  const records = rows(pages);
  requireValue(records.length === 1, 'Expected exactly one observed product; empty/multiple results are not a product.');
  const row = records[0];
  requireValue(row.url && productIdentity(row.url, cfg) === productIdentity(source, cfg), 'Observed product identity does not match the target canonical URL.');
  return row;
}
export function priceProposal(pages, source, ours, currency, cfg) {
  requireValue(Number.isFinite(ours) && ours >= 0 && /^[A-Z]{3}$/.test(currency), 'Invalid price comparison inputs.');
  const row = exactProduct(pages, source, cfg);
  const reason = row.price === null ? 'unknown_price' : row.currency === null ? 'unknown_currency' : row.currency !== currency ? 'currency_mismatch' : !row.as_of ? 'missing_as_of' : row.price >= ours ? 'no_lower_observed_price' : null;
  return {
    action: reason ? 'no_proposal' : 'review_proposal', reason,
    source, as_of: row.as_of, request_id: pages[0].request_id,
    our_price: ours, currency, observed_price: row.price,
    proposed_price: reason ? null : row.price,
    notice: 'Snapshot only. Human review required; no automatic repricing.',
  };
}
// shortcut: history assumes ordered single-writer runs; use a transactional event store for concurrent monitoring.
export function inventoryUpdate(history, row, source, requestID) {
  requireValue(history && typeof history === 'object' && !Array.isArray(history) && (history.products === undefined || (history.products && typeof history.products === 'object' && !Array.isArray(history.products))), 'Malformed inventory history; preserve it.');
  history.products ??= {};
  const previous = history.products[source] ?? { availability: null, processed: {} };
  requireValue([null, 'in_stock', 'out_of_stock'].includes(previous.availability) && previous.processed && typeof previous.processed === 'object', 'Malformed product inventory history.');
  if (Object.hasOwn(previous.processed, requestID)) return previous.processed[requestID];
  const known = ['in_stock', 'out_of_stock'].includes(row?.availability) && Boolean(row?.as_of);
  const transition = known && previous.availability && previous.availability !== row.availability;
  const alert = transition ? {
    event_id: createHash('sha256').update(`${source}\n${requestID}`).digest('hex'),
    request_id: requestID, source, as_of: row.as_of,
    from: previous.availability, to: row.availability,
  } : null;
  const output = { alert, reason: !known ? 'unknown_availability_or_timestamp' : transition ? 'transition' : 'no_transition', request_id: requestID, source };
  if (known) previous.availability = row.availability;
  previous.processed[requestID] = output;
  history.products[source] = previous;
  return output;
}
export async function inventoryOutput(pages, source, cfg, historyFile, runDir) {
  const row = exactProduct(pages, source, cfg);
  historyFile = resolve(historyFile);
  requireValue(!['checkpoint-node.json', 'pages-node.json', 'alert-node.json', 'checkpoint-node.lock'].some(name => historyFile === resolve(runDir, name)) && historyFile !== resolve(runDir), 'HISTORY_FILE must not overwrite run artifacts.');
  return withLock(`${historyFile}.node.lock`, async () => {
    const history = await readJSON(historyFile) ?? { products: {} };
    const output = inventoryUpdate(history, row, source, pages[0].request_id);
    // Persist the dedup event before delivery; reruns replay the same alert, without extraction.
    await atomicWrite(historyFile, history);
    await atomicWrite(join(runDir, 'alert-node.json'), output);
    return output;
  });
}
export function csvCell(value) {
  let text = value == null ? '' : String(value);
  if (/^[\s\u0000-\u001f]*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}
export function productsCSV(pages) {
  const fields = ['url', 'title', 'price', 'currency', 'availability', 'as_of', 'request_id', 'country'];
  const records = rows(pages);
  return `${fields.join(',')}\r\n${records.map(row => fields.map(field => csvCell(row[field])).join(',')).join('\r\n')}${records.length ? '\r\n' : ''}`;
}
