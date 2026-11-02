"""Offline public-boundary checks of the self-contained Python and Node CLIs."""
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BRAND = re.search(r'"endpoint": "(amazon|walmart)\.product_details"',
                  (ROOT / 'examples/price-matching/python.py').read_text())[1]
DOMAINS = ('amazon.com amazon.sg amazon.co.jp amazon.co.uk amazon.de amazon.fr '
           'amazon.com.au amazon.it amazon.es amazon.ca amazon.ae amazon.sa amazon.eg '
           'amazon.com.tr amazon.com.mx amazon.pl amazon.se amazon.nl amazon.com.be amazon.ie').split() if BRAND == 'amazon' else ['walmart.com']
CASES = ['price-matching', 'inventory-alerts', 'market-analysis-etl', 'llm-agent']
KEY, ID, ID2 = 'caller_stable_key', '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'
META = {'as_of': '2026-01-01T00:00:00Z', 'country': 'US'}
API, MCP = 'https://api.datawebot.com/v1/requests', 'https://mcp.datawebot.com/mcp'
SEARCH_ENDPOINT = BRAND + ('.search_items' if BRAND == 'amazon' else '.search')


def source(domain, search=False):
    path = ('/s?k=coffee' if BRAND == 'amazon' else '/search?q=coffee') if search else (
        '/dp/B012345678' if BRAND == 'amazon' else '/ip/coffee/123456')
    return 'https://www.' + domain + path


def answer(url, **changes):
    data = {'request_id': ID, 'status': 'done', 'meta': dict(META), 'products': [
        {'url': url, 'title': 'Coffee, "dark"\nroast', 'price': {'amount': 9, 'currency': 'USD'}, 'availability': 'in_stock'}]}
    data.update(changes)
    return data


def replies(case, data, cursor=False):
    if case != 'llm-agent':
        return [{'body': data}]
    def rpc(i, value):
        return {'body': {'jsonrpc': '2.0', 'id': i, 'result': value}}
    def wrapped(value):
        return {'structuredContent': {'data': value, 'meta': dict(META)}, 'isError': False}
    result = [rpc(1, {'protocolVersion': '2025-03-26', 'capabilities': {}, 'serverInfo': {'name': 'fixture', 'version': '1'}}),
              {'status': 202}, rpc(3, wrapped({'tools': [{'name': n} for n in ['find_endpoints', 'submit_job', 'get_results']]})),
              rpc(4, wrapped({'endpoints': [{'endpoint': SEARCH_ENDPOINT, 'available': True}]})), rpc(5, wrapped(data))]
    if cursor:
        result.append(rpc(6, wrapped(answer(data['products'][0]['url']))))
    return result


