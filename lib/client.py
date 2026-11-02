"""Small, durable one-page datawebot client (Python 3.11+, standard library)."""
import email.utils
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import queue
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

API = 'https://api.datawebot.com/v1/requests'
MCP_URL = 'https://mcp.datawebot.com/mcp'


class Failure(Exception):
    pass


def config():
    return json.loads((Path(__file__).resolve().parent.parent / 'config.json').read_text())


def canonical_url(url, kind, cfg=None):
    cfg = cfg or config()
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url) or len(url) > 2048:
        raise Failure('Invalid source URL')
    try:
        p = urlsplit(url)
        host = p.hostname or ''
    except ValueError:
        raise Failure('Invalid source URL') from None
    host = host.removeprefix('www.')
    if p.scheme != 'https' or p.username is not None or p.password is not None or p.netloc.lower() not in (host, 'www.' + host) or host not in cfg['domains'] or p.fragment:
        raise Failure('Use HTTPS on an exact configured source hostname without credentials, ports or fragment')
    amazon = cfg['brand'] == 'amazon'
    if kind == 'search':
        key, path = ('k', '/s') if amazon else ('q', '/search')
        query = parse_qsl(p.query, keep_blank_values=True)
        if p.path != path or len(query) != 1 or query[0][0] != key or not query[0][1].strip():
            raise Failure('Search requires one keyword parameter only; no filters or source pagination')
        keyword = query[0][1].strip()
        if len(keyword) > 200 or re.search(r'[\x00-\x1f\x7f\ufffd]', keyword):
            raise Failure('Search keyword is too long or contains control characters')
        return 'https://' + (host if amazon else 'www.' + host) + path + '?' + urlencode({key: keyword})
    if not amazon and (p.query or '?' in url):
        raise Failure('Walmart product URLs must not contain query parameters')
    pattern = r'/(?:dp|gp/product)/([A-Za-z0-9]{10})/?' if amazon else r'/ip/[A-Za-z0-9_-]{1,400}/([1-9][0-9]{0,19})'
    match = re.fullmatch(pattern, p.path)
    if not match:
        raise Failure('Unsupported product path')
    path = '/dp/' + match[1].upper() if amazon else p.path
    return 'https://' + (host if amazon else 'www.' + host) + path


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False, allow_nan=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (ValueError, OSError):
        raise Failure('Cannot read local JSON state') from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Clock:
    def __init__(self):
        self.deadline = time.monotonic() + 600

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Failure('Ten-minute deadline reached')
        return remaining

    def wait(self, seconds):
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise Failure('Invalid server wait')
        if seconds >= self.remaining():
            raise Failure('Server wait exceeds ten-minute deadline; resume later')
        time.sleep(seconds)


def retry_after(headers):
    value = {k.lower(): v for k, v in headers.items()}.get('retry-after', '0')
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, email.utils.parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError):
            raise Failure('Invalid Retry-After header') from None


