import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from intraday.replay_v2 import donchian_recent as recent
from intraday.replay_v2.metrics import encoded
from test_donchian_prospective import fetchers

END = datetime(2026, 10, 13, 0, tzinfo=timezone.utc)
START = END-timedelta(hours=48)


@pytest.mark.parametrize('now,end', [('2026-10-13T04:04:59Z', '2026-10-13T00:00:00Z'),
                                  ('2026-10-13T04:05:00Z', '2026-10-13T04:00:00Z')])
def test_latest_end_uses_absolute_utc_h4_and_a_five_minute_delay(now, end):
    assert recent.latest_end(datetime.fromisoformat(now)) == datetime.fromisoformat(end)


def test_ten_cases_have_three_exact_symbol_bundles_and_no_stress_cases():
    cases = recent.recent_cases(START, END)
    assert len(cases) == 10 and len({n for n, _ in cases}) == 10
    assert {len(recent.bundle_symbols(c)) for _, c in cases} == {3, 4, 5}
    assert all(getattr(c, 'cost_multiplier', 1) == 1 for _, c in cases)
    assert {c.leverage for n, c in cases if n.startswith('PERP')} == {1, 5}


def test_collection_three_bundles_and_deterministic_reports(tmp_path):
    paths = []
    for symbols in recent.symbol_groups(START, END):
        paths.append(recent.collect(symbols, START, END, tmp_path/str(len(symbols)),
                     now=END+timedelta(hours=1), progress=lambda _: None, **fetchers()))
    raw = json.loads(paths[0].read_text())
    assert raw['window'] == {'start': START.isoformat(), 'end': END.isoformat()}
    assert len(raw['candles']['ETHUSDT']['spot4h']['raw_rows']) == 612
    assert len(raw['candles']['ETHUSDT']['mark15m']['raw_rows']) == 192
    result = recent.run(paths, tmp_path/'reports', progress=lambda _: None)
    assert result['deterministic_rerun_verified'] and not result['activation_allowed']
    assert len(result['results']) == 10 and all(r['summary']['closed_trades'] == 0 for r in result['results'])
    assert (tmp_path/'reports'/'comparison.md').exists()
    assert json.loads((tmp_path/'reports'/'comparison.json').read_text())['window'] == raw['window']
    with pytest.raises(FileExistsError):
        recent.run(paths, tmp_path/'reports', progress=lambda _: None)


def test_collection_refuses_overwrite_before_fetching(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('an existing destination must be rejected before any network call')
    with pytest.raises(FileExistsError):
        recent.collect(('ETHUSDT', 'NEARUSDT', 'SOLUSDT'), START, END, tmp_path,
                       spot_fetcher=forbidden)


@pytest.mark.parametrize('hours', [0, -4, 47])
def test_window_rejects_invalid_duration(hours):
    with pytest.raises(ValueError):
        recent.window(hours=hours, end=END)


def test_missing_candle_fails_closed(tmp_path):
    f = fetchers()
    real = f['perp_fetcher']
    def missing(*args, **kwargs):
        snap = real(*args, **kwargs)
        return snap.model_copy(update={'raw_rows': snap.raw_rows[1:]})
    f['perp_fetcher'] = missing
    with pytest.raises(ValueError):
        recent.collect(('ETHUSDT', 'NEARUSDT', 'SOLUSDT'), START, END, tmp_path/'inputs',
                       now=END+timedelta(hours=1), progress=lambda _: None, **f)


def test_report_marks_window_end_positions_separately():
    from test_donchian_perp_leverage import prepared_account
    from intraday.replay_v2.donchian_filter_engine import simulate
    cfg, p = prepared_account(1)
    report = simulate(cfg, p)
    detail = recent.case_detail(report)
    assert detail['window_end_positions'] and detail['trades'][0]['valuation_exit']
    assert detail['orders'][0]['side'] == 'long' and detail['orders'][0]['notional']
    assert D(detail['window_end_positions'][0]['unrealized_price_pnl']) == 0
    assert detail['blocked_entries'] == {} and detail['failed_filters'] == {}


def test_report_explains_breakouts_skipped_while_a_position_is_open():
    from test_donchian_perp_leverage import prepared_account, START as ACCOUNT_START
    from intraday.replay_v2.donchian_filter_engine import simulate
    cfg, prepared = prepared_account(1)
    at = ACCOUNT_START+timedelta(hours=4)
    prepared.observations['perp']['ETHUSDT'][at][(30, 10)]['long_entry'] = True
    report = simulate(cfg, prepared)
    journal_before = encoded(report['events'])
    detail = recent.case_detail(report, prepared)
    assert detail['blocked_entries']['one_position_per_coin'] == 1
    assert detail['derived_skipped_signals'][0]['at'] == at.isoformat()
    assert encoded(report['events']) == journal_before


def test_tampered_bundle_checksum_is_rejected(tmp_path):
    paths = []
    for symbols in recent.symbol_groups(START, END):
        paths.append(recent.collect(symbols, START, END, tmp_path/str(len(symbols)),
                     now=END+timedelta(hours=1), progress=lambda _: None, **fetchers()))
    raw = json.loads(paths[0].read_text())
    raw['bundle_checksum'] = '0'*64
    paths[0].write_text(encoded(raw))
    with pytest.raises(ValueError, match='checksum'):
        recent.run(paths, tmp_path/'bad', progress=lambda _: None)
