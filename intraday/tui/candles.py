"""H4 candles drawn with block characters, from the `/api/setup2/chart` payload.

One column per bar and two price steps per row: a body over both halves of a cell is `█`, over the top half `▀`,
over the bottom half `▄`; a wick is `│`. EMA50, EMA200 and the Donchian entry channel are drawn where no candle
is; an entry signal is `▼` (short) above or `▲` (long) below its bar, and its initial stop `×`."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text

AXIS = 10                                    # columns of the price axis on the right
UP, DOWN = "green", "red"
LINES = (("upper", "─", "grey50"), ("lower", "─", "grey50"), ("ema200", "·", "magenta"), ("ema50", "·", "yellow"))
VOLUME = " ▁▂▃▄▅▆▇█"


def _price(value: float) -> str:
    return f"{value:.6g}"


def render(payload: dict, width: int, height: int, *, gap: bool = False) -> Text:
    """The last bars that fit in `width` columns: `height-2` price rows, one volume row and a date axis. With `gap`
    every bar takes two columns, the second one blank but for the lines: fewer, wider-spaced candles."""
    bars = payload.get("bars") or []
    rows = height-2
    columns = (width-AXIS)//(2 if gap else 1)
    if not bars or rows < 3 or columns < 5:
        return Text("Chưa đủ nến để vẽ.", style="dim")
    first = max(0, len(bars)-columns)
    view = bars[first:]
    lines = {key: (payload.get(key) or [])[first:] for key, _, _ in LINES}
    marks = [dict(m, k=m["k"]-first) for m in payload.get("marks") or [] if m["k"] >= first]
    values = [v for b in view for v in (b["high"], b["low"])]
    values += [v for series in lines.values() for v in series if v is not None]+[m["stop"] for m in marks]
    low, high = min(values), max(values)
    pad = (high-low)*0.02 or high*0.01
    low, high = low-pad, high+pad
    steps = 2*rows

    def step(value):                         # 0 = top half of the first row
        return min(steps-1, max(0, int((high-value)/(high-low)*steps)))

    background = [[(" ", "")]*len(view) for _ in range(rows)]
    for key, glyph, style in LINES:
        for k, value in enumerate(lines[key]):
            if value is not None:
                background[step(value)//2][k] = (glyph, style)
    grid = [list(row) for row in background]
    for k, b in enumerate(view):
        style = UP if b["close"] >= b["open"] else DOWN
        top, bottom = step(max(b["open"], b["close"])), step(min(b["open"], b["close"]))
        for row in range(step(b["high"])//2, step(b["low"])//2+1):
            halves = [s for s in (2*row, 2*row+1) if top <= s <= bottom]
            glyph = "│" if not halves else "█" if len(halves) == 2 else "▀" if halves[0] == 2*row else "▄"
            grid[row][k] = (glyph, style)
    for m in marks:
        if not 0 <= m["k"] < len(view):
            continue
        b = view[m["k"]]
        row = step(b["high"])//2-1 if m["side"] < 0 else step(b["low"])//2+1
        if 0 <= row < rows:
            grid[row][m["k"]] = ("▼" if m["side"] < 0 else "▲", "bold red" if m["side"] < 0 else "bold green")
        for k in range(m["k"]+1, min(len(view), m.get("end", m["k"]+1)+1)):
            grid[step(m["stop"])//2][k] = ("×", "bold orange1")
    last = view[-1]["close"]
    labels = {step(high-(high-low)*i/(rows-1))//2: high-(high-low)*i/(rows-1) for i in range(0, rows, 4)}
    labels[step(last)//2] = last
    text = Text(no_wrap=True, overflow="crop")
    for row in range(rows):
        for k, (glyph, style) in enumerate(grid[row]):
            text.append(glyph, style=style)
            if gap:
                text.append(*background[row][k])
        if row in labels:
            is_last = labels[row] == last
            text.append(f" {_price(labels[row]):<{AXIS-1}}",
                        style=("reverse " + (UP if last >= view[-1]["open"] else DOWN)) if is_last else "dim")
        text.append("\n")
    top_volume = max(b["volume"] for b in view) or 1.0
    for b in view:
        text.append(VOLUME[round(b["volume"]/top_volume*(len(VOLUME)-1))]+(" " if gap else ""),
                    style=UP if b["close"] >= b["open"] else DOWN)
    text.append(" volume\n", style="dim")
    scale = 2 if gap else 1
    axis, k = [" "]*(scale*len(view)), 0
    while k < len(view):
        opened = datetime.fromisoformat(view[k]["at"])
        if opened.hour == 0 and scale*k+5 <= len(axis):
            axis[scale*k:scale*k+5] = opened.strftime("%d/%m")
            k += 7//scale+1
        else:
            k += 1
    text.append("".join(axis), style="dim")
    return text
