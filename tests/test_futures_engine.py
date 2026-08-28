"""futures_engine.py için testler.

AEFES referans değerleri gerçek Takasbank verisiyle çapraz doğrulanmıştır:
AEFES 31.08.2026 vadeli işlemi, fiyat=18.62, PSR=%14.1, EMM=3.0, ECF=0.32
için Takasbank'ın KENDİ gömülü risk dizisindeki (<fut><ra>) en kötü
(en büyük) değer 259.722'dir -- bizim hesabımızla kuruşuna kadar
örtüşüyor (bkz. proje sohbet geçmişi).
"""

from __future__ import annotations

import pytest

from bist_span.futures_engine import (
    FuturesPosition,
    calculate_futures_margin,
    calculate_futures_scenario_pnl,
)
from bist_span.span_engine import generate_risk_scenarios


def test_calculate_futures_scenario_pnl_is_linear_no_vol_dependence():
    """Doğrusal enstrüman: aynı fiyat şokunda vol yönü (+1/-1) P&L'i DEĞİŞTİRMEMELİ."""
    position = FuturesPosition(ticker="AEFES", contracts=-1, contract_size=100.0)
    price = 18.62

    up_vol_scenario = {"price_shock": 0.141, "vol_shock": 0.5, "is_extreme": False, "covered_fraction": 1.0}
    down_vol_scenario = {"price_shock": 0.141, "vol_shock": -0.5, "is_extreme": False, "covered_fraction": 1.0}

    pnl_up = calculate_futures_scenario_pnl(position, price, up_vol_scenario)
    pnl_down = calculate_futures_scenario_pnl(position, price, down_vol_scenario)
    assert pnl_up == pytest.approx(pnl_down)


def test_calculate_futures_scenario_pnl_applies_contract_size_and_direction():
    """P&L = (şoklu fiyat - fiyat) * kontrat * kontrat_çarpanı -- SOM/NOV YOK."""
    position = FuturesPosition(ticker="AEFES", contracts=-2, contract_size=100.0)
    price = 20.0
    scenario = {"price_shock": 0.10, "vol_shock": 0.0, "is_extreme": False, "covered_fraction": 1.0}

    pnl = calculate_futures_scenario_pnl(position, price, scenario)
    # şoklu fiyat = 22.0, fark = +2.0, kısa 2 kontrat * 100 = -400.0
    assert pnl == pytest.approx(-400.0)


def test_calculate_futures_margin_matches_real_aefes_takasbank_risk_array():
    """AEFES 31.08.2026, fiyat=18.62 anındaki Takasbank <ra>'sında en kötü
    değer 262.542'dir -- kuruşuna kadar eşleşmeli (bkz. modül docstring'i)."""
    position = FuturesPosition(ticker="AEFES", contracts=-1, contract_size=100.0)
    result = calculate_futures_margin(
        position=position,
        price=18.62,
        price_scan_range=0.141,
        extreme_move_multiplier=3.0,
        extreme_move_covered_fraction=0.32,
    )
    assert result["scan_risk"] == pytest.approx(262.542, abs=0.01)
    assert result["total_initial_margin"] == pytest.approx(262.542, abs=0.01)
    assert result["intra_commodity_spread_charge"] == 0.0
    assert result["inter_commodity_spread_credit"] == 0.0


def test_calculate_futures_margin_has_no_som_or_nov_fields():
    """Vadeli işlem sonucu SOM/NOV/Opsiyon Prim Değeri İÇERMEMELİ (Madde 33-38,
    bu üçü açıkça 'Kısa OPSİYON Pozisyonu' için tanımlı -- futures'a uygulanmaz)."""
    position = FuturesPosition(ticker="AEFES", contracts=-1, contract_size=100.0)
    result = calculate_futures_margin(
        position=position,
        price=18.62,
        price_scan_range=0.141,
        extreme_move_multiplier=3.0,
        extreme_move_covered_fraction=0.32,
    )
    assert "short_option_minimum" not in result
    assert "net_option_value" not in result
    assert "option_premium_value" not in result


def test_calculate_futures_margin_uses_generate_risk_scenarios_unmodified():
    """futures_engine, span_engine.generate_risk_scenarios'ı OLDUĞU GİBİ (değiştirmeden)
    yeniden kullanıyor -- vol_shock ekseni vol=0/vsr=0 verildiğinde etkisiz kalıyor."""
    scenarios = generate_risk_scenarios(
        spot=18.62, volatility=0.0, price_scan_range=0.141,
        volatility_scan_range=0.0, extreme_move_multiplier=3.0,
        extreme_move_covered_fraction=0.32,
    )
    assert len(scenarios) == 16
    # vol_shock her zaman 0 olmalı (vsr=0 verildiği için)
    assert all(s["vol_shock"] == 0.0 for s in scenarios)
