from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from lab.data import sessions


def config(**kwargs):
    from lab.equity_research import EquityConfig
    return EquityConfig(start=datetime(2024, 10, 2, tzinfo=timezone.utc),
                        end=datetime(2024, 10, 10, tzinfo=timezone.utc), **kwargs)


def frames(cfg):
    idx = sessions(str(cfg.data_start), str(cfg.end.date()-timedelta(days=1)))
    frame = pd.DataFrame({'open': 100., 'high': 101., 'low': 99., 'close': 100.,
                          'adj_close': 100., 'volume': 1000.}, index=idx)
    prior = idx[idx < pd.Timestamp(cfg.start.date())][-1]
    frame.loc[prior, ['high', 'close', 'adj_close']] = [106., 105., 105.]
    return {s: frame.copy() for s in cfg.weights}


def test_equity_config_is_usd_long_only_and_rejects_crypto():
    cfg = config()
    assert set(cfg.weights) == {'NVDA', 'META', 'MSFT', 'GOOGL'}
    assert sum(cfg.weights.values()) == 1 and cfg.entry_cap == 1
    with pytest.raises(ValueError):
        config(weights={'BTCUSDT': 1})
    with pytest.raises(ValueError):
        config(stop='unknown')


@pytest.mark.parametrize('stop', ['fixed-5pct', 'atr14-3x'])
def test_equity_causal_next_session_entries_and_cash_reconcile(stop):
    from lab.equity_research import simulate
    cfg = config(stop=stop)
    report = simulate(cfg, frames(cfg))
    entries = [e for e in report['events'] if e['kind'] == 'entry']
    assert len(entries) == 4
    assert all(e['at'] == '2024-10-02T13:30:00+00:00' for e in entries)
    assert all(Decimal(e['price']) == 100 and 0 < Decimal(e['notional']) <= 250 for e in entries)
    assert all(datetime.fromisoformat(e['signal_available_at']) < datetime.fromisoformat(e['at']) for e in entries)
    assert all(Decimal(p['cash']) >= 0 for p in report['equity_curve'])
    assert all(Decimal(t['quantity']) > 0 for t in report['trades'])
    assert report['summary']['final_equity'] == pytest.approx(1000+sum(float(t['net_pnl']) for t in report['trades']))
    assert not report['activation_allowed'] and report['currency'] == 'USD'


@pytest.mark.parametrize('defect', ['missing', 'duplicate', 'nan', 'bad_ohlc'])
def test_stock_research_rejects_bad_sessions_and_prices(defect):
    from lab.equity_research import simulate
    cfg = config()
    inputs = frames(cfg)
    f = inputs['NVDA']
    if defect == 'missing':
        inputs['NVDA'] = f.drop(f.index[-2])
    elif defect == 'duplicate':
        inputs['NVDA'] = pd.concat([f, f.iloc[-1:]])
    elif defect == 'nan':
        f.loc[f.index[-1], 'close'] = float('nan')
    else:
        f.loc[f.index[-1], 'low'] = 150
    with pytest.raises(ValueError):
        simulate(cfg, inputs)


def test_equity_stop_gap_fills_at_open_and_touch_at_stop():
    from lab.equity_research import simulate
    cfg = config(stop='fixed-5pct', daily_loss='.5', max_drawdown='.5')
    for gap in (False, True):
        inputs = frames(cfg)
        for f in inputs.values():
            if gap:
                f.loc['2024-10-03', ['open', 'low', 'close', 'adj_close']] = [90, 89, 90, 90]
            else:
                f.loc['2024-10-02', 'low'] = 94
        r = simulate(cfg, inputs)
        assert len(r['trades']) == 4
        assert all(Decimal(t['exit_price']) == (90 if gap else 95) for t in r['trades'])
        assert all(t['exit_reason'] == ('stop_gap' if gap else 'stop_touch_detected_at_close') for t in r['trades'])


def test_future_stock_bars_do_not_change_initial_entries():
    from lab.equity_research import simulate
    cfg = config()
    a = frames(cfg)
    b = {s: f.copy() for s, f in a.items()}
    for f in b.values():
        f.loc['2024-10-04':, ['open', 'high', 'low', 'close', 'adj_close']] *= 2
    first = lambda r: [e for e in r['events'] if e['kind'] == 'entry' and e['at'].startswith('2024-10-02')]
    assert first(simulate(cfg, a)) == first(simulate(cfg, b))


