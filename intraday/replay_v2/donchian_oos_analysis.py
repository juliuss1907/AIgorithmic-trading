"""Descriptive evidence only, generated AFTER automatic locked scoring."""

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import json
import math
from pathlib import Path
import random

from intraday.replay_v2.artifacts import _write, _manifest, _verified_file
from intraday.replay_v2.donchian_filter_data import decode_bundle
from intraday.replay_v2.donchian_oos_config import OOSConfig
from intraday.replay_v2.donchian_oos_evaluator import verified_receipt, markdown
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


def journal(item,name):
    directory=Path(item['report_directory'])
    _,manifest=_manifest(directory.parent,item['run_id'])
    with _verified_file(directory/(name+'.jsonl'),manifest['files'][name+'.jsonl']) as stream:
        return [json.loads(line) for line in stream]


def trade_stats(trades):
    values=[Decimal(str(t['net_pnl'])) for t in trades]
    return dict(closed_trades=len(values),net_pnl=float(sum(values,Decimal(0))),
        wins=sum(v>0 for v in values),losses=sum(v<0 for v in values),
        win_rate_pct=100*sum(v>0 for v in values)/len(values) if values else None,
        mean_net_pnl=float(sum(values,Decimal(0))/len(values)) if values else None)


def annual_equity(curve,initial):
    last={}
    for point in curve:
        last[point['at'][:4]]=Decimal(point['equity_known'])
    previous=Decimal(str(initial)); result={}
    for year,value in sorted(last.items()):
        result[year]=dict(start_equity_usdt=float(previous),end_equity_usdt=float(value),
                          marked_change_usdt=float(value-previous),return_pct=float((value/previous-1)*100))
        previous=value
    return result


def bootstrap(returns, *, draws=5000,seed=20261005):
    """Circular moving blocks of synchronized portfolio daily returns, no coin resampling."""
    if len(returns)<14:
        return {'not_evaluable':'fewer than 14 full UTC days'}
    result={}; n=len(returns)
    for block in (7,14):
        rng=random.Random(seed+block); sampled=[]
        for _ in range(draws):
            values=[]
            while len(values)<n:
                begin=rng.randrange(n)
                values.extend(returns[(begin+i)%n] for i in range(min(block,n-len(values))))
            sampled.append((math.prod(1+v for v in values)-1)*100)
        sampled.sort()
        def percentile(p):
            index=(draws-1)*p; lo=int(index); hi=min(lo+1,draws-1)
            return sampled[lo]+(sampled[hi]-sampled[lo])*(index-lo)
        result[str(block)]=dict(block_days=block,draws=draws,seed=seed+block,samples=n,
            return_pct_95_interval=[percentile(.025),percentile(.975)],
            interpretation='descriptive conditional-history interval, not independent proof of edge')
    return result


def daily_returns(curve,config):
    last={}
    for row in curve:
        at=datetime.fromisoformat(row['at'])
        if at==config.end:
            at-=timedelta(milliseconds=1)  # Terminal costs belong to the ending day.
        last[at.date().isoformat()]=float(row['equity_known'])
    previous=float(config.capital); returns=[]
    for day,value in sorted(last.items()):
        opening=datetime.fromisoformat(day).replace(tzinfo=config.start.tzinfo)
        if opening<config.start or opening+timedelta(days=1)>config.end:
            continue  # Complete UTC days only; engine still includes partial final day.
        returns.append(value/previous-1); previous=value
    return returns


def buy_hold(config,data):
    fee,slip=Decimal('.001'),Decimal('.0005')
    positions={}; costs=Decimal(0)
    for symbol,weight in config.weights.items():
        first=next(row for row in data[symbol]['spot15m'] if row.opened_at==config.start)
        notional=config.capital*weight/(1+fee+slip)
        positions[symbol]=notional/first.open; costs+=notional*(fee+slip)
    histories={s:{row.available_at-timedelta(milliseconds=1):row.close for row in tags['spot15m'] if row.opened_at>=config.start}
               for s,tags in data.items()}
    times=sorted(set().union(*(set(rows) for rows in histories.values())))
    marks={s:next(row.open for row in tags['spot15m'] if row.opened_at==config.start) for s,tags in data.items()}
    peak=config.capital; dd=Decimal(0)
    for at in times:
        marks.update({s:rows[at] for s,rows in histories.items() if at in rows})
        value=sum((quantity*marks[symbol] for symbol,quantity in positions.items()),Decimal(0))
        peak=max(peak,value); dd=max(dd,1-value/peak)
    gross=sum((quantity*marks[symbol] for symbol,quantity in positions.items()),Decimal(0))
    costs+=gross*(fee+slip); final=gross*(1-fee-slip)
    dd=max(dd,1-final/peak)
    return dict(initial_usdt=float(config.capital),final_usdt=float(final),net_pnl=float(final-config.capital),
        return_pct=float((final/config.capital-1)*100),max_close_sampled_drawdown_pct=float(dd*100),
        fees_and_slippage_usdt=float(costs),
        methodology='100% Spot BTC40/ETH30/SOL30 first M15 open; no rebalance; last M15 close sale; fee10/slip5bps both fills',
        valuation='Observed source closes only; last actual price retained where another coin has no observation')


