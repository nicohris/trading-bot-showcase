"""
Tests unitaires du PositionSizer.

Logique purement mathématique — pas de mocks nécessaires.
"""

import pytest

from risk.position_sizer import PositionSizer


class TestPositionSizer:
    def setup_method(self):
        self.sizer = PositionSizer()

    def test_basic_position_size(self):
        """1% de 10000 USDT avec SL à 1000 USDT sous l'entrée."""
        qty = self.sizer.calculate_quantity(
            equity=10000.0,
            risk_pct=1.0,
            entry_price=40000.0,
            stop_loss=39000.0,  # distance = 1000
        )
        # risk_amount = 100, distance = 1000, qty = 0.1
        assert qty == pytest.approx(0.1, rel=1e-4)

    def test_zero_equity_returns_zero(self):
        qty = self.sizer.calculate_quantity(
            equity=0.0, risk_pct=1.0, entry_price=40000.0, stop_loss=39000.0
        )
        assert qty == 0.0

    def test_zero_distance_returns_zero(self):
        """Stop-loss au même niveau que l'entrée → division par zéro évitée."""
        qty = self.sizer.calculate_quantity(
            equity=10000.0, risk_pct=1.0, entry_price=40000.0, stop_loss=40000.0
        )
        assert qty == 0.0

    def test_stop_loss_calculation_buy(self):
        """SL pour long = entry - 1.5 * ATR."""
        stop = self.sizer.calculate_stop_loss(
            entry_price=40000.0, atr=500.0, atr_multiplier=1.5, side="BUY"
        )
        assert stop == pytest.approx(40000.0 - 750.0)

    def test_stop_loss_calculation_sell(self):
        """SL pour short = entry + 1.5 * ATR."""
        stop = self.sizer.calculate_stop_loss(
            entry_price=40000.0, atr=500.0, atr_multiplier=1.5, side="SELL"
        )
        assert stop == pytest.approx(40000.0 + 750.0)

    def test_stop_loss_invalid_atr(self):
        with pytest.raises(ValueError):
            self.sizer.calculate_stop_loss(entry_price=40000.0, atr=0.0)

    def test_take_profit_at_1r(self):
        """TP à 1R = entrée + distance_stop."""
        tp = self.sizer.calculate_take_profit(
            entry_price=40000.0, stop_loss=39000.0, r_multiple=1.0
        )
        assert tp == pytest.approx(41000.0)

    def test_take_profit_at_2r(self):
        tp = self.sizer.calculate_take_profit(
            entry_price=40000.0, stop_loss=39000.0, r_multiple=2.0
        )
        assert tp == pytest.approx(42000.0)

    def test_trailing_stop_only_moves_up_for_long(self):
        """Le trailing stop ne doit jamais reculer pour un long."""
        # Stop initial à 39500
        new_stop = self.sizer.calculate_trailing_stop(
            current_price=40500.0,
            atr=500.0,
            atr_multiplier=1.5,
            side="BUY",
            current_trailing=39500.0,
        )
        # new = 40500 - 750 = 39750 > 39500 → avance
        assert new_stop == pytest.approx(39750.0)

    def test_trailing_stop_does_not_move_down_for_long(self):
        """Si le prix a chuté, le stop ne doit pas reculer."""
        new_stop = self.sizer.calculate_trailing_stop(
            current_price=39800.0,
            atr=500.0,
            atr_multiplier=1.5,
            side="BUY",
            current_trailing=39500.0,
        )
        # new = 39800 - 750 = 39050 < 39500 → pas de changement
        assert new_stop == pytest.approx(39500.0)

    def test_risk_amount(self):
        risk = self.sizer.calculate_risk_amount(equity=10000.0, risk_pct=1.0)
        assert risk == pytest.approx(100.0)
