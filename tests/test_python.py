import copy
import csv
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import queue
import unittest
from unittest.mock import MagicMock, patch

from lib.client import (API, MCP_URL, Clock, Failure, NoRedirect, Rest, _http, http, atomic_json, canonical_url, config,
                        page_view, run, validate_page)
from lib.mcp import MCP, decode_rpc, tool_content
from lib.workflows import inventory_alert, main, money, price_proposal, products_csv

CFG = config()
HOST = CFG['domains'][0]
PRODUCT = 'https://' + HOST + ('/dp/B012345678' if CFG['brand'] == 'amazon' else '/ip/example/12345')
SEARCH = 'https://' + HOST + ('/s?k=coffee' if CFG['brand'] == 'amazon' else '/search?q=coffee')
TARGET = canonical_url(PRODUCT, 'product')
RID = '11111111-1111-4111-8111-111111111111'
RID2 = '22222222-2222-4222-8222-222222222222'


def answer(rid=RID, products=None, **kwargs):
    return {'request_id': rid, 'status': 'done', 'products': [] if products is None else products,
            'meta': {'as_of': '2026-10-27T10:00:00Z', 'country': 'US'}, **kwargs}


def product(**changes):
    return {'url': PRODUCT, 'title': 'Coffee', 'price': {'amount': 12.5, 'currency': 'USD'},
            'availability': 'in_stock', **changes}


class FakeClock:
    def __init__(self, budget=600):
        self.budget = budget
        self.waits = []

    def remaining(self):
        if self.budget <= 0:
            raise Failure('deadline')
        return self.budget

    def wait(self, seconds):
        self.waits.append(seconds)
        self.budget -= seconds
        self.remaining()