def describe(comparison,output_root, *, reference=None):
    receipt=verified_receipt(comparison)
    # Scoring must already exist and bind the exact comparison contents.
    from intraday.replay_v2.donchian_oos_evaluator import evaluate,load_criteria
    score=evaluate(receipt,load_criteria())
    if score!=receipt['evaluation'] or score['verdict']=='Invalid':
        raise ValueError('evaluate first; invalid or changed verdict evidence')
    item=next(row for row in receipt['results'] if row['variant']=='A4-Donchian30-10')
    config=OOSConfig.model_validate({k:v for k,v in item['config'].items() if k in OOSConfig.model_fields})
    trades,curve,events=(journal(item,s) for s in ('trades','equity_curve','events'))
    raw=read_inputs(receipt['inputs_path']); data,_=decode_bundle(config,raw)
    by_year=defaultdict(list); by_exit=defaultdict(list); by_coin=defaultdict(list)
    for trade in trades:
        by_year[trade['closed_at'][:4]].append(trade)
        by_exit[trade['exit_reason']].append(trade)
        by_coin[trade['market']+':'+trade['symbol']].append(trade)
    event_months={}
    for month,label in (('2022-05','LUNA'),('2022-11','FTX'),('2023-03','USDC depeg')):
        month_curve=[point for point in curve if point['at'].startswith(month)]
        event_months[month]=dict(label=label,descriptive_only=True,
            closing_trade_stats=trade_stats([t for t in trades if t['closed_at'].startswith(month)]),
            daily_halts=sum(e['kind']=='halt' and e['reason']=='daily_loss_limit' and e['at'].startswith(month) for e in events),
            first_observed_equity=float(month_curve[0]['equity_known']) if month_curve else None,
            last_observed_equity=float(month_curve[-1]['equity_known']) if month_curve else None)
    result=dict(verdict=score,comparison_sha256=file_hash(comparison),primary_summary=item['summary'],
        data_assumptions={k:item['summary'][k] for k in ('spot_availability','mark_availability',
            'funding_price_reference','native_boundary_disclosure') if k in item['summary']},
        year_marked_equity=annual_equity(curve,config.capital),
        year_closed_trades={k:trade_stats(v) for k,v in by_year.items()},
        coin_market={k:trade_stats(v) for k,v in by_coin.items()},
        exit_reasons={k:trade_stats(v) for k,v in by_exit.items()},event_months=event_months,
        buy_and_hold=buy_hold(config,data),cash=dict(final_usdt=1000,return_pct=0,
                                                  historical_stablecoin_yield_not_assumed=True),
        bootstrap=bootstrap(daily_returns(curve,config)),
        research_only=True,activation_allowed=False,official_gate_eligible=False)
    if reference:
        old=verified_receipt(reference)
        old_item=next(row for row in old['results'] if row['variant']==item['variant'])
        # Separate accounts/windows. Never pool stress runs or invent continuous DD/CAGR.
        if datetime.fromisoformat(old['window']['start'])<config.end:
            raise ValueError('pooled reference overlaps holdout')
        result['pooled_trade_stats']=trade_stats(trades+journal(old_item,'trades'))
        result['pooled_trade_stats']['limitations']='two independent 1000USDT accounts; no combined equity/DD/CAGR claim; base primary only'
        result['reference_comparison_sha256']=file_hash(reference)
    root=Path(output_root).expanduser().resolve(); root.mkdir(parents=True,mode=0o700,exist_ok=False)
    _write(root/'analysis.json',encoded(result))
    lines=[markdown(score),'## Confirmation cases','',
           '| Case | Final USDT | Net USDT | Return % | DD % | Trades |',
           '|---|---:|---:|---:|---:|---:|']
    for row in receipt['results']:
        s=row['summary']
        lines.append(f"| {row['variant']} | {s['final_equity_known']:.4f} | {s['net_pnl']:.4f} | {s['net_return_pct']:.4f} | {s['max_drawdown_known_pct']:.4f} | {s['closed_trades']} |")
    lines+=['','## Primary coin and market contributions','',
            '| Market/coin | Trades | Net USDT |','|---|---:|---:|']
    for name,stats in sorted(result['coin_market'].items()):
        lines.append(f"| {name} | {stats['closed_trades']} | {stats['net_pnl']:.4f} |")
    lines+=['','## Descriptive comparisons','',
            'Buy-and-hold: '+encoded(result['buy_and_hold']),
            'Cash: 1000USDT, 0% assumed yield. Stablecoin yield not fabricated.',
            'Annual marked equity: '+encoded(result['year_marked_equity']),
            'Event months: '+encoded(result['event_months']),
            'Block bootstrap: '+encoded(result['bootstrap']),
            'Pooled base trades: '+encoded(result.get('pooled_trade_stats')),'',
            'No tuning, A3 fallback, activation, official gate update or VPS deployment.',
            'Native M15 OHLC cannot prove tick fills/liquidation. Portfolio DD is sampled, observe-only.',
            'Retrospective holdout and descriptive intervals do not prove future profitability.','']
    if result['data_assumptions']:
        lines+=['## Approved source-data assumptions','',
            'Original prices/rates/timestamps preserved. No synthetic bars. Source timestamps are UTC.',
            'Spot unavailability delays fills; stale Spot/mark valuations give known-sample DD only.',
            'Missing funding settlement quotes use causal native mark-open references, not API-confirmed settlement prices.',
            'Only the exact approved H4/M15 price-view differences are admitted; no generic tolerance.',
            encoded(result['data_assumptions']),'']
    _write(root/'report.md','\n'.join(lines))
    _write(root/'manifest.json',encoded(dict(files={name:file_hash(root/name) for name in ('analysis.json','report.md')},
                                            comparison_sha256=file_hash(comparison))))
    return result


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description='Describe verified research after locked verdict; no tuning')
    p.add_argument('--comparison',required=True); p.add_argument('--output-root',required=True)
    p.add_argument('--reference'); args=p.parse_args(argv)
    describe(args.comparison,args.output_root,reference=args.reference)


if __name__=='__main__':
    main()
