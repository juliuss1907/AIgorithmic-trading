"""Advisory alerts retain the entry-time opinion and existing delivery semantics."""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from intraday import trade_alerts
from intraday.execution.journal import ExecutionJournal
from test_trade_alerts import ACCOUNT, T0, Bot, order


@pytest.mark.parametrize('allowed,verdict', [(False, 'phản đối'), (True, 'đồng ý')])
def test_soak_headline_describes_rule_and_jev_is_only_an_opinion(allowed, verdict):
    observation = SimpleNamespace(side=-1, close=D(100), atr=D(1), size_multiplier=D('.4'), bar_close=T0)
    text = trade_alerts.soak_signal_message(symbol='ETHUSDT', market='perp', observation=observation,
        profile='setup2_slots_v1', exit_level=D(105), direction='STRONG BUY', confidence=.49, allowed=allowed)
    assert text.startswith('[SOAK] ETHUSDT Perp SHORT: tín hiệu luật (soak, không đặt lệnh)\n')
    assert f'Jev: STRONG BUY 0.49 ({verdict})' in text
    assert 'không vào' not in text


@pytest.mark.parametrize('advice,line', [
    (dict(direction='sell', confidence=.92, would_allow=True, reasons=[]), 'SELL 0.92, đồng ý'),
    (dict(direction='strong_buy', confidence=.49, would_allow=False, reasons=['setup2_requires_jev_sell']),
     'STRONG BUY 0.49, phản đối'),
    ('unavailable', 'không có câu trả lời'),
    (None, None),
])
def test_demo_alert_uses_historical_entry_advice_once_even_after_route_changes(tmp_path, advice, line):
    journal = ExecutionJournal(tmp_path / 'execution.sqlite')
    bot = Bot()
    notify = lambda: trade_alerts.notify_demo(journal, bot, exit_level_for=lambda *args: None, now=T0)
    assert notify() is None
    portfolio_account = ACCOUNT.model_copy(update={'market': None})
    journal.save_portfolio(portfolio_account, {'routes': {'spot:ETHUSDT': {'entry_id': 'entry', 'entry_jev': advice}}},
                           now=T0, kind='portfolio_entry_reserved')
    order(journal, 'entry', 'FILLED', at=T0, price=D(100), source_id='setup2:rule:123')
    journal.save_portfolio(portfolio_account, {'routes': {'spot:ETHUSDT': {'entry_id': 'later', 'entry_jev': 'unavailable'}}},
                           now=T0, kind='portfolio_entry_reserved')
    assert notify() == {'delivered': 1, 'failed': 0}
    [message] = bot.sent
    assert message.startswith('[DEMO] VÀO ETHUSDT Spot LONG')
    assert (f'Jev (tư vấn): {line}' in message) if line else ('Jev (tư vấn)' not in message)
    assert notify() is None and len(bot.sent) == 1


@pytest.mark.parametrize('failure', ['lookup', 'malformed'])
def test_optional_advice_cannot_break_entry_notification(tmp_path, monkeypatch, failure):
    journal = ExecutionJournal(tmp_path / 'execution.sqlite')
    bot = Bot()
    notify = lambda: trade_alerts.notify_demo(journal, bot, exit_level_for=lambda *args: None, now=T0)
    notify()
    order(journal, 'entry', 'FILLED', at=T0, price=D(100), source_id='setup2:rule:123')
    def advice(intent):
        if failure == 'lookup':
            raise ValueError('unreadable optional advice')
        return {'direction': None, 'confidence': None}
    monkeypatch.setattr(journal, 'entry_jev', advice, raising=False)
    assert notify() == {'delivered': 1, 'failed': 0}
    assert 'Jev (tư vấn)' not in bot.sent[0]
    assert notify() is None
