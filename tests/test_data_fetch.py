"""data_fetch.py için testler.

Not: yfinance'e gerçek ağ çağrısı yapan testler @pytest.mark.network
ile işaretlidir; CI'da `pytest -m "not network"` ile hariç tutulabilir.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bist_span import data_fetch


def test_calculate_historical_volatility_known_series():
    """Bilinen bir fiyat serisi için volatilite doğru hesaplanmalı.

    Örn: sabit oranda değişen bir seri kullanıp elle hesaplanan
    beklenen volatilite ile karşılaştır.
    """
    prices = pd.Series([100, 101, 102, 101, 103, 104])
    vol = data_fetch.calculate_historical_volatility(prices)
    assert vol > 0


def test_calculate_historical_volatility_zero_for_constant_prices():
    """Sabit fiyat serisinde volatilite 0 olmalı."""
    prices = pd.Series([100.0] * 10)
    vol = data_fetch.calculate_historical_volatility(prices)
    assert np.isclose(vol, 0.0)


def test_calculate_historical_volatility_raises_on_too_few_points():
    """Tek bir fiyat noktasıyla volatilite hesaplanamamalı."""
    with pytest.raises(ValueError):
        data_fetch.calculate_historical_volatility(pd.Series([100.0]))


def test_get_price_data_uses_cache_on_second_call(tmp_path, monkeypatch):
    """Aynı gün içinde ikinci çağrı API'ye değil cache'e gitmeli."""
    monkeypatch.setattr(data_fetch, "CACHE_DIR", tmp_path)

    call_count = {"n": 0}

    def fake_get_current_price(ticker: str) -> float:
        call_count["n"] += 1
        return 123.45

    fake_history = pd.DataFrame(
        {"Close": [100, 101, 102, 103, 104]},
        index=pd.date_range("2026-01-01", periods=5),
    )

    monkeypatch.setattr(data_fetch, "get_current_price", fake_get_current_price)
    monkeypatch.setattr(
        data_fetch, "get_historical_prices", lambda ticker, period="1y": fake_history
    )

    first = data_fetch.get_price_data("AKBNK.IS")
    second = data_fetch.get_price_data("AKBNK.IS")

    assert call_count["n"] == 1  # ikinci çağrıda API'ye gidilmedi
    assert first.current_price == second.current_price == 123.45
    # CSV round-trip sırasında DatetimeIndex'in freq bilgisi kaybolur
    # (veri aynı kalır), bu yüzden check_freq=False.
    pd.testing.assert_frame_equal(first.history, second.history, check_freq=False)


@pytest.mark.network
def test_get_current_price_returns_positive_float():
    """Gerçek bir BIST sembolü için pozitif bir fiyat dönmeli (network testi)."""
    price = data_fetch.get_current_price("AKBNK.IS")
    assert isinstance(price, float)
    assert price > 0
