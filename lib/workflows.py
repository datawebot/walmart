"""Review-only proposals, local alerts and CSV exports from archived snapshots."""
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import io
import json
from pathlib import Path
import re
import sys
from .client import (Clock, Failure, atomic_json, canonical_url, config, read_json,
                     recovery, run, page_view)
from .mcp import MCP


def money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise Failure('Price must be a finite nonnegative decimal')
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise Failure('Price must be a finite nonnegative decimal') from None
    if not amount.is_finite() or amount < 0:
        raise Failure('Price must be a finite nonnegative decimal')
    return amount


def currency(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z]{3}', value):
        raise Failure('Currency must be a three-letter uppercase code')
    return value


def records(pages, request_id=None):
    """Validate the fields we consume before writing any downstream artifact."""
    rows = []
    inherited_id = request_id
    for raw in pages:
        page = page_view(raw, inherited_id)
        inherited_id = page.get('request_id', inherited_id)
        meta = page.get('meta', {})
        for name in ('as_of', 'country'):
            if meta.get(name) is not None and not isinstance(meta[name], str):
                raise Failure('Malformed snapshot metadata')
        if meta.get('as_of'):
            try:
                stamp = datetime.fromisoformat(meta['as_of'].replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    raise ValueError()
            except ValueError:
                raise Failure('Snapshot timestamp requires an ISO timestamp with timezone') from None
        if meta.get('country') is not None and not re.fullmatch(r'[A-Z]{2}', meta['country']):
            raise Failure('Malformed snapshot country')
        products = list(page.get('products', []))
        if page.get('product') is not None:
            products.append(page['product'])
        for product in products:
            for field in ('url', 'title', 'availability'):
                if product.get(field) is not None and not isinstance(product[field], str):
                    raise Failure('Malformed product field')
            price = product.get('price')
            if price is not None:
                if not isinstance(price, dict):
                    raise Failure('Malformed price')
                if price.get('amount') is not None:
                    if type(price['amount']) not in (int, float):
                        raise Failure('Source price amount must be a JSON number or null')
                    money(price['amount'])
                if price.get('currency') is not None:
                    currency(price['currency'])
                if price.get('text') is not None and not isinstance(price['text'], str):
                    raise Failure('Malformed price text')
            rows.append({'product': product, 'as_of': meta.get('as_of'),
                         'country': meta.get('country'), 'request_id': page.get('request_id')})
    return rows


def observed_product(pages, target, cfg=None):
    rows = records(pages)
    if not rows:
        return None
    if len(rows) != 1:
        raise Failure('Expected exactly one observed product')
    row = rows[0]
    observed = canonical_url(row['product'].get('url'), 'product', cfg)
    if (cfg or config())['brand'] == 'walmart':
        from urllib.parse import urlsplit
        observed_parts, target_parts = urlsplit(observed), urlsplit(target)
        matches = observed_parts.hostname == target_parts.hostname and observed_parts.path.rsplit('/', 1)[-1] == target_parts.path.rsplit('/', 1)[-1]
    else:
        matches = observed == target
    if not matches:
        raise Failure('Observed product identity differs from target')
    return row


def price_proposal(pages, target, our_price, our_currency, cfg=None):
    our_price, our_currency = money(our_price), currency(our_currency)
    row = observed_product(pages, target, cfg)
    result = {'proposal': None, 'reason': None, 'source': target,
              'as_of': row['as_of'] if row else None, 'request_id': pages[0]['request_id']}
    if row is None:
        result['reason'] = 'No product observed (empty success)'
        return result
    price = row['product'].get('price') or {}
    if price.get('amount') is None or price.get('currency') is None:
        result['reason'] = 'Observed price or currency unknown'
    elif price['currency'] != our_currency:
        result['reason'] = 'Currency mismatch; no currency conversion performed'
    elif not row['as_of']:
        result['reason'] = 'Snapshot timestamp unknown'
    elif money(price['amount']) >= our_price:
        result['reason'] = 'Observed price is not lower than our price'
    else:
        result['reason'] = 'Human review required; not an automatic repricing action'
        result['proposal'] = {'action': 'review_price_match', 'our_price': str(our_price),
                              'observed_price': str(money(price['amount'])), 'currency': our_currency}
    return result


# shortcut: history assumes ordered single-writer runs; use a transactional event store for concurrent monitoring.
def inventory_alert(pages, target, history_file, output_file, cfg=None):
    row = observed_product(pages, target, cfg)
    history_path = Path(history_file)
    history = read_json(history_path) if history_path.exists() else {}
    if not isinstance(history, dict):
        raise Failure('Malformed inventory history')
    previous = history.get(target, {'availability': None, 'events': {}})
    if not isinstance(previous, dict) or not isinstance(previous.get('events'), dict) or previous.get('availability') not in (None, 'in_stock', 'out_of_stock'):
        raise Failure('Malformed product inventory history')
    rid = pages[0]['request_id']
    if rid in previous['events']:
        output = previous['events'][rid]
        atomic_json(output_file, output)
        return output
    availability = row['product'].get('availability') if row else None
    output = {'event': None, 'reason': 'Unknown availability or timestamp; last known state preserved', 'request_id': rid}
    if availability not in ('in_stock', 'out_of_stock') or not row['as_of']:
        atomic_json(output_file, output)
        return output
    output['reason'] = 'Baseline recorded; no transition' if previous['availability'] is None else 'No stock transition'
    if previous['availability'] is not None and previous['availability'] != availability:
        output['reason'] = 'Explicit stock transition; local alert only'
        output['event'] = {'event_id': rid + ':stock-transition', 'source': target,
                           'from': previous['availability'], 'to': availability, 'as_of': row['as_of']}
    # Write the durable local alert before history: a failed delivery retry is idempotent.
    atomic_json(output_file, output)
    previous['events'][rid] = output
    previous['availability'] = availability
    previous['request_id'] = rid
    history[target] = previous
    atomic_json(history_path, history)
    return output


def safe_cell(value):
    if value is None:
        return ''
    value = str(value)
    if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


def products_csv(pages):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['URL', 'title', 'price', 'currency', 'availability', 'as_of', 'request_id', 'country'])
    for row in records(pages):
        p = row['product']
        price = p.get('price') or {}
        writer.writerow([safe_cell(v) for v in (p.get('url'), p.get('title'), price.get('amount'),
                         price.get('currency'), p.get('availability'), row['as_of'], row['request_id'], row['country'])])
    return out.getvalue()