class FakeClient:
    def __init__(self, replies, directory=None):
        self.replies = list(replies)
        self.calls = []
        self.directory = directory
        self.retry = 0

    def submit(self, payload):
        self.calls.append(('POST', copy.deepcopy(payload)))
        if self.directory:
            saved = json.loads((Path(self.directory) / 'checkpoint-python.json').read_text())
            assert saved['payload'] == payload
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def results(self, rid, cursor=None):
        self.calls.append(('GET', rid, cursor))
        return self.replies.pop(0)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def run_client(self, client, **kwargs):
        return run(SEARCH, 'search', self.directory, client=client, clock=kwargs.pop('clock', FakeClock()), **kwargs)

    def test_checkpoint_before_post_poll_all_pages_and_cache(self):
        first = answer(next_cursor='page&2')
        second = answer(products=[product()])
        client = FakeClient([{'request_id': RID, 'status': 'working', 'check_after_seconds': 4}, first, second], self.directory)
        clock = FakeClock()
        pages = self.run_client(client, clock=clock)
        self.assertEqual(pages, [first, second])
        self.assertEqual(client.calls[1:], [('GET', RID, None), ('GET', RID, 'page&2')])
        self.assertEqual(clock.waits, [4, 2])
        self.assertEqual(client.calls[0][1]['max_pages'], 1)
        self.assertEqual(client.calls[0][1]['max_credits'], 5)
        self.assertEqual(self.run_client(FakeClient([])), pages)
        for path in self.directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_ambiguous_submit_replays_identical_body_and_key(self):
        first = FakeClient([Failure('lost')], self.directory)
        with self.assertRaises(Failure):
            self.run_client(first)
        second = FakeClient([answer()], self.directory)
        self.run_client(second)
        self.assertEqual(first.calls[0], second.calls[0])

    def test_known_id_resume_get_only(self):
        first = FakeClient([{'request_id': RID, 'status': 'working', 'check_after_seconds': 4}], self.directory)
        with self.assertRaises(Failure):
            self.run_client(first, clock=FakeClock(1))
        client = FakeClient([answer()])
        self.run_client(client)
        self.assertEqual(client.calls, [('GET', RID, None)])

    def test_changed_input_rejected_before_network(self):
        self.run_client(FakeClient([answer()]))
        client = FakeClient([])
        with self.assertRaises(Failure):
            self.run_client(client, identity={'new': 'input'})
        self.assertEqual(client.calls, [])

    def test_malformed_failure_partial_and_repeated_cursor_rejected(self):
        for page in ({'request_id': RID, 'status': 'failed'}, answer(partial=True), answer(errors=['bad']),
                     answer(meta={'partial': True}), {'request_id': RID, 'status': 'done'}, answer(products=[None])):
            with self.subTest(page=page), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(Failure):
                    run(SEARCH, 'search', directory, client=FakeClient([page]), clock=FakeClock())
                self.assertFalse((Path(directory) / 'pages-python.json').exists())
        with self.assertRaises(Failure):
            self.run_client(FakeClient([answer(next_cursor='same'), answer(next_cursor='same')]))

    def test_mismatched_id_and_timeout(self):
        with self.assertRaises(Failure):
            self.run_client(FakeClient([answer(next_cursor='next'), answer(rid=RID2)]))
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(Failure):
            run(SEARCH, 'search', directory, client=FakeClient([{'request_id': RID, 'status': 'working'}]), clock=FakeClock(1))
        with patch('lib.client.time.monotonic', side_effect=[100, 701]):
            clock = Clock()
            with self.assertRaises(Failure):
                clock.remaining()

    def test_walmart_payload_preserves_required_www_slug_path(self):
        cfg = {'brand': 'walmart', 'domains': ['walmart.com'], 'product_endpoint': 'walmart.product_details', 'search_endpoint': 'walmart.search'}
        client = FakeClient([answer()])
        run('https://walmart.com/ip/good-slug_1/12345', 'product', self.directory, cfg=cfg, client=client, clock=FakeClock())
        self.assertEqual(client.calls[0][1]['url'], 'https://www.walmart.com/ip/good-slug_1/12345')
        self.assertEqual(client.calls[0][1]['max_credits'], 1)
        self.assertEqual(canonical_url('https://walmart.com/search?q=coffee', 'search', cfg), 'https://www.walmart.com/search?q=coffee')
        for path in ('/ip/123', '/ip/slug/0', '/ip/slug/0123', '/ip/slug/123?x=y', '/ip/bad%20slug/123', '/ip/slug/123/'):
            with self.subTest(path=path), self.assertRaises(Failure):
                canonical_url('https://www.walmart.com' + path, 'product', cfg)

    def test_http_deadline_and_redirects_never_forward(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://evil.example'))
        with self.assertRaises(Failure):
            _http('POST', 'https://evil.example', {}, {}, FakeClock())
        with patch('lib.client.threading.Thread') as thread, patch('lib.client.queue.Queue') as factory:
            factory.return_value.get.side_effect = queue.Empty
            with self.assertRaises(Failure):
                http('POST', API, {}, {}, FakeClock(3))
            factory.return_value.get.assert_called_once_with(timeout=3)
            self.assertTrue(thread.call_args.kwargs['daemon'])

    def test_sse_http_stops_at_matching_event_without_eof(self):
        notification = b'data: {"jsonrpc":"2.0","method":"notice"}\r\n\r\n'
        event = ('data: ' + json.dumps({'jsonrpc': '2.0', 'id': 7, 'result': {'text': 'caf\u00e9'}}, ensure_ascii=False) + '\r\n\r\n').encode()
        raw = notification + event
        chunks = [bytes([byte]) for byte in raw[:-1]] + [raw[-1:] + b'data: {unfinished']
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.headers = {'Content-Type': 'text/event-stream; charset=utf-8'}
        response.read1.side_effect = chunks + [AssertionError('Must not await EOF after matching event')]
        with patch('lib.client.os.environ', {'DATAWEBOT_API_KEY': 'offline-placeholder'}), patch('lib.client.build_opener') as opener:
            opener.return_value.open.return_value = response
            status, headers, text = _http('POST', MCP_URL, {'jsonrpc': '2.0', 'id': 7}, {}, FakeClock())
        self.assertEqual(text, raw.decode())
        self.assertEqual(decode_rpc(text, headers['Content-Type'], 7), {'text': 'caf\u00e9'})
        self.assertEqual(response.read1.call_count, len(chunks))
        self.assertEqual(status, 200)

    def test_post_retry_after_is_durable_before_identical_manual_replay(self):
        sent = []
        clock = FakeClock()
        def transport(method, url, body, headers, deadline):
            sent.append(copy.deepcopy(body))
            if len(sent) == 1:
                return 429, {'Retry-After': '7'}, ''
            self.assertEqual(clock.waits, [7])
            return 200, {}, json.dumps(answer())
        with patch('lib.client.time.time', return_value=1000):
            with self.assertRaises(Failure):
                self.run_client(Rest(clock, transport), clock=clock)
            saved = json.loads((self.directory / 'checkpoint-python.json').read_text())
            self.assertEqual(saved['next_poll_at'], 1007)
            self.assertNotIn('request_id', saved)
            self.assertEqual(clock.waits, [])
            clock = FakeClock()
            self.run_client(Rest(clock, transport), clock=clock)
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(sent[1]['request_key'], saved['payload']['request_key'])

    def test_read_failure_also_persists_retry_after(self):
        with patch('lib.client.time.time', return_value=1000):
            with self.assertRaises(Failure):
                self.run_client(FakeClient([{'request_id': RID, 'status': 'working', 'check_after_seconds': 2}]), clock=FakeClock(1))
            clock = FakeClock()
            def transport(method, url, body, headers, deadline):
                self.assertEqual(method, 'GET')
                return 200, {'Retry-After': '9'}, 'invalid json'
            with self.assertRaises(Failure):
                self.run_client(Rest(clock, transport), clock=clock)
        self.assertEqual(json.loads((self.directory / 'checkpoint-python.json').read_text())['next_poll_at'], 1009)

    def test_http_error_preserves_retry_after_without_response_body(self):
        from urllib.error import HTTPError
        with patch.dict(os.environ, {'DATAWEBOT_API_KEY': 'synthetic-test-key'}), patch('lib.client.build_opener') as factory:
            factory.return_value.open.side_effect = HTTPError(API, 503, 'Unavailable', {'Retry-After': '30'}, None)
            self.assertEqual(_http('POST', API, {}, {}, FakeClock()), (503, {'Retry-After': '30'}, ''))

    def test_empty_success_is_unchanged(self):
        page = answer()
        self.assertEqual(self.run_client(FakeClient([page])), [page])

    def test_rest_transport_get_retry_and_no_post_retry(self):
        calls = []
        replies = [(429, {'Retry-After': '6'}, ''), (200, {}, json.dumps(answer()))]
        def transport(*args):
            calls.append(args)
            return replies.pop(0)
        clock = FakeClock()
        client = Rest(clock, transport)
        self.assertEqual(client.results(RID, 'a&b'), answer())
        self.assertEqual(clock.waits, [6])
        self.assertIn('cursor=a%26b', calls[0][1])
        replies.append((429, {'Retry-After': '5'}, 'secret response'))
        with self.assertRaises(Failure):
            client.submit({})
        self.assertEqual(len(calls), 3)


class DomainTests(unittest.TestCase):
    def test_urls_both_brands(self):
        for cfg, good, search in [({'brand': 'amazon', 'domains': ['amazon.com']}, 'https://www.amazon.com/gp/product/B012345678?tag=x', 'https://amazon.com/s?k=coffee'),
                                  ({'brand': 'walmart', 'domains': ['walmart.com']}, 'https://walmart.com/ip/slug/123', 'https://walmart.com/search?q=coffee')]:
            canonical_url(good, 'product', cfg)
            canonical_url(search, 'search', cfg)
            for bad in [search + '&page=2', search + '&q=other', search.replace('https:', 'http:'), search.replace('://', '://user@'), search.replace('.com', '.com.evil'), search + '#x']:
                with self.subTest(bad=bad), self.assertRaises(Failure):
                    canonical_url(bad, 'search', cfg)
        with self.assertRaises(Failure):
            canonical_url('https://' + HOST + ':443' + '/s?k=x', 'search')

    def test_price_review_decimal_null_currency_identity(self):
        pages = [answer(products=[product()])]
        proposal = price_proposal(pages, TARGET, '13.00', 'USD')
        self.assertEqual(proposal['proposal']['observed_price'], '12.5')
        self.assertEqual(proposal['source'], TARGET)
        for price in (None, {'amount': None, 'currency': 'USD'}, {'amount': 1, 'currency': 'EUR'}):
            result = price_proposal([answer(products=[product(price=price)])], TARGET, '20', 'USD')
            self.assertIsNone(result['proposal'])
            self.assertTrue(result['reason'])
        self.assertIsNone(price_proposal([answer()], TARGET, '20', 'USD')['proposal'])
        with self.assertRaises(Failure):
            price_proposal([answer(products=[product(url=PRODUCT.replace('123', '999'))])], TARGET, '20', 'USD')
        for value in ('NaN', '-1', 'Infinity', 'bad', True):
            with self.assertRaises(Failure):
                money(value)

    def test_invalid_price_cli_before_network_and_arbitrary_cwd(self):
        with patch('lib.workflows.run') as network, patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(main('price-matching', [PRODUCT, 'NaN', 'USD', '/unused']), 1)
            network.assert_not_called()
        script = Path(__file__).resolve().parents[1] / 'examples/price-matching/python.py'
        result = subprocess.run([sys.executable, str(script), PRODUCT, '-1', 'USD', '/unused'], cwd='/tmp', capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('ModuleNotFoundError', result.stderr)

    def test_inventory_baseline_transition_dedup_unknown_and_separate_products(self):
        with tempfile.TemporaryDirectory() as directory:
            history, output = Path(directory) / 'history.json', Path(directory) / 'alert.json'
            self.assertIsNone(inventory_alert([answer(products=[product()])], TARGET, history, output)['event'])
            pages = [answer(rid=RID2, products=[product(availability='out_of_stock')])]
            alert = inventory_alert(pages, TARGET, history, output)
            self.assertEqual(alert['event']['from'], 'in_stock')
            self.assertEqual(inventory_alert(pages, TARGET, history, output), alert)
            saved = history.read_bytes()
            inventory_alert([answer(products=[product(availability='maybe')])], TARGET, history, output)
            self.assertEqual(history.read_bytes(), saved)
            inventory_alert([answer(rid='33333333-3333-4333-8333-333333333333', products=[product()], meta={})], TARGET, history, output)
            self.assertEqual(history.read_bytes(), saved)
            other_url = PRODUCT.replace('123', '999')
            inventory_alert([answer(products=[product(url=other_url)])], canonical_url(other_url, 'product'), history, output)
            self.assertEqual(len(json.loads(history.read_text())), 2)

    def test_csv_quoting_formulas_and_missing_not_zero(self):
        title = '=HYPERLINK("bad"),\ncoffee'
        text = products_csv([answer(products=[product(title=title, price=None)])])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[1][1], "'" + title)
        self.assertEqual(rows[1][2], '')
        for value in ([], {'amount': '12', 'currency': 'USD'}, {'amount': 3, 'currency': 2}):
            with self.assertRaises(Failure):
                products_csv([answer(products=[product(price=value)])])


class MCPTests(unittest.TestCase):
    def transport(self, wrapped, sse=False):
        calls = []
        def transport(method, url, body, headers, clock):
            calls.append((body, headers))
            name = body.get('params', {}).get('name')
            if body['method'] == 'notifications/initialized':
                return 202, {}, ''
            if body['method'] == 'initialize':
                result = {'protocolVersion': '2025-03-26', 'capabilities': {}}
            else:
                output = wrapped[name].pop(0)
                result = {'structuredContent': output, 'content': [{'type': 'text', 'text': json.dumps(output)}]}
            rpc = json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': result})
            return 200, {'Content-Type': 'text/event-stream' if sse else 'application/json', 'Mcp-Session-Id': 'test-session'}, ('event: message\ndata: ' + rpc + '\n\n') if sse else rpc
        return transport, calls

    def wrapped(self, done):
        return {'discover': [{'data': {'tools': [{'name': n, 'input_schema': {}} for n in ['find_endpoints', 'submit_job', 'get_results']]}}],
                'find_endpoints': [{'data': {'endpoints': [{'endpoint': CFG['search_endpoint'], 'available': True}]}}],
                'submit_job': [done], 'get_results': []}

    def test_immediate_completion_without_id_json_and_sse_cache_raw(self):
        for sse in (False, True):
            raw = {'data': {'products': []}, 'meta': {'as_of': '2026-10-27T10:00:00Z'}}
            wrapped = self.wrapped(raw)
            transport, calls = self.transport(wrapped, sse)
            with tempfile.TemporaryDirectory() as directory:
                clock = FakeClock()
                pages = run(SEARCH, 'search', directory, mode='mcp', clock=clock, client=MCP(clock, transport))
                self.assertEqual(pages, [raw])
                self.assertEqual(json.loads((Path(directory) / 'pages-python.json').read_text()), [raw])
                self.assertEqual(run(SEARCH, 'search', directory, mode='mcp', client=MCP(clock, transport)), pages)
                self.assertEqual(len(calls), 5)
                self.assertEqual(calls[1][1]['Mcp-Session-Id'], 'test-session')
                self.assertEqual(calls[-1][0]['params']['arguments']['max_pages'], 1)

    def test_working_and_cursor_final_without_id(self):
        first = {'data': {'status': 'working', 'request_id': RID, 'check_after_seconds': 3}}
        middle = {'data': {'products': [], 'request_id': RID, 'next_cursor': 'next'}}
        last = {'data': {'products': [product()]}, 'meta': {'country': 'US'}}
        wrapped = self.wrapped(first)
        wrapped['get_results'] = [middle, last]
        transport, calls = self.transport(wrapped)
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            pages = run(SEARCH, 'search', directory, mode='mcp', clock=clock, client=MCP(clock, transport))
            self.assertEqual(pages, [middle, last])
            self.assertEqual(calls[-1][0]['params']['arguments'], {'request_id': RID, 'cursor': 'next'})
            self.assertEqual(run(SEARCH, 'search', directory, mode='mcp'), pages)

    def test_mcp_known_id_resume_never_submits_again(self):
        first = {'data': {'status': 'working', 'request_id': RID, 'check_after_seconds': 3}}
        transport, calls = self.transport(self.wrapped(first))
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock(1)
            with self.assertRaises(Failure):
                run(SEARCH, 'search', directory, mode='mcp', clock=clock, client=MCP(clock, transport))
            last = {'data': {'products': []}, 'meta': {'country': 'US'}}
            wrapped = self.wrapped({'data': {'products': []}})
            wrapped['get_results'] = [last]
            resumed_transport, resumed_calls = self.transport(wrapped)
            clock = FakeClock()
            self.assertEqual(run(SEARCH, 'search', directory, mode='mcp', clock=clock, client=MCP(clock, resumed_transport)), [last])
            names = [c[0].get('params', {}).get('name') for c in resumed_calls]
            self.assertIn('get_results', names)
            self.assertNotIn('submit_job', names)

    def test_prepare_honors_retry_after_between_intermediate_rpcs(self):
        clock = FakeClock()
        transport, calls = self.transport(self.wrapped({'data': {'products': []}}))
        def throttled(method, url, body, headers, deadline):
            method_name = body['method']
            tool = body.get('params', {}).get('name')
            expected = [] if method_name == 'initialize' else [3] if method_name == 'notifications/initialized' else [3, 4] if tool == 'discover' else [3, 4, 5]
            self.assertEqual(clock.waits, expected)
            status, returned_headers, raw = transport(method, url, body, headers, deadline)
            delay = 3 if method_name == 'initialize' else 4 if method_name == 'notifications/initialized' else 5 if tool == 'discover' else 0
            returned_headers['Retry-After'] = str(delay)
            return status, returned_headers, raw
        MCP(clock, throttled).prepare(HOST, CFG['search_endpoint'])
        self.assertEqual(clock.waits, [3, 4, 5])

    def test_error_wrapping_and_missing_tools(self):
        with self.assertRaises(Failure):
            decode_rpc(json.dumps({'jsonrpc': '2.0', 'id': 1, 'error': {'message': 'secret'}}), 'application/json', 1)
        with self.assertRaises(Failure):
            tool_content({'isError': True, 'structuredContent': {'error': 'secret'}})
        self.assertEqual(tool_content({'content': [{'type': 'text', 'text': '{"data":{"products":[]}}'}]}), {'data': {'products': []}})
        wrapped = self.wrapped({'data': {'products': []}})
        wrapped['discover'] = [{'data': {'tools': []}}]
        transport, calls = self.transport(wrapped)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(Failure):
            clock = FakeClock()
            run(SEARCH, 'search', directory, mode='mcp', clock=clock, client=MCP(clock, transport))
        self.assertNotIn('submit_job', [c[0].get('params', {}).get('name') for c in calls])


if __name__ == '__main__':
    unittest.main()
