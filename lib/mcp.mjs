import { SampleError, requireValue, http, clock } from './core.mjs';

export function rpcResponse(text, contentType, id) {
  let messages;
  try {
    if (contentType.includes('text/event-stream')) {
      messages = text.replace(/\r\n/g, '\n').split(/\n\n/).flatMap(block => {
        const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        return data ? [JSON.parse(data)] : [];
      });
    } else {
      requireValue(contentType.includes('application/json'), 'Unsupported MCP response media type.');
      messages = [JSON.parse(text)];
    }
  } catch { throw new SampleError('Malformed MCP JSON/SSE response.'); }
  const message = messages.find(m => m.id === id);
  requireValue(message?.jsonrpc === '2.0' && !message.error && Object.hasOwn(message, 'result'), 'MCP JSON-RPC error or missing matching response.');
  return message.result;
}
export function toolContent(result) {
  requireValue(result && !result.isError, 'MCP tool returned isError; no downstream output was updated.');
  if (result.structuredContent && typeof result.structuredContent === 'object') return result.structuredContent;
  try {
    const text = result.content?.filter(c => c.type === 'text').map(c => c.text).join('\n');
    const value = JSON.parse(text);
    requireValue(value && typeof value === 'object', 'Invalid MCP tool data.');
    return value;
  } catch { throw new SampleError('MCP tool did not return structured data or JSON text.'); }
}
export function mcpClient(send, timer = clock()) {
  let id = 0, session, version = '2025-03-26', initialized = false, pendingUntil = 0;
  async function rpc(method, params, notification = false) {
    if (pendingUntil > timer.now()) await timer.wait(pendingUntil - timer.now());
    const requestID = notification ? undefined : ++id;
    const response = await send('https://mcp.datawebot.com/mcp', {
      method: 'POST',
      headers: { Accept: 'application/json, text/event-stream', 'MCP-Protocol-Version': version, ...(session ? { 'Mcp-Session-Id': session } : {}) },
      body: { jsonrpc: '2.0', ...(notification ? {} : { id: requestID }), method, ...(params === undefined ? {} : { params }) },
    });
    pendingUntil = timer.now() + (response.delay ?? 0);
    const returnedSession = response.headers.get('mcp-session-id');
    if (returnedSession) {
      requireValue(!/[\r\n]/.test(returnedSession), 'Invalid MCP session header.');
      session = returnedSession;
    }
    if (notification) return { delay: response.delay ?? 0 };
    return { result: rpcResponse(response.text, response.headers.get('content-type') ?? '', requestID), delay: response.delay ?? 0 };
  }
  return {
    async initialize() {
      if (initialized) return;
      const { result } = await rpc('initialize', { protocolVersion: version, capabilities: {}, clientInfo: { name: 'datawebot-node-example', version: '1.0.0' } });
      requireValue(['2025-03-26', '2025-06-18', '2025-11-25'].includes(result.protocolVersion), 'Unsupported negotiated MCP protocol version.');
      version = result.protocolVersion;
      await rpc('notifications/initialized', undefined, true);
      initialized = true;
    },
    async call(name, args = {}) {
      await this.initialize();
      const { result, delay } = await rpc('tools/call', { name, arguments: args });
      return { page: toolContent(result), delay };
    },
  };
}
export function mcpTransport({ cfg, source, timer, key, fetcher, client }) {
  let ready = false;
  async function discover() {
    if (ready) return;
    client ??= mcpClient(http({ key, fetcher, timer }), timer);
    const { page: catalog } = await client.call('discover');
    const tools = catalog.data?.tools?.map(t => t.name) ?? [];
    requireValue(['find_endpoints', 'submit_job', 'get_results'].every(name => tools.includes(name)), 'Required exact-endpoint MCP tools are not in discover. No shopping fallback.');
    const { page } = await client.call('find_endpoints', { site: new URL(source).hostname, type: 'search' });
    requireValue(Array.isArray(page.data?.endpoints) && page.data.endpoints.some(e => e.endpoint === cfg.search_endpoint && e.available === true), 'Configured search endpoint was not returned by find_endpoints.');
    ready = true;
  }
  return {
    async submit(payload) {
      await discover();
      return client.call('submit_job', payload);
    },
    async get(id, cursor) {
      await discover();
      return client.call('get_results', { request_id: id, ...(cursor ? { cursor } : {}) });
    },
  };
}
