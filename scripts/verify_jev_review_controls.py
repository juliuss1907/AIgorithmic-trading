"""Historical mechanism control with explicitly synthetic opinions, never a Jev evaluation."""

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intraday.jev_review import database_routes, paired_replay, route_identity, scan_route, valid_plan
from intraday.jev_review_cache import AnswerCache, canonical, digest
from intraday.replay_v2.artifacts import publish_report
from intraday.setup2_review import questions


def bound_signals(plan, routes):
    signals, identities = [], []
    for config, data in routes:
        found = scan_route(config, data)
        signals.extend(found)
        identities.append(route_identity(config, data, found))
    if canonical(identities) != canonical(plan['route_inputs']):
        raise ValueError('control route identities or dataset checksums differ from the frozen plan')
    if canonical([s.payload() for s in signals]) != canonical(plan['signals']):
        raise ValueError('control signals differ from the frozen plan')
    if digest(questions()) != plan['request_template_sha256']:
        raise ValueError('control question template differs from the frozen plan')
    return tuple(signals)


def run(plan_path, database, funding_dir, root):
    plan = json.loads(plan_path.read_text())
    if not valid_plan(plan):
        raise ValueError('control plan checksum mismatch')
    if root.exists():
        raise FileExistsError('control output root already exists')
    end = datetime.fromisoformat(plan['route_inputs'][0]['config']['end'])
    routes = list(database_routes(database, end, funding_dir=funding_dir))
    print('Verifying complete route, dataset and signal bindings', flush=True)
    signals = bound_signals(plan, routes)
    root.mkdir(parents=True, exist_ok=False)
    cache_path = root/'SYNTHETIC-answers.sqlite3'
    with AnswerCache(cache_path, writable=True) as cache:
        states = {s['key']: tuple(s['state']) for s in plan['signals']}
        for key, state in sorted(states.items()):
            mode = int(key[:2], 16) % 4
            if mode == 3:  # Missing opinion control.
                continue
            action = ({'enter_full': .8, 'reduce': .1, 'skip': .1} if mode == 0 else
                      {'enter_full': .3, 'reduce': .6, 'skip': .1} if mode == 1 else
                      {'enter_full': .05, 'reduce': .05, 'skip': .9})
            payload = dict(model='typesafe/jev-1.13', synthetic_fixture=True,
                fixture_method='sha256-first-byte-mod-four; independent of prices/outcomes',
                answers=dict(action=dict(choice=max(action, key=action.get), probabilities=action),
                             exhaustion=dict(noul=.1), disorderly=dict(noul=.1)))
            cache.put(state, payload)
    results = []
    with AnswerCache(cache_path) as cache:
        for config, data in routes:
            print('Synthetic historical control:', config.symbol, config.market, flush=True)
            pair = paired_replay(config, data, [s for s in signals if s.rule_id == config.rule_id], cache)
            rejected = [r for r in pair['counterfactual'] if r['label_a'] in {'VETO', 'REDUCE'}]
            assert all(r['counterfactual_pnl'] is not None for r in rejected)
            saved = {name: publish_report(root/'reports', pair[name]) for name in ('rule_only', 'reviewed')}
            results.append(dict(symbol=config.symbol, market=config.market, reports=saved,
                result_ids={name: pair[name]['result_id'] for name in saved},
                closed_trades={name: pair[name]['summary']['closed_trades'] for name in saved},
                blocked_vetoes=pair['reviewed']['summary']['blocked_entries'].get('jev_review_veto', 0),
                label_counts=dict(Counter(label for _, _, label in pair['overlay']['labels'])),
                counterfactual_methods=dict(Counter(r['counterfactual_method'] for r in rejected)),
                counterfactual=pair['counterfactual'], overlay=pair['overlay']))
    out = dict(synthetic=True, strategy_evaluation=False, live_jev_calls=0, plan_hash=plan['plan_hash'],
               cache=str(cache_path), results=results)
    with (root/'controls.json').open('x') as stream:
        stream.write(canonical(out)+'\n')
    print('Synthetic mechanism controls complete:', root/'controls.json', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--funding-dir', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    run(args.plan, args.database, args.funding_dir, args.output_root)
