"""Immutable offline branch-A sizing overlay and rule-only counterfactual join."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
from types import MappingProxyType

from intraday.setup2_review import (MODEL, MULTIPLIERS, QUESTION_SET_VERSION, STATE_VERSION,
                                    LabelConfig, label_a)


def _bar(value):
    at = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(at, datetime) or at.utcoffset() is None:
        raise ValueError('review bar must be an aware datetime')
    return at.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReviewOverlay:
    labels: tuple[tuple[str, str, str], ...]
    answers_sha256: str
    config: LabelConfig = LabelConfig()
    model: str = MODEL
    _index: object = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if not re.fullmatch('[a-f0-9]{64}', self.answers_sha256) or self.model != MODEL:
            raise ValueError('invalid review answers checksum or model')
        labels = tuple(sorted((rule, _bar(at), label) for rule, at, label in self.labels))
        index = {}
        for rule, at, label in labels:
            if not rule or label not in MULTIPLIERS or (rule, at) in index:
                raise ValueError('duplicate or invalid review label')
            index[rule, at] = label
        object.__setattr__(self, 'labels', labels)
        object.__setattr__(self, '_index', MappingProxyType(index))

    def label_for(self, rule_id, bar_close):
        return self._index.get((rule_id, _bar(bar_close)), 'NO_VIEW')

    def size_for(self, rule_id, bar_close):
        return MULTIPLIERS[self.label_for(rule_id, bar_close)]

    def payload(self):
        return dict(model=self.model, state_version=STATE_VERSION, question_set_version=QUESTION_SET_VERSION,
                    label_config=asdict(self.config), multipliers=MULTIPLIERS,
                    answers_sha256=self.answers_sha256, labels=self.labels)

    @classmethod
    def from_cache(cls, signals, cache, *, config=LabelConfig()):
        """Signals are frozen (rule_id, bar_close, token_tuple); cache is read-only."""
        signals = tuple(signals)
        labels = tuple((rule, _bar(at), label_a(cache.get(tokens), config)[0]) for rule, at, tokens in signals)
        return cls(labels, cache.used_digest(tokens for _, _, tokens in signals), config)


def compare_routes(rule_only, reviewed, overlay):
    """Join *actual* rule-only trades by rule/bar, without inventing absent counterfactuals.

    A veto changes subsequent position occupancy. Some later overlay entries therefore have
    no matching rule-only trade. Those remain explicitly unavailable, rather than zero PnL.
    """
    rule_id = rule_only['inputs']['rule_id']
    if reviewed['inputs']['rule_id'] != rule_id or reviewed['config'] != rule_only['config']:
        raise ValueError('counterfactual routes must share rule and replay configuration')
    base = {_bar(t['entered_at']): t for t in rule_only['trades']}
    other = {_bar(t['entered_at']): t for t in reviewed['trades']}
    vetoes = {_bar(e['at']) for e in reviewed['events'] if e.get('reason') == 'jev_review_veto'}
    return [dict(rule_id=rule_id, bar_close=at, label_a=overlay.label_for(rule_id, at),
                 multiplier=str(overlay.size_for(rule_id, at)), reference_trade_available=at in base,
                 rule_only_pnl=base[at]['net_known_pnl'] if at in base else None,
                 overlay_pnl=other[at]['net_known_pnl'] if at in other else None,
                 counterfactual_pnl=base[at]['net_known_pnl'] if at in base else None,
                 counterfactual_method='paired_rule_only' if at in base else None,
                 counterfactual_id=rule_only['result_id'] if at in base else None)
            for at in sorted(set(base) | set(other) | vetoes)]
