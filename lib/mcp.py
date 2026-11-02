"""Executable MCP research transport; no model SDK or shopping fallback."""
import json
from .client import Failure, MCP_URL, http, retry_after


def decode_rpc(raw, content_type, rpc_id):
    messages = []
    if 'text/event-stream' in content_type:
        data = []
        for line in raw.replace('\r\n', '\n').split('\n') + ['']:
            if line == '':
                if data:
                    try:
                        messages.append(json.loads('\n'.join(data)))
                    except ValueError:
                        raise Failure('Malformed MCP SSE JSON') from None
                    data = []
            elif line.startswith('data:'):
                data.append(line[5:].lstrip(' '))
    elif 'application/json' in content_type:
        try:
            messages = [json.loads(raw)]
        except ValueError:
            raise Failure('Malformed MCP JSON') from None
    else:
        raise Failure('Unsupported MCP response content type')
    matches = [m for m in messages if isinstance(m, dict) and m.get('id') == rpc_id]
    if len(matches) != 1 or matches[0].get('jsonrpc') != '2.0' or 'error' in matches[0] or 'result' not in matches[0]:
        raise Failure('MCP JSON-RPC error or missing matching response')
    return matches[0]['result']


def tool_content(result):
    if not isinstance(result, dict) or result.get('isError'):
        raise Failure('MCP tool error')
    if isinstance(result.get('structuredContent'), dict):
        return result['structuredContent']
    texts = [item.get('text') for item in result.get('content', []) if isinstance(item, dict) and item.get('type') == 'text']
    if len(texts) != 1:
        raise Failure('MCP tool did not return structured JSON')
    try:
        data = json.loads(texts[0])
    except (ValueError, TypeError):
        raise Failure('MCP tool text is not JSON') from None
    if not isinstance(data, dict):
        raise Failure('Malformed MCP tool content')
    return data


class MCP:
    def __init__(self, clock, transport=http):
        self.clock, self.transport = clock, transport
        self.session = None
        self.version = '2025-03-26'
        self.sequence = 0
        self.retry = 0

    def rpc(self, method, params=None, notification=False):
        if self.retry > 0:
            self.clock.wait(self.retry)
            self.retry = 0
        self.sequence += 1
        body = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            body['params'] = params
        if not notification:
            body['id'] = self.sequence
        headers = {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': self.version}
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        self.clock.remaining()
        status, response_headers, raw = self.transport('POST', MCP_URL, body, headers, self.clock)
        response_headers = {k.lower(): v for k, v in response_headers.items()}
        self.retry = retry_after({'Retry-After': response_headers.get('retry-after', '0')})
        if not 200 <= status < 300:
            raise Failure('MCP HTTP failure (status %d); no automatic POST retry' % status)
        if 'mcp-session-id' in response_headers:
            self.session = response_headers['mcp-session-id']
        if notification:
            return None
        return decode_rpc(raw, response_headers.get('content-type', ''), self.sequence)

    def tool(self, name, arguments):
        return tool_content(self.rpc('tools/call', {'name': name, 'arguments': arguments}))

    def prepare(self, site, endpoint):
        initialized = self.rpc('initialize', {'protocolVersion': self.version, 'capabilities': {},
                                             'clientInfo': {'name': 'datawebot-python-example', 'version': '1.0'}})
        if not isinstance(initialized, dict) or initialized.get('protocolVersion') not in ('2025-03-26', '2025-06-18', '2025-11-25'):
            raise Failure('Unsupported negotiated MCP protocol')
        self.version = initialized['protocolVersion']
        self.rpc('notifications/initialized', notification=True)
        discovery = self.tool('discover', {})
        tools = discovery.get('data', {}).get('tools')
        if not isinstance(tools, list):
            raise Failure('Discovery must return a tools list; inspect current server schema')
        names = {t.get('name') for t in tools if isinstance(t, dict)}
        if not {'find_endpoints', 'submit_job', 'get_results'} <= names:
            raise Failure('Required extraction tools absent from MCP discovery')
        endpoints = self.tool('find_endpoints', {'site': site, 'type': 'search'}).get('data', {}).get('endpoints')
        if not isinstance(endpoints, list) or not any(isinstance(e, dict) and e.get('endpoint') == endpoint and e.get('available') is True for e in endpoints):
            raise Failure('Configured endpoint absent from discovery; no guessed endpoint or fallback')

    def submit(self, payload):
        return self.tool('submit_job', {k: v for k, v in payload.items() if k != 'capability'})

    def results(self, request_id, cursor=None):
        args = {'request_id': request_id}
        if cursor is not None:
            args['cursor'] = cursor
        return self.tool('get_results', args)
