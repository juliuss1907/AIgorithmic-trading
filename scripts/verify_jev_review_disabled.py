"""Replay the same ten copied G4 routes under a chosen checkout and hash *all* output bytes.

Run once with the original checkout, once with this worktree; compare the routes arrays.
This script deliberately imports only APIs available at the original 9eeb207 base.
"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys


def verify(checkout, database, funding_dir, end, output):
    sys.path.insert(0, str(checkout.resolve()))
    from intraday import active_set, setup2_store
    from intraday.store import IntradayStore
    from intraday.replay_v2.contracts import ReplayConfig, binance_gate_profile
    from intraday.replay_v2.data import load_dataset
    from intraday.replay_v2.engine import simulate
    from intraday.replay_v2.funding import FundingSnapshot
    from intraday.replay_v2.lifecycle import _setup2_inputs
    from intraday.replay_v2.metrics import encoded
    from intraday.replay_v2.portfolio_study import file_hash
    import intraday.replay_v2.engine as engine

    assert Path(engine.__file__).resolve().is_relative_to(checkout.resolve()), 'wrong checkout import'
    before = file_hash(database)
    store, routes = IntradayStore(database, read_only=True), []
    with store.read_snapshot():
        current = active_set.current(store)
        for symbol in sorted(current.plan.coins):
            for market in ('spot', 'perp'):
                rules = [store.load_scoped_rule(r['id']) for r in store.list_scoped_rules(
                    setup2_store.SCOPES[market], symbol=symbol)]
                rules = [r for r in rules if r.parameters.entry_profile == current.plan.rule_profile]
                assert len(rules) == 1
                rule = rules[0]
                start, finish, _ = _setup2_inputs(store, rule, market, end)
                funding = FundingSnapshot.model_validate_json((funding_dir/f'{symbol}-funding-public.json').read_bytes()
                    ).history if market == 'perp' else None
                config = ReplayConfig(symbol=symbol, market=market, rule_id=rule.rule_id,
                    start=start, end=finish, capital=1000, leverage=1, profile=binance_gate_profile(funding=funding))
                print('Disabled replay:', symbol, market, flush=True)
                report = simulate(config, load_dataset(database, config, reader=store))
                body = encoded(report).encode()
                routes.append(dict(symbol=symbol, market=market, result_id=report['result_id'],
                    output_sha256=hashlib.sha256(body).hexdigest(), output_bytes=len(body), summary=report['summary']))
    assert file_hash(database) == before, 'read-only source bytes changed'
    result = dict(checkout=str(checkout.resolve()), engine_source=str(Path(engine.__file__).resolve()),
                  database_sha256=before, routes=routes)
    with output.open('x') as stream:
        stream.write(json.dumps(result, indent=2)+'\n')
    print('Verified all output bytes:', output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--funding-dir', type=Path, required=True)
    parser.add_argument('--end', type=datetime.fromisoformat, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.checkout, args.database, args.funding_dir, args.end, args.output)
