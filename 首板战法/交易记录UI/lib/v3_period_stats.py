"""One period projection for the API, local dashboard, and encrypted Pages data."""
from v3_ledger import capital_period, dec, as_float, ZERO


def monthly_stats(replay):
    current=replay['as_of'][:7]
    events=[e for e in replay['events'] if e['type']=='sell' and e['period']==capital_period(e['date'])[0]]

    def asset(month,before=False):
        key,base=capital_period(month+'-01')
        initial=dict(equity=as_float(base),unrealized_profit=0.0,cash=as_float(base),
                     cash_available=as_float(base),cash_locked=0.0,position_value=0.0,valuation_marks=[])
        if before and month in ('2026-01','2026-11'): return initial
        selected=[s for s in replay['snapshots'] if s['period']==key and
                  (s['date']<month+'-01' if before else s['date'][:7]<=month)]
        return selected[-1] if selected else initial

    result={}
    for year in range(2026,int(current[:4])+1):
        for m in range(1,13):
            month=f'{year}-{m:02d}'
            if month>current: break
            chosen=[e for e in events if e['date'].startswith(month)]
            def total(slot,field):
                values=[dec(e[field]) for e in chosen if (not slot or e['slot']==slot) and e.get(field) is not None]
                return as_float(sum(values,ZERO)) if values else None
            start,end=asset(month,True),asset(month)
            u0,u1=dec(start.get('unrealized_profit')),dec(end.get('unrealized_profit'))
            floating=None if u0 is None or u1 is None else u1-u0
            realized=sum((dec(e['profit']) for e in chosen),ZERO)
            pnl=None if floating is None else realized+floating
            opening,ending=dec(start['equity']),dec(end['equity'])
            ra,rp=total('am','whole_pnl'),total('pm','whole_pnl')
            result[month]=dict(ra=ra,rp=rp,morningBatchPct=ra,afternoonBatchPct=rp,
                amHalf=total('am','batch_change'),pmHalf=total('pm','batch_change'),
                aProfit=total('am','profit'),pProfit=total('pm','profit'),otherProfit=total('other','profit'),
                batchTotal=total(None,'batch_change'),profit=as_float(realized),
                realized_profit=as_float(realized),unrealized_open=as_float(u0),unrealized_close=as_float(u1),
                floatingChange=as_float(floating),assetPnl=as_float(pnl),
                accountReturnPct=as_float(pnl/opening*100) if pnl is not None and opening else None,
                opening=as_float(opening),ending=as_float(ending),openingMarks=start.get('valuation_marks',[]),
                endingMarks=end.get('valuation_marks',[]),cash=end.get('cash'),cash_available=end.get('cash_available'),
                cash_locked=end.get('cash_locked'),position_value=end.get('position_value'),count=len(chosen),
                date_basis='settlement_date',completeness='complete' if ending is not None else 'missing_valuation')
    return result
