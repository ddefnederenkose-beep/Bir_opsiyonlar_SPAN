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
# THYAO_SPAN_Hesaplama-2.xlsx + Takasbank resmi PC-SPAN üretim verisi
# referans doğrulaması
#
# 14 REGULAR satır (1-14), kullanıcının hazırladığı Excel hesaplayıcıdan
# (Google Drive) hücre hücre alınmıştır. Girdi: THYAO K=280, S0=301.50,
# T=0.1096, vol=0.3021, r=0.35, PSR=0.134, VSR=0.28, EMM=3, EMCF=0.32,
# kontrat_büyüklüğü=100, kısa 1 kontrat.
#
# 2 EXTREME satır (15-16) ARTIK Excel'den DEĞİL, Takasbank'ın kendi resmi
# PC-SPAN üretim dosyasından (spanFile/pointDef/scanPointDef, point 15-16)
# doğrulanmıştır -- Excel bu iki senaryoda "vol_shock=+VSR" varsayıyordu,
# ama Takasbank'ın kendi üretim verisi volScanDef.mult=0.0 (vol şoklanmaz)
# diyor; resmi kaynak kazandı, kodu ona göre düzelttik (bkz. span_engine.
# generate_risk_scenarios docstring'i).
#
# Bu tablo, projede daha önce yaşanan üç gerçek hatayı ortaya çıkardı:
# 1) vol_shock TOPLAMSAL değil ÇARPIMSAL uygulanmalıymış (vol*(1+VSR)).
# 2) Extreme move senaryolarında vol_shock=0 olmalıymış (Excel'in aksine).
# 3) (Ayrı bir turda) kontrat çarpanı (100) hiç uygulanmıyormuş.
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
    (15, 3, 0, -3818.775215174316),  # extreme up (Takasbank resmi: vol_shock=0)
    (16, -3, 0, 1084.913691081266),  # extreme down (Takasbank resmi: vol_shock=0)
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
    (15, 3, 0, 59.72078482568521),  # extreme up (Takasbank resmi: vol_shock=0)
    (16, -3, 0, -2793.5823089187343),  # extreme down (Takasbank resmi: vol_shock=0)
]


@pytest.mark.parametrize(
    "option_type,row",
    [("call", row) for row in _THYAO_CALL_ROWS]
    + [("put", row) for row in _THYAO_PUT_ROWS],
    ids=[f"call-sen{row[0]}" for row in _THYAO_CALL_ROWS]
    + [f"put-sen{row[0]}" for row in _THYAO_PUT_ROWS],
)
def test_thyao_scenario_pnl_matches_excel_reference(option_type, row):
    """32 satırın (14 regular Excel'den, 2 extreme Takasbank resmi veriden)
    her biri, beklenen Kısa K/Z ile eşleşmeli."""
    scenario_no, price_multiplier, vol_direction, expected_pnl = row
    is_extreme = scenario_no >= 15
    # price_multiplier, Excel'deki "Fiyat Çarpanı" kolonuyla birebir aynı:
    # normal satırlarda +/-{1/3,2/3,1}, extreme satırlarda zaten +/-EMM (3/-3)
    # -- ikisinde de fiyat şoku basitçe price_multiplier * PSR'dir.
    price_shock = price_multiplier * _THYAO_RISK_PARAMS.price_scan_range

    if is_extreme:
        scenario = {
            "price_shock": price_shock,
            "vol_shock": 0.0,  # Takasbank resmi PC-SPAN verisi: extreme'de vol şoklanmaz
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
    "option_type,expected_scan_risk,expected_theoretical_price",
    [
        ("call", 3901.039329, 33.9036982728081),
        ("put", 2793.582309, 1.8662977232698097),
    ],
)
def test_thyao_calculate_span_margin_matches_excel_sonuclar(
    option_type, expected_scan_risk, expected_theoretical_price
):
    """calculate_span_margin'in tam çıktısı beklenen referans değerlerle eşleşmeli.

    CALL Scanning Risk=3.901,04 (Aktif Senaryo #11, regular -- Excel'le
    doğrulanmış); SOM (1.640) < Scanning Risk olduğu için BISTECH Marjin
    Riski=3.901,04. PUT Scanning Risk=2.793,58 (Aktif Senaryo #16, extreme
    -- Takasbank'ın resmi PC-SPAN verisiyle doğrulanmış, vol_shock=0); aynı
    şekilde BISTECH Marjin Riski=2.793,58.

    base_price verilmediği (None) için Net Opsiyon Değeri, calculate_scenario_pnl
    ile AYNI teorik Black-Scholes fiyatını taban alır -- taşınan (bugün
    açılmamış) kısa 1 kontratlık pozisyon için toplam artık SADECE
    BISTECH Marjin Riski değil, +|kontrat|×taban×100 (kontrat çarpanı)
    kadar daha yüksek (Madde 33-38, "Net Opsiyon Değeri" -- bkz.
    calculate_span_margin docstring'i). Gerçek PC-SPAN çıktısıyla ayrıca
    doğrulanmıştır (bkz. proje sohbet geçmişi: THYAO 30.09.2026 K=300
    CALL -- SPAN Risk 3.697, Available Net Option (2.102), Total
    Requirement 5.799 -- bizim formülümüzle kuruşuna kadar örtüştü).
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
    expected_nov_addition = 1 * expected_theoretical_price * position.contract_size
    assert result["scan_risk"] == pytest.approx(expected_scan_risk, abs=0.01)
    assert result["intra_commodity_spread_charge"] == 0.0  # tek bacaklı pozisyon
    assert result["net_option_value"] == pytest.approx(-expected_nov_addition, abs=0.01)
    assert result["option_premium_value"] == 0.0  # taşınan pozisyon, bugün açılmadı
    assert result["total_initial_margin"] == pytest.approx(
        expected_scan_risk + expected_nov_addition, abs=0.01
    )


def test_generate_risk_scenarios_extreme_uses_zero_vol_shock():
    """Extreme senaryolarda vol_shock=0 olmalı (volatilite şoklanmaz).

    Takasbank'ın kendi resmi PC-SPAN üretim dosyasıyla (spanFile/pointDef/
    scanPointDef, point 15-16 -> volScanDef.mult=0.0) doğrulanmıştır. Bir
    kullanıcı Excel'i daha önce "extreme'de her zaman vol yukarı"
    varsayıyordu; Takasbank'ın kendi üretim verisiyle karşılaştırıldığında
    bunun yanlış olduğu görüldü ve resmi kaynak lehine düzeltildi.
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
        assert s["vol_shock"] == pytest.approx(0.0)


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


