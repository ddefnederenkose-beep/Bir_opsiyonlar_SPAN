"""span_engine.py için testler."""

from __future__ import annotations

import pytest

from bist_span import span_engine
from bist_span.risk_params import RiskParams
from bist_span.span_engine import OptionPosition


def test_black_scholes_call_price_at_the_money():
    """At-the-money call fiyatı pozitif ve makul bir aralıkta olmalı."""
    price = span_engine.black_scholes_price(
        spot=100,
        strike=100,
        time_to_expiry=30 / 365,
        risk_free_rate=0.45,
        volatility=0.35,
        option_type="call",
    )
    assert price > 0


def test_black_scholes_put_call_parity():
    """Put-call parity: C - P = S - K*e^(-rT) yaklaşık sağlanmalı."""
    import math

    spot, strike, t, r, sigma = 100, 100, 30 / 365, 0.45, 0.35
    call = span_engine.black_scholes_price(spot, strike, t, r, sigma, "call")
    put = span_engine.black_scholes_price(spot, strike, t, r, sigma, "put")
    expected = spot - strike * math.exp(-r * t)
    assert call - put == pytest.approx(expected, rel=1e-3)


def test_generate_risk_scenarios_returns_16_scenarios():
    """SPAN standart olarak 16 senaryo üretmeli."""
    scenarios = span_engine.generate_risk_scenarios(
        spot=100,
        volatility=0.35,
        price_scan_range=0.15,
        volatility_scan_range=0.05,
        extreme_move_multiplier=2.0,
        extreme_move_covered_fraction=0.35,
    )
    assert len(scenarios) == 16


def test_scanning_risk_picks_worst_case():
    """Scanning risk, en büyük zararı (en negatif P&L) baz almalı."""
    pnls = [10.0, -50.0, 20.0, -5.0]
    result = span_engine.scanning_risk(pnls)
    assert result == pytest.approx(50.0)


def test_calculate_span_margin_is_at_least_som():
    """Final teminat her zaman SOM'dan küçük olamaz."""
    position = OptionPosition(
        ticker="AKBNK",
        strike=50,
        option_type="call",
        contracts=-10,  # kısa pozisyon
        time_to_expiry=30 / 365,
        risk_free_rate=0.45,
    )
    risk_params = RiskParams(
        ticker="AKBNK",
        price_scan_range=0.15,  # %15 (gerçek PSR skalası: bkz. risk_params.py)
        volatility_scan_range=0.05,
        extreme_move_multiplier=2.0,
        extreme_move_covered_fraction=0.35,
        intra_commodity_spread_charge=0.0,
        short_option_minimum=100.0,
    )
    result = span_engine.calculate_span_margin(
        position=position,
        spot=50,
        volatility=0.35,
        risk_params=risk_params,
    )
    assert result["total_initial_margin"] >= result["short_option_minimum"]


def test_calculate_scenario_pnl_applies_contract_size():
    """P&L, contract_size ile tam orantılı ölçeklenmeli (bug: eskiden hiç uygulanmıyordu).

    VİOP pay opsiyonlarında standart kontrat büyüklüğü 100 pay/kontrat.
    Bu çarpan olmadan Scan Risk, SOM ile aynı birimde olmuyor (SOM zaten
    Takasbank'tan kontrat başına toplam TL olarak geliyor) ve
    max(SOM, Scan Risk) karşılaştırması anlamsızlaşıyor.
    """
    base_kwargs = dict(
        ticker="THYAO",
        strike=280,
        option_type="call",
        contracts=-1,
        time_to_expiry=0.1096,
        risk_free_rate=0.35,
    )
    scenario = {"price_shock": 0.134, "vol_shock": 0.28, "is_extreme": False}

    position_1x = OptionPosition(**base_kwargs, contract_size=1)
    position_100x = OptionPosition(**base_kwargs, contract_size=100)

    pnl_1x = span_engine.calculate_scenario_pnl(position_1x, 301.50, 0.3021, scenario)
    pnl_100x = span_engine.calculate_scenario_pnl(
        position_100x, 301.50, 0.3021, scenario
    )

    assert pnl_100x == pytest.approx(pnl_1x * 100)


def test_option_position_contract_size_defaults_to_100():
    """OptionPosition, contract_size verilmezse VİOP standardı 100'ü kullanmalı."""
    position = OptionPosition(
        ticker="THYAO",
        strike=280,
        option_type="call",
        contracts=-1,
        time_to_expiry=0.1096,
        risk_free_rate=0.35,
    )
    assert position.contract_size == 100


