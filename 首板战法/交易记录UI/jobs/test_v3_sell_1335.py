"""Source overlay tests; all writes use isolated temporary data."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
spec = importlib.util.spec_from_file_location('quotes', Path(__file__).parents[1] / 'lib/v3_sell_1335.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with TemporaryDirectory() as temporary:
    base = Path(temporary)
    directory = base / 'data/sell_1335'
    directory.mkdir(parents=True)
    rows = [dict(program='stock', id=1, buy_date='2026-11-02', sell_date='2026-11-03', stock_code='000001', sell_price_1335=12.5), dict(program='stock', id=2, buy_date='2026-11-03', sell_date='2026-11-04', stock_code='000001', sell_price_1335=None), dict(program='stock', id=3, buy_date='2026-11-04', sell_date='2026-11-05', stock_code='000001', sell_price_1335=14)]
    path = directory / 'stock.json'
    path.write_text(json.dumps(rows))
    original = dict(program='stock', id=1, date='2026-11-02', stock_code='000001', sell_price_1315=99, d2_sell_date_1315='2026-11-05')
    out = module.attach_1335([original], 'stock', base)[0]
    assert out['sell_price_1335'] == 12.5 and out['d2_sell_price_1335'] == 14
    assert 'sell_price_1335' not in original
    missing = dict(id=2, date='2026-11-03', stock_code='000001', sell_price_1315=99, sell_price_1335=98)
    assert module.attach_1335([missing], 'stock', base)[0]['sell_price_1335'] is None
    wrong_identity = dict(original, date='2026-10-01')
    assert module.attach_1335([wrong_identity], 'stock', base)[0]['sell_price_1335'] is None
    assert module.attach_1335([original], 'up', base)[0]['sell_price_1335'] is None
    no_d2_date = dict(original)
    del no_d2_date['d2_sell_date_1315']
    assert module.attach_1335([no_d2_date], 'stock', base)[0]['d2_sell_price_1335'] is None
    # Updated files are visible without restarting the API or retaining stale cache.
    rows[0]['sell_price_1335'] = 13
    path.write_text(json.dumps(rows))
    assert module.attach_1335([original], 'stock', base)[0]['sell_price_1335'] == 13
    module.save_manual_1335(base, 'stock', original, {'sell_price_1335': 15})
    assert module.attach_1335([original], 'stock', base)[0]['sell_price_1335'] == 15
    assert original['sell_price_1315'] == 99
print('13:35 overlay passed: compound identity, authoritative null, independent D2 execution date, no legacy fallback, mtime refresh, isolated manual edit persistence.')