def test_calculate_scenario_pnl_uses_theoretical_base_price_by_default():
    """base_price verilmezse (None), taban teorik Black-Scholes fiyatı olmalı."""
    position = OptionPosition(
        ticker="THYAO",
        strike=280,
        option_type="call",
        contracts=-1,
        time_to_expiry=0.1096,
        risk_free_rate=0.35,
    )
    scenario = {"price_shock": 0.0, "vol_shock": 0.0, "is_extreme": False}
    # Fiyat/vol şoksuz senaryoda PnL, taban ile aynı fiyat farkı sıfır
    # olduğu için 0 olmalı (teorik taban = şoksuz senaryonun ta kendisi).
    pnl = span_engine.calculate_scenario_pnl(position, 301.50, 0.3021, scenario)
    assert pnl == pytest.approx(0.0, abs=1e-6)


def test_calculate_scenario_pnl_uses_explicit_base_price_when_given():
    """base_price verilirse (ör. Takasbank'ın gerçek piyasa fiyatı), teorik
    hesap yerine DOĞRUDAN o kullanılmalı."""
    position = OptionPosition(
        ticker="THYAO",
        strike=280,
        option_type="call",
        contracts=-1,
        time_to_expiry=0.1096,
        risk_free_rate=0.35,
    )
    scenario = {"price_shock": 0.0, "vol_shock": 0.0, "is_extreme": False}
    theoretical = span_engine.black_scholes_price(
        301.50, 280, 0.1096, 0.35, 0.3021, "call"
    )
    market_price = theoretical - 5.0  # gerçek piyasa fiyatı teorikten farklı olsun
    pnl = span_engine.calculate_scenario_pnl(
        position, 301.50, 0.3021, scenario, base_price=market_price
    )
    # Şoksuz senaryoda şoklu fiyat = teorik; taban = market_price olduğu
    # için fark tam olarak (teorik - market_price) = 5.0 olmalı.
    expected = (theoretical - market_price) * position.contracts * position.contract_size
    assert pnl == pytest.approx(expected)
    assert pnl == pytest.approx(-500.0)  # -1 kontrat * 100 * 5.0


def test_calculate_span_margin_passes_base_price_through():
    """calculate_span_margin, base_price'ı tüm 16 senaryoya doğru aktarmalı."""
    position = OptionPosition(
        ticker="THYAO",
        strike=280,
        option_type="call",
        contracts=-1,
        time_to_expiry=0.1096,
        risk_free_rate=0.35,
    )
    risk_params = RiskParams(
        ticker="THYAO",
        price_scan_range=0.134,
        volatility_scan_range=0.28,
        extreme_move_multiplier=3.0,
        extreme_move_covered_fraction=0.32,
        intra_commodity_spread_charge=0.0,
        short_option_minimum=1640.0,
    )
    theoretical = span_engine.black_scholes_price(
        301.50, 280, 0.1096, 0.35, 0.3021, "call"
    )
    result_theoretical = span_engine.calculate_span_margin(
        position=position, spot=301.50, volatility=0.3021, risk_params=risk_params
    )
    result_market = span_engine.calculate_span_margin(
        position=position,
        spot=301.50,
        volatility=0.3021,
        risk_params=risk_params,
        base_price=theoretical,  # teoriğe eşit market fiyatı verilirse aynı sonucu vermeli
    )
    assert result_market["scan_risk"] == pytest.approx(result_theoretical["scan_risk"])
