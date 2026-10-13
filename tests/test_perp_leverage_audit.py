from decimal import Decimal as D

import pytest

from intraday.replay_v2.artifacts import publish_report
from intraday.replay_v2.donchian_filter_engine import simulate
from scripts.verify_perp_leverage_research import audit_case
from test_donchian_perp_leverage import prepared_account


@pytest.mark.parametrize('corrupt', [None, 'net_pnl', 'exchange_fee', 'result_id'])
def test_decimal_audit_accepts_valid_journals_and_rejects_accounting_errors(tmp_path, corrupt):
    cfg, prepared = prepared_account(1)
    report = simulate(cfg, prepared)
    if corrupt and corrupt != 'result_id':
        report['trades'][0][corrupt] += D(1)
    item = dict(variant='PERP5X', **publish_report(tmp_path/'reports', report))
    if corrupt == 'result_id':
        item['result_id'] = '0'*64
    if corrupt:
        with pytest.raises(ValueError, match='reconcile|identity'):
            audit_case(item)
    else:
        assert audit_case(item)['decimal_accounting_verified']
