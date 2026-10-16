"""Causal, anonymous Setup-2 review state and frozen branch-A policy; no I/O."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from intraday import setup2

D = Decimal
STATE_VERSION = 'setup2-review-state-v1'
QUESTION_SET_VERSION = 'setup2-review-questions-v1'
LABEL_CONFIG_VERSION = 'setup2-review-labels-v1'
MODEL = 'typesafe/jev-1.13'
# Technical cut points fixed before reading trade outcomes. No outcome-dependent fitting.
CUT_POINTS = {
    'adx': ('25', '40'), 'breakout_atr': ('.5', '1.5'), 'ema_slow_atr': ('1', '3'),
    'volume_ratio': ('1.2', '2'), 'rsi': ('30', '70'), 'move_six_atr': ('1', '3'),
    'funding': ('-.0001', '.0001'), 'atr_fraction': ('.01', '.03'), 'trigger_atr': ('1', '2'),
}
TOKEN_GROUPS = {
    'route': ('spot_long', 'perp_short', 'route_unknown'),
    'breakout': ('breakout_twenty_five', 'breakout_thirty', 'breakout_unknown'),
    'breakout_ext': tuple('breakout_ext_'+s for s in ('small', 'medium', 'large', 'unknown')),
    'adx': tuple('adx_'+s for s in ('weak', 'strong', 'extreme', 'unknown')),
    'adx_slope': tuple('adx_slope_'+s for s in ('rising', 'flat', 'falling', 'unknown')),
    'ema_slow': tuple('ema_slow_'+s for s in ('near', 'moderate', 'far', 'unknown')),
    'ema_fast': tuple('ema_fast_'+s for s in ('aligned', 'mixed', 'unknown')),
    'volume': tuple('volume_'+s for s in ('normal', 'high', 'extreme', 'unknown')),
    'rsi': tuple('rsi_'+s for s in ('oversold', 'neutral', 'overbought', 'unknown')),
    'move_six': tuple('move_six_'+s for s in ('small', 'medium', 'large', 'unknown')),
    'funding': tuple('funding_'+s for s in ('negative', 'neutral', 'positive', 'not_applicable', 'unknown')),
    'atr': tuple('atr_'+s for s in ('low', 'normal', 'high', 'unknown')),
    'trigger': tuple('trigger_'+s for s in ('normal', 'long', 'extreme', 'unknown')),
    'profile': tuple('profile_'+s for s in ('beyond', 'inside', 'unknown')),
}
VOCABULARY = frozenset(t for group in TOKEN_GROUPS.values() for t in group)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = D(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _band(prefix, value, cut_name, names):
    if value is None:
        return prefix+'_unknown'
    low, high = map(D, CUT_POINTS[cut_name])
    return prefix+'_'+(names[0] if value < low else names[1] if value <= high else names[2])


def _rsi(rows):
    """Wilder H4 RSI14, SMA seed; missing/gapped history is unknown."""
    if len(rows) < 15 or not setup2.contiguous(rows, setup2.H4):
        return None
    changes = [b.close-a.close for a, b in zip(rows, rows[1:])]
    gain = sum((max(D(0), c) for c in changes[:14]), D(0))/14
    loss = sum((max(D(0), -c) for c in changes[:14]), D(0))/14
    for change in changes[14:]:
        gain += (max(D(0), change)-gain)/14
        loss += (max(D(0), -change)-loss)/14
    return 100-100/(1+gain/loss) if loss else D(100) if gain else D(50)


def encode_state(obs, h4, funding=None):
    """Fourteen fixed tokens; only bars/settlements available by the trigger close.

    `h4` is the anchored H4 series including the closed trigger, when available.
    Features are those already computed by Setup2Observation; no book/OI/positioning.
    Funding is the most recent *settled* FundingSettlement, never a future rate.
    """
    side, f = obs.side, obs.features
    rows = [b for b in h4 if obs.bar_close is not None and b.available_at <= obs.bar_close]
    rows = rows if rows and rows[-1].available_at == obs.bar_close else []
    price, atr = _number(obs.close), _number(obs.atr)
    atr = atr if atr is not None and atr > 0 else None
    price = price if price is not None and price > 0 else None
    adx, prior = _number(f.get('adx')), _number(f.get('prior_adx'))
    slow, fast, prior_fast = (_number(f.get(k)) for k in ('ema200', 'ema50', 'prior_ema50'))
    vol, vol_ma = _number(f.get('volume')), _number(f.get('volume_ma'))
    window = {'setup2-slots-live-v1': 25, 'setup2-live-v1': 30}.get(obs.evaluator_version)
    if obs.bar_close is None:
        window = None
    ext = None
    if window and len(rows) > window and atr and price:
        channel = max(b.high for b in rows[-window-1:-1]) if side > 0 else min(
            b.low for b in rows[-window-1:-1])
        ext = max(D(0), side*(price-channel)/atr)
    slope = 'unknown' if adx is None or prior is None else (
        'rising' if adx > prior else 'falling' if adx < prior else 'flat')
    aligned = 'unknown' if None in (price, fast, prior_fast) else (
        'aligned' if side*(price-fast) > 0 and side*(fast-prior_fast) > 0 else 'mixed')
    move = abs(price-rows[-7].close)/atr if price and atr and len(rows) >= 7 and setup2.contiguous(
        rows[-7:], setup2.H4) else None
    rate = _number(funding.rate) if funding is not None and obs.bar_close is not None and (
        funding.at <= obs.bar_close) else None
    boundary = _number((obs.profile or {}).get('vah' if side > 0 else 'val'))
    tokens = [
        'spot_long' if side == 1 else 'perp_short' if side == -1 else 'route_unknown',
        'breakout_twenty_five' if window == 25 else 'breakout_thirty' if window == 30 else 'breakout_unknown',
        _band('breakout_ext', ext, 'breakout_atr', ('small', 'medium', 'large')),
        _band('adx', adx, 'adx', ('weak', 'strong', 'extreme')), 'adx_slope_'+slope,
        _band('ema_slow', abs(price-slow)/atr if None not in (price, slow, atr) else None,
              'ema_slow_atr', ('near', 'moderate', 'far')), 'ema_fast_'+aligned,
        _band('volume', vol/vol_ma if vol is not None and vol_ma and vol_ma > 0 else None,
              'volume_ratio', ('normal', 'high', 'extreme')),
        _band('rsi', _rsi(rows), 'rsi', ('oversold', 'neutral', 'overbought')),
        _band('move_six', move, 'move_six_atr', ('small', 'medium', 'large')),
        'funding_not_applicable' if side == 1 else _band('funding', rate, 'funding', ('negative', 'neutral', 'positive')),
        _band('atr', atr/price if atr and price else None, 'atr_fraction', ('low', 'normal', 'high')),
        _band('trigger', (rows[-1].high-rows[-1].low)/atr if rows and atr else None,
              'trigger_atr', ('normal', 'long', 'extreme')),
        'profile_unknown' if price is None or boundary is None else 'profile_'+(
            'beyond' if side*(price-boundary) > 0 else 'inside'),
    ]
    return tokens


def questions():
    horizon = (
        'Review a rule-qualified multi-day trend breakout on closed four-hour bars, held for days, '
        'not an intraday reversal trade. Exit on the opposite ten-bar channel or the trailing stop. '
        'The initial stop is the closer of three ATR and the profile cap: five percent for '
        'breakout_twenty_five, ten percent for breakout_thirty; it never loosens. '
        'Slow EMA means two hundred bars; fast EMA means fifty bars. RSI extremes alone do not veto '
        'a multi-day trend. Move_six is the absolute six-bar move measured in ATR; trigger is '
        'the trigger high-low range in ATR. Funding is a settled rate, not a forecast. '
        'Unknown tokens provide no evidence. Use only supplied tokens. '
    )
    return {
        'action': dict(type='choice', instructions=horizon+'Choose the entry size for this setup.', criteria={
            'enter_full': 'Trend continuation over days is credible and tokens give no material reason to cut rule size.',
            'reduce': 'Continuation remains credible but stretch, exhaustion or disorder warrants half the rule size.',
            'skip': 'Tokens strongly suggest stop-reaching reversal before continuation or conditions too poor to enter.'}),
        'exhaustion': dict(type='noul', instructions=horizon+
            'Will an adverse move large enough to hit the initial stop occur before meaningful trend continuation?',
            criteria={'true': 'Stop-reaching adverse reversal is likely before continuation.',
                      'false': 'Continuation is more likely before a stop-reaching adverse move.'}),
        'disorderly': dict(type='noul', instructions=horizon+
            'Are conditions too disorderly for this multi-day entry, based on volatility, trigger range and stretch?',
            criteria={'true': 'Abnormal volatility or an excessive trigger range makes entry disorderly.',
                      'false': 'Volatility and trigger range are orderly enough for a rule entry.'}),
    }


@dataclass(frozen=True)
class ReviewAnswer:
    model: str
    enter_full: D
    reduce: D
    skip: D
    exhaustion: D
    disorderly: D

    def __post_init__(self):
        values = (self.enter_full, self.reduce, self.skip, self.exhaustion, self.disorderly)
        if not isinstance(self.model, str) or not re.fullmatch(r'[a-zA-Z0-9_./:-]{1,200}', self.model):
            raise ValueError('invalid returned model')
        if any(_number(v) is None or not 0 <= v <= 1 for v in values):
            raise ValueError('invalid probability')
        if abs(self.enter_full+self.reduce+self.skip-1) > D('.000001'):
            raise ValueError('invalid action distribution')


def parse_answer(payload):
    """Malformed/missing opinions are absent, never replay-stopping exceptions."""
    try:
        answers = payload['answers']
        if set(answers) != {'action', 'exhaustion', 'disorderly'}:
            return None
        action, probs = answers['action'], answers['action']['probabilities']
        if set(probs) != {'enter_full', 'reduce', 'skip'} or action['choice'] not in probs:
            return None
        values = [_number(probs[k]) for k in ('enter_full', 'reduce', 'skip')]
        if None in values or _number(probs[action['choice']]) != max(values):
            return None
        return ReviewAnswer(payload['model'], *values, _number(answers['exhaustion']['noul']),
                            _number(answers['disorderly']['noul']))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


@dataclass(frozen=True)
class LabelConfig:
    version: str = LABEL_CONFIG_VERSION
    t_skip: D = D('.7')
    t_exh: D = D('.7')
    t_reduce: D = D('.6')
    t_dis: D = D('.6')

    def __post_init__(self):
        if self.version != LABEL_CONFIG_VERSION or any(_number(v) is None or not 0 <= v <= 1
            for v in (self.t_skip, self.t_exh, self.t_reduce, self.t_dis)):
            raise ValueError('invalid branch-A threshold configuration')


MULTIPLIERS = {'AGREE': D(1), 'REDUCE': D('.5'), 'VETO': D(0), 'NO_VIEW': D(1)}


def label_a(answer, config=LabelConfig()):
    label = 'NO_VIEW' if answer is None else 'VETO' if (
        answer.skip >= config.t_skip or answer.exhaustion >= config.t_exh) else 'REDUCE' if (
        answer.reduce+answer.skip >= config.t_reduce or answer.disorderly >= config.t_dis) else 'AGREE'
    return label, MULTIPLIERS[label]


def join_labels(a, b=None):
    """Reserved A/B seam. Branch B has no policy or execution in this experiment."""
    if b is not None:
        raise ValueError('branch B is not implemented')
    return a