# ---------------------------------------------------------------------------
# THYAO_SPAN_Hesaplama-2.xlsx referans doğrulaması
#
# Kullanıcının hazırladığı Excel hesaplayıcıdan (Google Drive) hücre hücre
# alınmış 32 satır (16 call + 16 put). Girdi: THYAO K=280, S0=301.50,
# T=0.1096, vol=0.3021, r=0.35, PSR=0.134, VSR=0.28, EMM=3, EMCF=0.32,
# kontrat_büyüklüğü=100, kısa 1 kontrat.
#
# Bu tablo, projede daha önce yaşanan iki gerçek hatayı ortaya çıkardı:
# 1) vol_shock TOPLAMSAL değil ÇARPIMSAL uygulanmalıymış (vol*(1+VSR)).
# 2) Extreme move senaryoları vol_shock=0 değil, her zaman vol YUKARI
#    şoku kullanmalıymış.
# Her satır (senaryo, fiyat çarpanı, vol yönü) -> beklenen "Kısa K/Z" (TL).
# ---------------------------------------------------------------------------
_THYAO_INPUTS = dict(
    spot=301.50,
    strike=280,
    time_to_expiry=0.1096,
    volatility=0.3021,
    risk_free_rate=0.35,
)
_THYAO_RISK_PARAMS = RiskParams(
    ticker="THYAO",
    price_scan_range=0.134,
    volatility_scan_range=0.28,
    extreme_move_multiplier=3.0,
    extreme_move_covered_fraction=0.32,
    intra_commodity_spread_charge=0.0,  # tek bacaklı/tek vadeli -> 0
    short_option_minimum=1640.0,
)

# (senaryo no, fiyat çarpanı, vol yönü (+1/-1), beklenen Kısa K/Z)
_THYAO_CALL_ROWS = [
    (1, 0, 1, -194.6821341),
    (2, 0, -1, 134.2756423),
    (3, 1 / 3, 1, -1361.044829),
    (4, 1 / 3, -1, -1171.323088),
    (5, -1 / 3, 1, 851.0223747),
    (6, -1 / 3, -1, 1341.176892),
    (7, 2 / 3, 1, -2607.050802),
    (8, 2 / 3, -1, -2508.70606),
    (9, -2 / 3, 1, 1732.075967),
    (10, -2 / 3, -1, 2327.580959),
    (11, 1, 1, -3901.039329),
    (12, 1, -1, -3853.741556),
    (13, -1, 1, 2413.855041),
    (14, -1, -1, 2974.772881),
    (15, 3, 1, -3818.849877),  # extreme up
    (16, -3, 1, 1084.70921),  # extreme down (vol_shock hâlâ yukarı!)
]
_THYAO_PUT_ROWS = [
    (1, 0, 1, -194.6821341),
    (2, 0, -1, 134.2756423),
    (3, 1 / 3, 1, -14.34482929),
    (4, 1 / 3, -1, 175.3769124),
    (5, -1 / 3, 1, -495.6776253),
    (6, -1 / 3, -1, -5.523108181),
    (7, 2 / 3, 1, 86.34919771),
    (8, 2 / 3, -1, 184.6939397),
    (9, -2 / 3, 1, -961.3240335),
    (10, -2 / 3, -1, -365.819041),
    (11, 1, 1, 139.060671),
    (12, 1, -1, 186.3584442),
    (13, -1, 1, -1626.244959),
    (14, -1, -1, -1065.327119),
    (15, 3, 1, 59.64612296),  # extreme up
    (16, -3, 1, -2793.78679),  # extreme down (vol_shock hâlâ yukarı!)
]