def test_equity_close_risk_flattens_next_session_open_not_past_close():
    from lab.equity_research import simulate
    cfg = config(stop='fixed-5pct', max_drawdown='.01')
    inputs = frames(cfg)
    for f in inputs.values():
        f.loc['2024-10-02', ['low', 'close', 'adj_close']] = [97, 98, 98]
        f.loc['2024-10-03', ['open', 'low', 'close', 'adj_close']] = [97, 96, 97, 97]
    r = simulate(cfg, inputs)
    assert r['summary']['terminal_halt_at'] == '2024-10-02T20:00:00+00:00'
    assert all(t['closed_at'] == '2024-10-03T13:30:00+00:00' and Decimal(t['exit_price']) == 97 for t in r['trades'])
    assert len([e for e in r['events'] if e['kind'] == 'entry']) == 4


def test_stock_snapshot_offline_study_reproduces_and_never_overwrites(tmp_path):
    from lab.equity_study import run_study, freeze_inputs, decode_inputs
    cfg = config()
    raw = freeze_inputs(cfg, frames(cfg))
    assert set(decode_inputs(cfg, raw)) == set(cfg.weights)
    raw['series']['NVDA']['rows'][-1][4] += 1
    with pytest.raises(ValueError, match='checksum'):
        decode_inputs(cfg, raw)
    receipt = run_study(tmp_path/'reports', config=cfg, raw_inputs=freeze_inputs(cfg, frames(cfg)))
    assert len(receipt['results']) == 5 and receipt['source_unchanged']
    assert all(r['deterministic_rerun_verified'] for r in receipt['results'])
    assert len({r['result_id'] for r in receipt['results']}) == 5
    with pytest.raises(FileExistsError):
        run_study(tmp_path/'reports', config=cfg, raw_inputs=freeze_inputs(cfg, frames(cfg)))


def test_equity_daily_pause_resumes_next_session_flat_without_resetting_peak():
    from lab.equity_research import simulate
    cfg = config(daily_loss='.02')
    inputs = frames(cfg)
    for f in inputs.values():
        f.loc['2024-10-02':, ['open', 'high', 'low', 'close', 'adj_close']] = [97, 100, 96, 97, 97]
        f.loc['2024-10-02', 'open'] = 100
    report = simulate(cfg, inputs)
    halts = [e for e in report['events'] if e['kind'] == 'halt']
    resumes = [e for e in report['events'] if e['kind'] == 'resume']
    assert halts[0]['reason'] == 'daily_loss_limit' and halts[0]['at'].startswith('2024-10-02')
    assert resumes[0]['at'] == '2024-10-03T13:30:00+00:00'
    assert all(t['closed_at'] == resumes[0]['at'] for t in report['trades'])
    assert report['summary']['max_drawdown_pct'] > 2


def test_stock_calendar_handles_holiday_early_close_and_dst():
    from lab.equity_research import EquityConfig, simulate
    cfg = EquityConfig(start=datetime(2024, 11, 27, tzinfo=timezone.utc),
                       end=datetime(2024, 12, 3, tzinfo=timezone.utc))
    r = simulate(cfg, frames(cfg))
    assert r['summary']['sessions'] == 3  # Thanksgiving and weekend excluded.
    assert any(p['at'] == '2024-11-29T18:00:00+00:00' for p in r['equity_curve'])
    assert any(p['at'] == '2024-11-27T14:30:00+00:00' for p in r['equity_curve'])
    assert not any(p['at'].startswith('2024-11-28') for p in r['equity_curve'])


def test_stock_cli_requires_explicit_collection_or_offline_inputs(monkeypatch):
    from lab import equity_study
    calls = []
    monkeypatch.setattr(equity_study, 'run_study', lambda *a, **k: calls.append(k) or
                        {'results': [None]*5, 'source_unchanged': True})
    equity_study.main(['--report-root', 'x', '--inputs', 'frozen'])
    assert calls[0]['inputs_path'] == 'frozen' and not calls[0]['collect_data']
    for options in ([], ['--collect', '--inputs', 'x']):
        with pytest.raises(SystemExit):
            equity_study.main(['--report-root', 'x', *options])


@pytest.mark.parametrize('defect', ['missing_series', 'wrong_symbols', 'columns', 'malformed_series'])
def test_equity_decoder_rejects_malformed_bundles_even_with_valid_checksum(defect):
    from lab.equity_study import decode_inputs, freeze_inputs
    from intraday.replay_v2.metrics import fingerprint
    cfg = config()
    raw = freeze_inputs(cfg, frames(cfg))
    if defect == 'missing_series':
        raw.pop('series')
    elif defect == 'wrong_symbols':
        raw['series'].pop('NVDA')
    elif defect == 'columns':
        raw['series']['NVDA']['columns'] = ['unexpected']
    else:
        raw['series']['NVDA'] = None
    raw['checksum'] = fingerprint({k:v for k,v in raw.items() if k != 'checksum'})
    with pytest.raises(ValueError):
        decode_inputs(cfg, raw)