def _http(method, url, body, headers, clock):
    p = urlsplit(url)
    if not (url == MCP_URL or (p.scheme == 'https' and p.netloc == 'api.datawebot.com' and p.path.startswith('/v1/requests'))):
        raise Failure('Refusing non-public API destination')
    key = os.environ.get('DATAWEBOT_API_KEY')
    if not key:
        raise Failure('Set DATAWEBOT_API_KEY in the environment')
    data = None if body is None else json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
    req = Request(url, data=data, method=method, headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', **headers})
    try:
        with build_opener(NoRedirect()).open(req, timeout=clock.remaining()) as response:
            chunks = []
            sse_pending = b''
            sse = 'text/event-stream' in response.headers.get('Content-Type', '').lower()
            rpc_id = body.get('id') if isinstance(body, dict) else None
            while True:
                remaining = clock.remaining()
                # urllib exposes the underlying socket; update it as the deadline shrinks.
                sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                if sock is not None:
                    sock.settimeout(remaining)
                chunk = response.read1(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if sse and rpc_id is not None:
                    sse_pending += chunk
                    while boundary := re.search(br'\r?\n\r?\n', sse_pending):
                        event = sse_pending[:boundary.start()]
                        sse_pending = sse_pending[boundary.end():]
                        data_lines = [line[5:].lstrip(b' ') for line in event.replace(b'\r\n', b'\n').split(b'\n') if line.startswith(b'data:')]
                        if not data_lines:
                            continue
                        message = json.loads(b'\n'.join(data_lines))
                        if isinstance(message, dict) and message.get('jsonrpc') == '2.0' and message.get('id') == rpc_id and ('result' in message or 'error' in message):
                            raw = b''.join(chunks)
                            clock.remaining()
                            return response.status, dict(response.headers), raw[:len(raw) - len(sse_pending)].decode('utf-8')
            clock.remaining()
            return response.status, dict(response.headers), b''.join(chunks).decode('utf-8')
    except HTTPError as error:
        try:
            return error.code, dict(error.headers), ''
        finally:
            error.close()
    except Failure:
        raise
    except Exception:
        raise Failure('HTTP transport failure; response omitted') from None


def http(method, url, body, headers, clock):
    # Socket timeouts alone do not bound DNS resolution or a trickling response.
    result = queue.Queue(maxsize=1)
    def worker():
        try:
            result.put((True, _http(method, url, body, headers, clock)))
        except Exception as error:
            result.put((False, error))
    threading.Thread(target=worker, daemon=True).start()
    try:
        ok, value = result.get(timeout=clock.remaining())
    except queue.Empty:
        raise Failure('Ten-minute HTTP deadline reached; submission outcome may be unknown') from None
    if not ok:
        raise value
    return value


class Rest:
    def __init__(self, clock, transport=http):
        self.clock, self.transport = clock, transport
        self.retry = 0

    def call(self, method, url, body=None):
        while True:
            self.clock.remaining()
            status, headers, raw = self.transport(method, url, body, {'Accept': 'application/json'}, self.clock)
            self.retry = retry_after(headers)
            if status == 429 and method == 'GET':
                self.clock.wait(max(2, self.retry))
                continue
            if not 200 <= status < 300:
                raise Failure('HTTP failure (status %d); no automatic submission retry' % status)
            try:
                return json.loads(raw)
            except ValueError:
                raise Failure('Malformed API JSON') from None

    def submit(self, payload):
        return self.call('POST', API, payload)

    def results(self, request_id, cursor=None):
        return self.call('GET', API + '/' + request_id + ('?' + urlencode({'cursor': cursor}) if cursor else ''))


def valid_id(value):
    if not isinstance(value, str):
        raise Failure('Missing request ID')
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise Failure('Invalid request ID') from None
    return value


def page_view(page, request_id=None):
    """Read current MCP envelopes without changing the archived response."""
    if not isinstance(page, dict) or 'data' not in page:
        return page
    data = page['data']
    if not isinstance(data, dict):
        raise Failure('Malformed MCP data envelope')
    view = {**data, 'meta': page.get('meta', {})}
    for key in ('error', 'errors', 'partial', 'partial_complete'):
        if page.get(key):
            view[key] = page[key]
    if 'status' not in view and ('products' in view or 'product' in view):
        view['status'] = 'done'
    if 'request_id' not in view and request_id is not None:
        view['request_id'] = request_id
    return view


def validate_page(page, request_id=None, allow_missing_id=False):
    if not isinstance(page, dict):
        raise Failure('Malformed result page')
    rid = page.get('request_id')
    if rid is not None or not (allow_missing_id and page.get('status') == 'done' and not page.get('next_cursor')):
        rid = valid_id(rid)
    if request_id is not None and rid != request_id:
        raise Failure('Result request ID changed')
    if page.get('status') not in ('working', 'done') or page.get('error') or page.get('errors') or page.get('partial') or page.get('partial_complete'):
        raise Failure('Failed, partial or malformed extraction; no downstream update')
    meta = page.get('meta', {})
    if not isinstance(meta, dict) or meta.get('partial') or meta.get('errors') or meta.get('status') in ('partial', 'PARTIAL_COMPLETE', 'failed'):
        raise Failure('Partial or malformed extraction metadata')
    if page['status'] == 'done':
        if 'products' not in page and 'product' not in page:
            raise Failure('Missing product result; empty success must be explicit')
        if 'products' in page and (not isinstance(page['products'], list) or any(not isinstance(p, dict) for p in page['products'])):
            raise Failure('Malformed products')
        if page.get('product') is not None and not isinstance(page['product'], dict):
            raise Failure('Malformed product')
        if page.get('next_cursor') is not None and (not isinstance(page['next_cursor'], str) or not page['next_cursor']):
            raise Failure('Malformed result cursor')
    return rid


def run(url, kind, run_dir, *, identity=None, clock=None, client=None, mode='rest', cfg=None):
    """A retry replays the saved body/key only if no request ID was received."""
    cfg = cfg or config()
    url = canonical_url(url, kind, cfg)
    clock = clock or Clock()
    directory = Path(run_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    checkpoint = directory / 'checkpoint-python.json'
    intended = {'mode': mode, 'url': url, 'kind': kind, 'inputs': identity or {}}
    payload = {'capability': 'endpoint', 'endpoint': cfg[kind + '_endpoint'], 'url': url,
               'format': 'structured', 'max_pages': 1, 'max_credits': 1 if kind == 'product' else 5,
               'request_key': uuid.uuid4().hex}
    # Exclusive creation prevents a concurrent starter from inventing a second key.
    try:
        fd = os.open(checkpoint, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        state = read_json(checkpoint)
    else:
        state = {'identity': intended, 'payload': payload}
        with os.fdopen(fd, 'w') as out:
            json.dump(state, out)
            out.flush()
            os.fsync(out.fileno())
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    if state.get('identity') != intended:
        raise Failure('Run directory belongs to different input; choose a new directory')
    expected = dict(payload, request_key=state['payload'].get('request_key'))
    if state['payload'] != expected or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', expected['request_key'] or ''):
        raise Failure('Saved payload differs from this request')
    if state.get('complete'):
        pages = read_json(directory / 'pages-python.json')
        if not isinstance(pages, list) or not pages:
            raise Failure('Malformed completed archive')
        for page in pages:
            view = page_view(page, state.get('request_id')) if mode == 'mcp' else page
            validate_page(view, state.get('request_id'), allow_missing_id=mode == 'mcp')
        return pages
    client = client or Rest(clock)

    def receive(operation, *args):
        try:
            return operation(*args)
        finally:
            retry = getattr(client, 'retry', 0)
            if retry > 0:
                state['next_poll_at'] = max(state.get('next_poll_at', 0), time.time() + retry)
                atomic_json(checkpoint, state)

    delay = state.get('next_poll_at', 0) - time.time()
    if delay > 0 or state.get('request_id'):
        clock.wait(max(2 if state.get('request_id') else 0, delay))
    if mode == 'mcp':
        receive(client.prepare, urlsplit(url).hostname, payload['endpoint'])
    if state.get('request_id'):
        rid = valid_id(state['request_id'])
        page = receive(client.results, rid)
    else:
        page = receive(client.submit, state['payload'])
        # Persist an ID even if the server then reports failure/partial.
        view = page_view(page) if mode == 'mcp' else page
        value = view.get('request_id') if isinstance(view, dict) else None
        rid = valid_id(value) if value is not None else None
        if rid is not None:
            state['request_id'] = rid
            if view.get('status') == 'working':
                delay = view.get('check_after_seconds', 2)
                if isinstance(delay, (int, float)) and not isinstance(delay, bool) and math.isfinite(delay):
                    state['next_poll_at'] = time.time() + max(2, delay, getattr(client, 'retry', 0))
            atomic_json(checkpoint, state)
        elif mode != 'mcp':
            raise Failure('Missing request ID')
    pages, cursors = [], set()
    cursor = None
    while True:
        clock.remaining()
        view = page_view(page, rid) if mode == 'mcp' else page
        validate_page(view, rid, allow_missing_id=mode == 'mcp')
        if view['status'] == 'working':
            delay = view.get('check_after_seconds', 2)
            if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not math.isfinite(delay):
                raise Failure('Malformed polling delay')
            wait = max(2, delay, getattr(client, 'retry', 0))
            state['next_poll_at'] = time.time() + wait
            atomic_json(checkpoint, state)
            clock.wait(wait)
            page = receive(client.results, rid, cursor)
            continue
        pages.append(page)
        cursor = view.get('next_cursor')
        if cursor is None:
            break
        if cursor in cursors:
            raise Failure('Repeated result cursor')
        cursors.add(cursor)
        clock.wait(max(2, getattr(client, 'retry', 0)))
        page = receive(client.results, rid, cursor)
    atomic_json(directory / 'pages-python.json', pages)
    state['complete'] = True
    atomic_json(checkpoint, state)
    return pages


def recovery(run_dir):
    """Never include arbitrary exception strings or server bodies in diagnostics."""
    try:
        state = read_json(Path(run_dir) / 'checkpoint-python.json')
        rid = valid_id(state.get('request_id'))
    except (Failure, TypeError):
        return 'Rerun the identical command and run directory to replay the saved key; do not delete the checkpoint.'
    return 'Saved request_id=' + rid + '; rerun the identical command and directory for GET-only recovery.'
