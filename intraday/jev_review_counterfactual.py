"""Supplement only the skipped/reduced signals absent from the paired baseline cohort."""

from dataclasses import replace
from datetime import datetime
from decimal import localcontext

from intraday import setup2
from intraday.jev_review_cache import digest
from intraday.portfolio_coordinator import ParentPortfolioPolicy
from intraday.replay_v2.engine import SETUP2_SLOT, _setup2, setup2_splits
from intraday.replay_v2.ledger import Ledger


def supplement_counterfactuals(config, data, rows, dataset_checksum):
    """An isolated entry starts flat with the original route capital at that signal.

    Its stop, exits, costs, funding, loss limits and fill path use the same engine.
    It is not a second portfolio history, and must not be summed as portfolio PnL.
    Overlapping isolated trades are correlated; preserve the method per row.
    """
    missing = [r for r in rows if r['label_a'] in {'VETO', 'REDUCE'} and not r['reference_trade_available']]
    if not missing:
        return rows
    data = replace(data, jev_review=None)
    side = 1 if config.market == 'spot' else -1
    with localcontext() as context:
        context.prec = 28
        prepared = setup2.series_observations(list(data.candles), list(data.profile_candles), side=side,
            anchor=data.indicator_anchor, profile=data.rule.parameters.entry_profile,
            warmup_bars=data.warmup_bars or setup2.WARMUP_BARS, own_from=data.price_proxy_until)
        for row in missing:
            at = datetime.fromisoformat(row['bar_close'])
            isolated = config.model_copy(update={'start': at})
            book = Ledger(isolated, policy=ParentPortfolioPolicy.from_split(*setup2_splits(data.rule), 1),
                          slot_weight=SETUP2_SLOT)
            _setup2(isolated, data, book, set(), side=side, prepared=prepared, first_trade_only=True)
            if not book.trades or book.trades[0]['entered_at'] != at.isoformat():
                raise ValueError('counterfactual entry is not executable under the original rule and filters')
            trade = book.trades[0]
            evidence = dict(method='jev-review-isolated-first-trade-v1', dataset_checksum=dataset_checksum,
                            config=isolated.model_dump(mode='json'), trade=trade)
            row.update(counterfactual_method='isolated_flat_start', counterfactual_pnl=trade['net_known_pnl'],
                       counterfactual_id=digest(evidence), isolated_trade=trade,
                       isolated_initial_capital=str(isolated.capital))
    return rows
