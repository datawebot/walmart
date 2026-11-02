// Read one product and propose a lower same-currency price for human review.

try {
  if (process.argv.length !== 6) throw new Error('Usage: node.mjs PRODUCT_URL OUR_PRICE CURRENCY REQUEST_KEY');
  process.umask(0o077);
  const [url, ourPriceText, currency, requestKey] = process.argv.slice(2);
  const ourPrice = Number(ourPriceText);
  if (!ourPriceText.trim() || !Number.isFinite(ourPrice) || ourPrice < 0 || !/^[A-Z]{3}$/.test(currency)) throw new Error('Use a nonnegative decimal price and an uppercase ISO currency code.');
  const target = new URL(url), host = target.hostname.replace(/^www\./, '');
  const allowedHosts = ["walmart.com"];
  if (target.protocol !== 'https:' || !allowedHosts.includes(host) || target.username || target.password || target.port || target.hash || target.hostname !== 'www.walmart.com') throw new Error('Use an HTTPS Walmart URL without credentials, ports or fragments.');
  const identity = !target.search && target.pathname.match(/^\/ip\/[A-Za-z0-9_-]{1,400}\/([1-9][0-9]{0,19})$/)?.[1];
  if (!identity || !/^[A-Za-z0-9_-]{8,128}$/.test(requestKey)) throw new Error('Use the documented URL shape and a stable 8-128 character request key.');

  // Keep this key on retries. The server returns the same request instead of billing a new one.
  const payload = { capability: 'endpoint', endpoint: 'walmart.product_details',
    url, max_pages: 1, format: 'structured', max_credits: 1, request_key: requestKey };
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

  // Compare this exact listing, never search rank or an inferred currency.
  if (answer.products.length !== 1) throw new Error('Expected one product; no price proposal.');
  const product = answer.products[0], observed = new URL(product.url);
  if (observed.protocol !== 'https:' || observed.username || observed.password || observed.hostname !== target.hostname || !observed.pathname.replace(/\/$/, '').endsWith('/' + identity)) throw new Error('Returned product does not match the requested listing.');
  const price = product.price ?? {}, asOf = answer.meta?.as_of;
  const knownPrice = typeof price.amount === 'number' && Number.isFinite(price.amount) && price.amount >= 0;
  const comparable = knownPrice && price.currency === currency && typeof asOf === 'string' && Number.isFinite(Date.parse(asOf));
  const proposal = {
    action: comparable && price.amount < ourPrice ? 'review_price_match' : 'no_proposal',
    our_price: ourPrice, observed_price: price.amount ?? null, currency: price.currency ?? null,
    source: product.url, as_of: asOf ?? null, request_id: answer.request_id,
    note: 'Review model, variant, seller, shipping and taxes; this does not change a price.',
  };
  console.log(JSON.stringify(proposal, null, 2));
} catch (error) {
  console.error(error instanceof TypeError || error instanceof SyntaxError ? 'Invalid input or network response; no new key on retries.' : error.message);
  process.exitCode = 1;
}
