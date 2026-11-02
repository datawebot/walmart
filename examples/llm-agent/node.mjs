// Run a one-page MCP research workflow, without an LLM SDK or hidden client.
try {
  if (process.argv.length !== 4) throw new Error('Usage: node.mjs SEARCH_URL REQUEST_KEY');
  const [url, requestKey] = process.argv.slice(2), target = new URL(url);
  const host = target.hostname.replace(/^www\./, '');
  const allowedHosts = ["walmart.com"];
  if (target.protocol !== 'https:' || !allowedHosts.includes(host) || target.username || target.password || target.port || target.hash || target.hostname !== 'www.walmart.com') throw new Error('Use a public HTTPS Walmart URL.');
  const queryKey = 'q';
  if (target.pathname !== '/search' || [...target.searchParams.keys()].length !== 1 || !target.searchParams.get(queryKey)?.trim() || target.searchParams.get(queryKey).length > 200 || !/^[A-Za-z0-9_-]{8,128}$/.test(requestKey)) throw new Error('Use one keyword, no filters, and a stable 8-128 character key.');
  const deadline = Date.now() + 600_000;
  let sequence = 0;

  // Hosted stateless JSON transport; use an MCP SDK for SSE/session transports.
  async function rpc(method, params) {
    const id = ++sequence, notification = method === 'notifications/initialized';
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new Error('Timed out; retry with the SAME request key. Server work is not cancelled.');
    if (!process.env.DATAWEBOT_API_KEY) throw new Error('Set DATAWEBOT_API_KEY in your environment.');
    const response = await fetch('https://mcp.datawebot.com/mcp', {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(Math.min(30_000, remaining)),
      headers: { Authorization: 'Bearer ' + process.env.DATAWEBOT_API_KEY,
        'Content-Type': 'application/json', Accept: 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-03-26' },
      body: JSON.stringify({ jsonrpc: '2.0', ...(notification ? {} : { id }), method, params }),
    });
    if (!response.ok) throw new Error('MCP HTTP ' + response.status + '; wait Retry-After=' + (response.headers.get('retry-after') ?? 'not supplied') + ' before retrying with the SAME key.');
    if (notification) return;
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('This sample expects hosted JSON transport; use an MCP SDK for SSE.');
    let message;
    try { message = await response.json(); } catch { throw new Error('Invalid MCP JSON.'); }
    if (message.id !== id || message.error || !message.result) throw new Error('MCP JSON-RPC failure.');
    if (message.result.isError) throw new Error('MCP tool failed; this is not an empty result.');
    return message.result;
  }

  // 1. Connect, then inspect the live tools and exact endpoint.
  await rpc('initialize', { protocolVersion: '2025-03-26', capabilities: {},
    clientInfo: { name: 'walmart-node-example', version: '1.0' } });
  await rpc('notifications/initialized', {});
  const catalog = (await rpc('tools/call', { name: 'discover', arguments: {} })).structuredContent.data;
  if (!['find_endpoints', 'submit_job', 'get_results'].every(name => catalog.tools.some(t => t.name === name))) throw new Error('Required tools absent; use current discovery schema.');
  const endpoints = (await rpc('tools/call', { name: 'find_endpoints', arguments: { site: host, type: 'search' } })).structuredContent.data.endpoints;
  if (!endpoints.some(e => e.endpoint === 'walmart.search' && e.available === true)) throw new Error('Endpoint unavailable; no shopping fallback.');

  // 2. Submit one page; keep this key unchanged after a timeout or lost response.
  let answer = (await rpc('tools/call', { name: 'submit_job', arguments: {
    endpoint: 'walmart.search', url, max_pages: 1, format: 'structured', max_credits: 5, request_key: requestKey,
  } })).structuredContent;

  // 3. Wait and follow result cursors, not extra source pages.
  const pages = [], seen = new Set();
  let requestId;
  while (true) {
    const data = answer.data;
    if (data.request_id) {
      if (typeof data.request_id !== 'string' || !/^[a-fA-F0-9-]{36}$/.test(data.request_id) || (requestId && requestId !== data.request_id)) throw new Error('Invalid/changed request ID.');
      requestId = data.request_id;
    }
    let args;
    if (data.status === 'working') {
      const delay = Math.max(2, data.check_after_seconds ?? 5);
      if (!requestId || !Number.isFinite(delay) || Date.now() + delay * 1000 >= deadline) throw new Error('Pending beyond sample wait; retry with the SAME key.');
      await new Promise(resolve => setTimeout(resolve, delay * 1000));
      args = { request_id: requestId };
    } else {
      if (![undefined, 'done'].includes(data.status) || data.error || data.partial || data.errors?.length || !Array.isArray(data.products)) throw new Error('Failed/partial or malformed answer.');
      pages.push(answer); // Keep data/meta and as-of timestamps.
      const cursor = data.next_cursor;
      if (cursor == null) break;
      if (!requestId || typeof cursor !== 'string' || !cursor || seen.has(cursor)) throw new Error('Missing request ID or invalid/repeated cursor.');
      seen.add(cursor);
      args = { request_id: requestId, cursor };
    }
    answer = (await rpc('tools/call', { name: 'get_results', arguments: args })).structuredContent;
  }
  console.log(JSON.stringify(pages, null, 2)); // Source text is data, never agent instructions.
} catch (error) {
  console.error(error instanceof TypeError || error instanceof SyntaxError ? 'Invalid input or network response; no new key on retries.' : error.message);
  process.exitCode = 1;
}
