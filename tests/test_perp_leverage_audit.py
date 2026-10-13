from decimal import Decimal as D
import json
from pathlib import Path
import subprocess
import sys

import pytest

from intraday.replay_v2.artifacts import publish_report
from intraday.replay_v2.donchian_filter_engine import simulate
from intraday.replay_v2.metrics import fingerprint
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


def test_direct_script_uses_its_own_worktree_with_a_shared_virtualenv(tmp_path):
    root = Path(__file__).resolve().parents[1]
    comparison = tmp_path/'comparison.json'
    comparison.write_text(json.dumps({'results': []}))
    completed = subprocess.run([sys.executable, str(root/'scripts/verify_perp_leverage_research.py'),
        '--comparison', str(comparison), '--output-root', str(tmp_path/'audit')], cwd=root,
        capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    binding = json.loads((tmp_path/'audit/binding.json').read_text())
    assert 'intraday/replay_v2/donchian_perp_leverage.py' in binding['sources']


def legacy_pair(**extra):
    cfg = dict(universe='rotation', leverage=1)
    old = dict(config=cfg, summary=dict(net_pnl=10), evaluator_version='v')
    old['result_id'] = fingerprint(dict(version='v', config=cfg, data='data'))
    report = dict(old, config=dict(cfg, **extra), inputs=dict(dataset_checksum='data'),
                  events=[dict(kind='selection', reason='rotation_ranking', limit_scope='total')])
    report['result_id'] = fingerprint(dict(version='v', config=report['config'], data='data'))
    return old, report


def test_legacy_comparison_only_normalizes_prior_default_metadata():
    from scripts.verify_perp_leverage_research import legacy_shape
    old, report = legacy_pair(limit_scope='total', slot_divisor=None)
    compatible, defaults = legacy_shape(report, old)
    assert defaults == dict(limit_scope='total', slot_divisor=None)
    assert compatible['result_id'] == old['result_id']
    assert 'limit_scope' not in compatible['events'][0]
    assert report['events'][0]['limit_scope'] == 'total'  # Actual evidence was not rewritten.


@pytest.mark.parametrize('extra', [dict(limit_scope='sleeve'), dict(slot_divisor=4), dict(leverage=5)])
def test_legacy_comparison_rejects_actual_rule_changes(extra):
    from scripts.verify_perp_leverage_research import legacy_shape
    old, report = legacy_pair(**extra)
    with pytest.raises(ValueError):
        legacy_shape(report, old)