@pytest.mark.parametrize(
    "option_type,row",
    [("call", row) for row in _THYAO_CALL_ROWS]
    + [("put", row) for row in _THYAO_PUT_ROWS],
    ids=[f"call-sen{row[0]}" for row in _THYAO_CALL_ROWS]
    + [f"put-sen{row[0]}" for row in _THYAO_PUT_ROWS],
)
def test_thyao_scenario_pnl_matches_excel_reference(option_type, row):
    """32 satırın (16 call + 16 put) her biri, Excel'deki Kısa K/Z ile eşleşmeli."""
    scenario_no, price_multiplier, vol_direction, expected_pnl = row
    is_extreme = scenario_no >= 15
    # price_multiplier, Excel'deki "Fiyat Çarpanı" kolonuyla birebir aynı:
    # normal satırlarda +/-{1/3,2/3,1}, extreme satırlarda zaten +/-EMM (3/-3)
    # -- ikisinde de fiyat şoku basitçe price_multiplier * PSR'dir.
    price_shock = price_multiplier * _THYAO_RISK_PARAMS.price_scan_range

    if is_extreme:
        scenario = {
            "price_shock": price_shock,
            "vol_shock": _THYAO_RISK_PARAMS.volatility_scan_range,  # hep yukarı
            "is_extreme": True,
            "covered_fraction": _THYAO_RISK_PARAMS.extreme_move_covered_fraction,
        }
    else:
        scenario = {
            "price_shock": price_shock,
            "vol_shock": vol_direction * _THYAO_RISK_PARAMS.volatility_scan_range,
            "is_extreme": False,
            "covered_fraction": 1.0,
        }

    position = OptionPosition(
        ticker="THYAO",
        strike=_THYAO_INPUTS["strike"],
        option_type=option_type,
        contracts=-1,
        time_to_expiry=_THYAO_INPUTS["time_to_expiry"],
        risk_free_rate=_THYAO_INPUTS["risk_free_rate"],
    )
    pnl = span_engine.calculate_scenario_pnl(
        position, _THYAO_INPUTS["spot"], _THYAO_INPUTS["volatility"], scenario
    )
    assert pnl == pytest.approx(expected_pnl, abs=0.05)


@pytest.mark.parametrize(
    "option_type,expected_scan_risk",
    [("call", 3901.039329), ("put", 2793.78679)],
)
def test_thyao_calculate_span_margin_matches_excel_sonuclar(
    option_type, expected_scan_risk
):
    """calculate_span_margin'in tam çıktısı, Excel'in SONUÇLAR bölümüyle eşleşmeli.

    Excel: CALL Scanning Risk=3.901,04 (Aktif Senaryo #11) -> SOM'u (1.640)
    geçtiği için Toplam=3.901,04. PUT Scanning Risk=2.793,79 (Aktif
    Senaryo #16) -> yine SOM'u geçtiği için Toplam=2.793,79.
    """
    position = OptionPosition(
        ticker="THYAO",
        strike=_THYAO_INPUTS["strike"],
        option_type=option_type,
        contracts=-1,
        time_to_expiry=_THYAO_INPUTS["time_to_expiry"],
        risk_free_rate=_THYAO_INPUTS["risk_free_rate"],
    )
    result = span_engine.calculate_span_margin(
        position=position,
        spot=_THYAO_INPUTS["spot"],
        volatility=_THYAO_INPUTS["volatility"],
        risk_params=_THYAO_RISK_PARAMS,
    )
    assert result["scan_risk"] == pytest.approx(expected_scan_risk, abs=0.01)
    assert result["total_initial_margin"] == pytest.approx(expected_scan_risk, abs=0.01)
    assert result["intra_commodity_spread_charge"] == 0.0  # tek bacaklı pozisyon


def test_generate_risk_scenarios_extreme_uses_multiplicative_vol_up():
    """Extreme senaryolar vol_shock=0 DEĞİL, her zaman +VSR (vol yukarı) kullanmalı.

    (Excel referansıyla doğrulanan bug fix -- eskiden vol_shock=0.0 idi.)
    """
    scenarios = span_engine.generate_risk_scenarios(
        spot=100,
        volatility=0.30,
        price_scan_range=0.134,
        volatility_scan_range=0.28,
        extreme_move_multiplier=3.0,
        extreme_move_covered_fraction=0.32,
    )
    extreme_scenarios = [s for s in scenarios if s["is_extreme"]]
    assert len(extreme_scenarios) == 2
    for s in extreme_scenarios:
        assert s["vol_shock"] == pytest.approx(0.28)


def test_calculate_scenario_pnl_applies_vol_shock_multiplicatively():
    """vol_shock TOPLAMSAL değil ÇARPIMSAL uygulanmalı: vol*(1+shock), vol+shock değil."""
    position = OptionPosition(
        ticker="THYAO",
        strike=280,
        option_type="call",
        contracts=-1,
        time_to_expiry=0.1096,
        risk_free_rate=0.35,
    )
    scenario = {"price_shock": 0.0, "vol_shock": 0.28, "is_extreme": False}
    pnl = span_engine.calculate_scenario_pnl(position, 301.50, 0.3021, scenario)
    # Excel: Sen. 1 (fiyat sabit, vol yukarı) -> Kısa K/Z = -194.6821341
    assert pnl == pytest.approx(-194.6821341, abs=0.05)