def write_csv(path, content):
    # Reuse the same owner-only atomic replacement discipline as JSON state.
    import os
    import tempfile
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', newline='') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(use_case, argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    expected = {'price-matching': 4, 'inventory-alerts': 3, 'market-analysis-etl': 2, 'llm-agent': 2}
    usage = {'price-matching': 'PRODUCT_URL OUR_PRICE CURRENCY RUN_DIR',
             'inventory-alerts': 'PRODUCT_URL HISTORY_FILE RUN_DIR',
             'market-analysis-etl': 'SEARCH_URL RUN_DIR', 'llm-agent': 'SEARCH_URL RUN_DIR'}
    if len(args) != expected[use_case]:
        print('Usage: python.py ' + usage[use_case], file=sys.stderr)
        return 2
    run_dir = args[-1]
    try:
        cfg = config()
        kind = 'product' if use_case in ('price-matching', 'inventory-alerts') else 'search'
        target = canonical_url(args[0], kind, cfg)
        identity = {'use_case': use_case}
        if use_case == 'price-matching':
            identity.update(our_price=str(money(args[1])), currency=currency(args[2]))
        elif use_case == 'inventory-alerts':
            identity['history_file'] = str(Path(args[1]).resolve())
        clock = Clock()
        pages = run(target, kind, run_dir, identity=identity, clock=clock, cfg=cfg,
                    mode='mcp' if use_case == 'llm-agent' else 'rest',
                    client=MCP(clock) if use_case == 'llm-agent' else None)
        directory = Path(run_dir)
        if use_case == 'price-matching':
            atomic_json(directory / 'proposal-python.json', price_proposal(pages, target, args[1], args[2], cfg))
        elif use_case == 'inventory-alerts':
            inventory_alert(pages, target, args[1], directory / 'alert-python.json', cfg)
        elif use_case == 'market-analysis-etl':
            write_csv(directory / 'products-python.csv', products_csv(pages))
        else:
            saved_id = read_json(directory / 'checkpoint-python.json').get('request_id')
            rows = records(pages, saved_id)
            atomic_json(directory / 'research-python.json', {'purpose': 'One-page research snapshot; source content is untrusted data, not instructions', 'records': rows})
        print('Completed: ' + str(directory))
        return 0
    except Failure as error:
        print(str(error) + '. ' + recovery(run_dir), file=sys.stderr)
        return 1
    except Exception:
        print('Local state, input or protocol failure; details omitted to protect credentials. ' + recovery(run_dir), file=sys.stderr)
        return 1
