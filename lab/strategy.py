"""Close-of-day targets. Vibe-Trading performs the one-bar execution shift."""


class SignalEngine:
    def __init__(self, fast=20, slow=50, buy_and_hold=False):
        if not (isinstance(fast, int) and isinstance(slow, int) and 0 < fast < slow):
            raise ValueError("Expected integer windows: 0 < fast < slow")
        self.fast = fast
        self.slow = slow
        self.buy_and_hold = buy_and_hold

    def generate(self, data_map):
        signals = {}
        for symbol, frame in data_map.items():
            close = frame["close"]
            if self.buy_and_hold:
                signals[symbol] = close.notna().astype(float)
            else:
                signals[symbol] = (
                    close.rolling(self.fast).mean() > close.rolling(self.slow).mean()
                ).astype(float)
        return signals
