"""Opt-in historical research sizing; never changes recorded-Jev defaults."""

from intraday.replay_v2.mixed_book import MixedBook


class HistoricalBook(MixedBook):
    def allocation_base(self, marks):
        if self.config.capital_growth == 'equity':
            # Revalue only new entry budgets. Existing positions are not rebalanced.
            return self.equity(marks)
        return super().allocation_base(marks)
