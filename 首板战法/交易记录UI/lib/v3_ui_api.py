"""Small V3 UI adapters; no selection, price generation, or ledger mutation."""
from flask import request


def register_v3_ui(app, namespace):
    from pathlib import Path
    import sys
    shared = str(Path(namespace['BASE_DIR']).parent / '公共/lib')
    if shared not in sys.path:
        sys.path.insert(0, shared)
    # Preserve board/path identity when using the existing create/update API.
    for key in ('board_rank', 'path', 'path_rank'):
        if key not in namespace['META_FIELDS']:
            namespace['META_FIELDS'].append(key)
        if key not in namespace['ALL_EDITABLE']:
            namespace['ALL_EDITABLE'].append(key)

    for key in ('sell_price_1335', 'd2_sell_price_1335'):
        if key not in namespace['PRICE_FIELDS']:
            namespace['PRICE_FIELDS'].append(key)
        if key not in namespace['ALL_EDITABLE']:
            namespace['ALL_EDITABLE'].append(key)

    @app.after_request
    def v3_attach_1335(response):
        if response.status_code >= 400 or not request.path.startswith('/api/trades') or not response.is_json:
            return response
        payload = response.get_json()
        if not payload or not payload.get('success'):
            return response
        from v3_sell_1335 import attach_1335, save_manual_1335
        import json
        data = payload.get('data')
        program = request.args.get('program') or namespace['DEFAULT_PROGRAM']
        if request.method == 'GET' and request.path == '/api/trades' and isinstance(data, list):
            payload['data'] = attach_1335(data, program, namespace['BASE_DIR'])
        elif request.method in ('POST', 'PUT') and isinstance(data, dict) and data.get('id'):
            submitted = request.get_json(silent=True) or {}
            program = data.get('program') or submitted.get('program') or program
            save_manual_1335(namespace['BASE_DIR'], program, data, submitted)
            payload['data'] = attach_1335([data], program, namespace['BASE_DIR'])[0]
        else:
            return response
        response.set_data(json.dumps(payload, ensure_ascii=False, default=str))
        return response

    @app.get('/api/v3/next-trading-day')
    def v3_next_trading_day():
        day = request.args.get('date', '')
        if not namespace['DATE_RE'].fullmatch(day):
            return namespace['_err']('日期格式错误')
        # Require an exchange calendar. Do not substitute weekday arithmetic.
        calendar = namespace['ps'].get_trade_calendar()
        if calendar is None:
            return namespace['_ok']({'date': day, 'next_date': None})
        future = calendar[calendar['trade_date'].dt.strftime('%Y-%m-%d') > day]
        next_day = None if future.empty else future.iloc[0]['trade_date'].strftime('%Y-%m-%d')
        return namespace['_ok']({'date': day, 'next_date': next_day})

    @app.get('/api/v3/pool')
    def v3_pool():
        from v3_pool import pool_view
        day = request.args.get('date', '')
        if day and not namespace['DATE_RE'].fullmatch(day):
            return namespace['_err']('日期格式错误')
        return namespace['_ok'](pool_view(namespace, day))

    @app.get('/api/v3/ledger')
    def v3_ledger():
        from v3_ledger_service import ledger_for
        try:
            rank = int(request.args.get('rank', '1'))
            result = ledger_for(namespace, request.args.get('model', 'original'), rank, request.args.get('mode', 'D1'), force=request.args.get('refresh')=='1')
        except ValueError as exc:
            return namespace['_err'](str(exc))
        return namespace['_ok'](result)

    @app.get('/api/v3/ledger-summary')
    def v3_ledger_summary():
        from v3_ledger_service import summary_for
        try:
            rank = int(request.args.get('rank','1'))
            result = summary_for(namespace, request.args.get('model','original'), rank, request.args.get('mode','D1'), force=request.args.get('refresh')=='1')
        except ValueError as exc:
            return namespace['_err'](str(exc))
        return namespace['_ok'](result)
