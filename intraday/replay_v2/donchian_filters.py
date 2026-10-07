"""Causal closed-H4 features and approximate M15 volume-at-price, research only."""

from decimal import Decimal

import numpy as np

ZERO, ONE = Decimal(0), Decimal(1)


def indicator_series(rows):
    """EMA SMA seeds; ATR simple TR14 (legacy sizing); DMI/ADX Wilder14."""
    result, trs, ups, downs, dxs = [], [], [], [], []
    ema50 = ema200 = tr_rma = up_rma = down_rma = adx = None
    for i, bar in enumerate(rows):
        prior50, prior_adx = ema50, adx
        if i == 49:
            ema50 = sum((b.close for b in rows[:50]), ZERO)/50
        elif ema50 is not None:
            ema50 += Decimal(2)/51*(bar.close-ema50)
        if i == 199:
            ema200 = sum((b.close for b in rows[:200]), ZERO)/200
        elif ema200 is not None:
            ema200 += Decimal(2)/201*(bar.close-ema200)
        plus = minus = None
        if i:
            previous = rows[i-1]
            tr = max(bar.high-bar.low, abs(bar.high-previous.close), abs(bar.low-previous.close))
            up, down = bar.high-previous.high, previous.low-bar.low
            trs.append(tr)
            ups.append(up if up > down and up > 0 else ZERO)
            downs.append(down if down > up and down > 0 else ZERO)
            if len(trs) == 14:
                tr_rma, up_rma, down_rma = (sum(v, ZERO)/14 for v in (trs, ups, downs))
            elif tr_rma is not None:
                tr_rma += (tr-tr_rma)/14
                up_rma += (ups[-1]-up_rma)/14
                down_rma += (downs[-1]-down_rma)/14
            if tr_rma is not None:
                plus, minus = (100*up_rma/tr_rma, 100*down_rma/tr_rma) if tr_rma else (ZERO, ZERO)
                dx = 100*abs(plus-minus)/(plus+minus) if plus+minus else ZERO
                dxs.append(dx)
                if len(dxs) == 14:
                    adx = sum(dxs, ZERO)/14
                elif adx is not None:
                    adx += (dx-adx)/14
        result.append(dict(close=bar.close, volume=bar.volume,
            volume_ma=sum((b.volume for b in rows[i-20:i]), ZERO)/20 if i >= 20 else None,
            atr=sum(trs[-14:], ZERO)/14 if len(trs) >= 14 else None,
            ema50=ema50, prior_ema50=prior50, ema200=ema200, adx=adx,
            prior_adx=prior_adx, plus_di=plus, minus_di=minus))
    return result


def observation(rows, entry, exit):
    if len(rows) < max(entry, exit)+1:
        raise ValueError('insufficient closed Donchian history')
    upper, lower = max(b.high for b in rows[-entry-1:-1]), min(b.low for b in rows[-entry-1:-1])
    high_exit, low_exit = max(b.high for b in rows[-exit-1:-1]), min(b.low for b in rows[-exit-1:-1])
    close = rows[-1].close
    return dict(upper=upper, lower=lower, upper_exit=high_exit, lower_exit=low_exit,
                long_entry=close > upper, short_entry=close < lower,
                long_exit=close < low_exit, short_exit=close > high_exit)


def volume_profile(rows, bins=50):
    """Uniform volume within each M15 low/high, NOT tick-level volume-at-price."""
    if not rows or not 1 <= bins <= 500:
        raise ValueError('profile requires candles and valid bins')
    values = np.asarray([(float(b.low), float(b.high), float(b.volume)) for b in rows])
    if not np.isfinite(values).all() or (values[:, 0] <= 0).any() or (
            values[:, 1] < values[:, 0]).any() or (values[:, 2] < 0).any():
        raise ValueError('invalid profile prices or volume')
    low, high, volume = values.T
    total = volume.sum()
    if not total:
        return None
    bottom, top = low.min(), high.max()
    if bottom == top:
        return dict(poc=bottom, val=bottom, vah=top, volumes=[float(total)], covered_volume=float(total))
    step = (top-bottom)/bins
    edges = np.linspace(bottom, top, bins+1)
    left = np.clip(np.floor((low-bottom)/step).astype(int), 0, bins-1)
    right = np.clip(np.floor((high-bottom)/step).astype(int), 0, bins-1)
    hist, diff = np.zeros(bins), np.zeros(bins+1)
    same = left == right
    np.add.at(hist, left[same], volume[same])
    selected = ~same
    a, z, lo, hi, v = left[selected], right[selected], low[selected], high[selected], volume[selected]
    density = v/(hi-lo)
    np.add.at(hist, a, density*(edges[a+1]-lo))
    np.add.at(hist, z, density*(hi-edges[z]))
    np.add.at(diff, a+1, density*step)
    np.add.at(diff, z, -density*step)
    hist += np.cumsum(diff)[:bins]
    hist = np.maximum(hist, 0)
    # Bin-edge floating roundoff must not turn mathematically tied bins into
    # different POCs. Relative tolerance1e-12 is also used for VA adjacency.
    poc = int(np.flatnonzero(np.isclose(hist, hist.max(), rtol=1e-12, atol=0))[0])
    first = last = poc
    covered = hist[poc]
    while covered < .7*total and (first > 0 or last < bins-1):
        above = hist[last+1] if last < bins-1 else -1
        below = hist[first-1] if first else -1
        tied = abs(above-below) <= 1e-12*max(abs(above),abs(below))
        if above >= below or tied:  # Equal adjacent volumes: upper row wins.
            last += 1
            covered += hist[last]
        else:
            first -= 1
            covered += hist[first]
    return dict(poc=float((edges[poc]+edges[poc+1])/2), val=float(edges[first]),
                vah=float(edges[last+1]), volumes=hist.tolist(), covered_volume=float(covered))


def entry_filters(f, side, level, *, adx_threshold=25):
    """All applicable blockers, not merely the first one, for ablation reports."""
    failed = []
    if level >= 1:
        if f['ema200'] is None or side*(f['close']-f['ema200']) <= 0:
            failed.append('ema200')
        if f['ema50'] is None or f['prior_ema50'] is None or (
                side*(f['close']-f['ema50']) <= 0 or side*(f['ema50']-f['prior_ema50']) <= 0):
            failed.append('ema50')
    if level >= 2 and adx_threshold is not None:
        if f['adx'] is None or f['prior_adx'] is None or f['adx'] <= adx_threshold or f['adx'] <= f['prior_adx']:
            failed.append('adx')
        if f['plus_di'] is None or f['minus_di'] is None or side*(f['plus_di']-f['minus_di']) <= 0:
            failed.append('dmi')
    if level >= 3 and (f['volume_ma'] is None or f['volume_ma'] <= 0 or f['volume'] <= Decimal('1.2')*f['volume_ma']):
        failed.append('volume_ma')
    if level >= 4:
        profile = f.get('profile')
        boundary = profile['vah' if side > 0 else 'val'] if profile else None
        if boundary is None or side*(f['close']-Decimal(str(boundary))) <= 0:
            failed.append('volume_profile')
    return failed