class Examples(unittest.TestCase):
    def run_cli(self, runtime, case, url, fixture, history=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        args = {'price-matching': [url, '12.50', 'USD', KEY],
                'inventory-alerts': [url, str(history or directory / 'history.json'), KEY],
                'market-analysis-etl': [url, KEY, str(directory / 'output')],
                'llm-agent': [url, KEY]}[case]
        script = ROOT / 'examples' / case / ('python.py' if runtime == 'python' else 'node.mjs')
        env = {'DATAWEBOT_API_KEY': 'synthetic-test-key', 'DATAWEBOT_API_URL': 'https://invalid.test',
               'DATAWEBOT_MCP_URL': 'https://invalid.test'}
        calls, stdout, code = [], io.StringIO(), 0
        if runtime == 'node':
            fixture_file, log = directory / 'replies.json', directory / 'http.jsonl'
            fixture_file.write_text(json.dumps(fixture))
            proc = subprocess.run(['node', '--import', str(ROOT / 'tests/fake-api.mjs'), str(script), *args],
                                  env={**os.environ, **env, 'FAKE_REPLIES': str(fixture_file), 'FAKE_LOG': str(log)},
                                  capture_output=True, text=True, timeout=15)
            code, output = proc.returncode, proc.stdout
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            error = proc.stderr
        else:
            queue = list(fixture)
            def opener(handler):
                self.assertIsNone(handler.redirect_request(None, None, 302, '', {}, 'https://invalid.test'))
                return type('Opener', (), {'open': staticmethod(open_request)})()
            def open_request(req, timeout):
                calls.append({'url': req.full_url, 'method': req.get_method(),
                              'body': json.loads(req.data) if req.data else None, 'headers': dict(req.header_items())})
                self.assertGreater(timeout, 0)
                self.assertTrue(queue, 'Unexpected transport call')
                reply = queue.pop(0)
                response = io.BytesIO(json.dumps(reply.get('body')).encode())
                response.headers = {'Content-Type': 'application/json'}
                return response
            old_mask = os.umask(0o077)
            try:
                with patch.dict(os.environ, env), patch('sys.argv', [str(script), *args]), patch('time.sleep'), \
                     patch('urllib.request.build_opener', side_effect=opener), contextlib.redirect_stdout(stdout):
                    try:
                        runpy.run_path(str(script), run_name='__main__')
                    except SystemExit as exc:
                        code = 0 if exc.code in (None, 0) else 1
                output, error = stdout.getvalue(), ''
            finally:
                os.umask(old_mask)
        for call in calls:
            self.assertTrue(call['url'] == MCP if case == 'llm-agent' else call['url'] == API or call['url'].startswith(API + '/' + ID))
            self.assertEqual({k.lower(): v for k, v in call['headers'].items()}['authorization'], 'Bearer synthetic-test-key')
            if runtime == 'node':
                self.assertEqual(call['redirect'], 'error')
        return code, output, calls, directory, error

    def payload(self, calls, case, url):
        if case == 'llm-agent':
            bodies = [c['body'] for c in calls]
            self.assertEqual([b.get('params', {}).get('name', b['method']) for b in bodies],
                             ['initialize', 'notifications/initialized', 'discover', 'find_endpoints', 'submit_job', 'get_results'])
            self.assertNotIn('id', bodies[1])
            self.assertEqual(bodies[3]['params']['arguments'], {'site': urlsplit(url).hostname.removeprefix('www.'), 'type': 'search'})
            self.assertEqual(bodies[5]['params']['arguments'], {'request_id': ID, 'cursor': 'next/+='})
            payload = bodies[4]['params']['arguments']
        else:
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]['method'], 'POST')
            payload = calls[0]['body']
        search = case in CASES[2:]
        expected = {'endpoint': SEARCH_ENDPOINT if search else BRAND + '.product_details', 'url': url,
                    'max_pages': 1, 'format': 'structured', 'max_credits': 5 if search else 1, 'request_key': KEY}
        if case != 'llm-agent':
            expected['capability'] = 'endpoint'
        self.assertEqual(payload, expected)

    def test_every_domain_all_four_clis(self):
        for domain in DOMAINS:
            for runtime in ['python', 'node']:
                for case in CASES:
                    with self.subTest(domain=domain, runtime=runtime, case=case):
                        url = source(domain, case in CASES[2:])
                        data = answer(url)
                        if case == 'llm-agent':
                            data['next_cursor'] = 'next/+='
                        code, out, calls, directory, error = self.run_cli(runtime, case, url, replies(case, data, case == 'llm-agent'))
                        self.assertEqual(code, 0, error)
                        self.payload(calls, case, url)
                        if case == 'price-matching':
                            self.assertEqual(json.loads(out)['action'], 'review_price_match')
                            self.assertIn('does not change a price', json.loads(out)['note'])
                        elif case == 'inventory-alerts':
                            self.assertIsNone(json.loads(out))
                            self.assertEqual(json.loads((directory / 'history.json').read_text())['availability'], 'in_stock')
                        elif case == 'market-analysis-etl':
                            with (directory / 'output' / ('products-' + runtime + '.csv')).open(newline='') as stream:
                                self.assertEqual(list(csv.DictReader(stream))[0]['title'], data['products'][0]['title'])
                        else:
                            self.assertEqual(json.loads(out)[0]['data'], data)
                            self.assertEqual(json.loads(out)[0]['meta'], META)

    def test_price_null_and_currency_mismatch(self):
        url = source(DOMAINS[0])
        for runtime in ['python', 'node']:
            for price in [None, {'amount': None, 'currency': 'USD'}, {'amount': 1, 'currency': 'EUR'}]:
                with self.subTest(runtime=runtime, price=price):
                    data = answer(url)
                    data['products'][0]['price'] = price
                    code, out, calls, _, _ = self.run_cli(runtime, CASES[0], url, replies(CASES[0], data))
                    self.assertEqual(code, 0)
                    self.assertEqual(json.loads(out)['action'], 'no_proposal')
                    self.assertEqual(len(calls), 1)

    def test_inventory_ordered_transition_replay_and_unknown(self):
        url = source(DOMAINS[0])
        for runtime in ['python', 'node']:
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as tmp:
                history = Path(tmp) / 'history.json'
                baseline = answer(url)
                baseline['products'][0]['availability'] = 'out_of_stock'
                self.assertEqual(self.run_cli(runtime, CASES[1], url, replies(CASES[1], baseline), history)[0], 0)
                data = answer(url, request_id=ID2, meta={**META, 'as_of': '2026-01-02T00:00:00Z'})
                code, out, *_ = self.run_cli(runtime, CASES[1], url, replies(CASES[1], data), history)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)['from'], 'out_of_stock')
                self.assertEqual(json.loads(out)['to'], 'in_stock')
                saved = history.read_bytes()
                self.assertEqual(self.run_cli(runtime, CASES[1], url, replies(CASES[1], data), history)[1], out)
                self.assertEqual(history.read_bytes(), saved)
                data['products'][0]['availability'] = None
                self.assertNotEqual(self.run_cli(runtime, CASES[1], url, replies(CASES[1], data), history)[0], 0)
                self.assertEqual(history.read_bytes(), saved)

    def test_csv_cursors_quotes_formulas_and_null(self):
        url = source(DOMAINS[0], True)
        for runtime in ['python', 'node']:
            with self.subTest(runtime=runtime):
                data = answer(url, next_cursor='next/+=')
                second = answer(url)
                second['products'] = [{'title': text, 'price': None} for text in ['=1+1', '+SUM(A1)', '-2', '@cmd', '\ttext']]
                code, _, calls, directory, error = self.run_cli(runtime, CASES[2], url, [{'body': data}, {'body': second}])
                self.assertEqual(code, 0, error)
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[1]['method'], 'GET')
                self.assertEqual(parse_qs(urlsplit(calls[1]['url']).query), {'cursor': ['next/+=']})
                with (directory / 'output' / ('products-' + runtime + '.csv')).open(newline='') as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(rows[0]['title'], data['products'][0]['title'])
                self.assertEqual([r['title'] for r in rows[1:]], ["'" + p['title'] for p in second['products']])
                self.assertTrue(all(r['price'] == '' for r in rows[1:]))

    def test_failed_partial_and_invalid_urls(self):
        for runtime in ['python', 'node']:
            for case in CASES:
                url = source(DOMAINS[0], case in CASES[2:])
                for changes in [{'status': 'failed'}, {'partial': True}]:
                    with self.subTest(runtime=runtime, case=case, changes=changes):
                        result = self.run_cli(runtime, case, url, replies(case, answer(url, **changes)))
                        self.assertNotEqual(result[0], 0)
                        self.assertFalse((result[3] / 'history.json').exists())
                        self.assertFalse((result[3] / 'output').exists())
                for invalid in [url.replace('https:', 'http:'), url.replace('www.', 'www.evil.'), url + '#fragment', url.replace('https://', 'https://user:pass@'), url + '&extra=1' if case in CASES[2:] else url + '/wrong']:
                    with self.subTest(runtime=runtime, case=case, invalid=invalid):
                        result = self.run_cli(runtime, case, invalid, [])
                        self.assertNotEqual(result[0], 0)
                        self.assertEqual(result[2], [])

    def test_pending_polls_without_resubmitting(self):
        for runtime in ['python', 'node']:
            for case in CASES:
                with self.subTest(runtime=runtime, case=case):
                    url = source(DOMAINS[0], case in CASES[2:])
                    pending = answer(url, status='working', check_after_seconds=2)
                    fixture = replies(case, pending, case == 'llm-agent')
                    if case != 'llm-agent':
                        fixture.append({'body': answer(url)})
                    code, _, calls, _, error = self.run_cli(runtime, case, url, fixture)
                    self.assertEqual(code, 0, error)
                    self.assertEqual(len(calls), 6 if case == 'llm-agent' else 2)
                    if case == 'llm-agent':
                        self.assertEqual(calls[-1]['body']['params'], {'name': 'get_results', 'arguments': {'request_id': ID}})
                    else:
                        self.assertEqual((calls[-1]['method'], calls[-1]['url']), ('GET', API + '/' + ID))

    def test_mcp_immediate_done_without_status_or_id(self):
        url = source(DOMAINS[0], True)
        data = {'products': answer(url)['products']}
        for runtime in ['python', 'node']:
            with self.subTest(runtime=runtime):
                code, out, calls, _, error = self.run_cli(runtime, CASES[3], url, replies(CASES[3], data))
                self.assertEqual(code, 0, error)
                self.assertEqual(len(calls), 5)
                self.assertEqual(json.loads(out), [{'data': data, 'meta': META}])
