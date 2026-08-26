"""SPAN Hesaplama Motoru.

Black-Scholes opsiyon fiyatlama, 16 risk senaryosu üretimi, Scanning
Risk ve Short Option Minimum hesaplamaları ile final SPAN başlangıç
teminatı formülünü içerir:

    Total Initial Margin = max(
        SOM,
        Scan Risk + Intra-Commodity Spread Charge + Delivery Risk
            - Inter-Commodity Spread Credit
    )
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from scipy.stats import norm

from .risk_params import RiskParams

OptionType = Literal["call", "put"]

# Kayan nokta hatalarından kaynaklanan negatif/sıfır volatiliteyi önlemek
# için kullanılan alt sınır.
_MIN_VOLATILITY = 1e-6

# Anormal derecede büyük bir price_shock, spot'u teorik olarak negatife
# taşımaya çalışırsa sabitlenecek alt sınır (spot her zaman pozitiftir).
_MIN_SPOT = 1e-6

# SPAN'in standart 16 senaryosunda fiyat şokları PSR'nin şu kesirleri
# olarak uygulanır: fiyat değişmez, +/-1/3, +/-2/3, +/-3/3 (tam PSR).
_PRICE_SHOCK_FRACTIONS = (0.0, 1 / 3, 2 / 3, 1.0)


@dataclass
class OptionPosition:
    """Bir opsiyon pozisyonu.

    Attributes:
        ticker: Dayanak hisse sembolü.
        strike: Kullanım fiyatı.
        option_type: "call" veya "put".
        contracts: Kontrat sayısı (kısa pozisyon için negatif olabilir).
        time_to_expiry: Vadeye kalan süre (yıl cinsinden, örn. 30/365).
        risk_free_rate: Risksiz faiz oranı (örn. 0.45 -> %45).
        contract_size: Kontrat başına dayanak varlık miktarı (VİOP pay
            opsiyonlarında standart 100 pay/kontrat). black_scholes_price
            birim (pay başına) fiyat döndürür; P&L'e çevrilirken bu
            çarpanla kontrat büyüklüğüne ölçeklenir. Endeks/döviz
            opsiyonları gibi farklı kontrat büyüklüğüne sahip enstrümanlar
            için override edilebilir.
    """

    ticker: str
    strike: float
    option_type: OptionType
    contracts: int
    time_to_expiry: float
    risk_free_rate: float
    contract_size: int = 100


@dataclass
class ScenarioResult:
    """Tek bir risk senaryosunun sonucu.

    Attributes:
        price_shock: Uygulanan fiyat şoku (ör. +1/3 PSR gibi PSR'nin katı).
        vol_shock: Uygulanan volatilite şoku (VSR'nin katı).
        is_extreme: Bu senaryo extreme move senaryosu mu.
        pnl: Bu senaryo altında pozisyonun kâr/zararı.
    """

    price_shock: float
    vol_shock: float
    is_extreme: bool
    pnl: float


def black_scholes_price(
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float,
    volatility: float,
    option_type: OptionType,
) -> float:
    """Black-Scholes call/put fiyatı hesaplar.

    Args:
        spot: Dayanak varlık güncel fiyatı (S).
        strike: Kullanım fiyatı (K).
        time_to_expiry: Vadeye kalan süre, yıl cinsinden (T).
        risk_free_rate: Risksiz faiz oranı (r).
        volatility: Yıllıklaştırılmış volatilite (sigma).
        option_type: "call" veya "put".

    Returns:
        Teorik opsiyon fiyatı.
    """
    # Vade dolmuşsa (veya geçmişse) fiyat, içsel değere eşittir.
    if time_to_expiry <= 0:
        if option_type == "call":
            return max(spot - strike, 0.0)
        return max(strike - spot, 0.0)

    volatility = max(volatility, _MIN_VOLATILITY)

    d1 = (
        math.log(spot / strike)
        + (risk_free_rate + 0.5 * volatility**2) * time_to_expiry
    ) / (volatility * math.sqrt(time_to_expiry))
    d2 = d1 - volatility * math.sqrt(time_to_expiry)

    discounted_strike = strike * math.exp(-risk_free_rate * time_to_expiry)

    if option_type == "call":
        return spot * norm.cdf(d1) - discounted_strike * norm.cdf(d2)
    return discounted_strike * norm.cdf(-d2) - spot * norm.cdf(-d1)


def generate_risk_scenarios(
    spot: float,
    volatility: float,
    price_scan_range: float,
    volatility_scan_range: float,
    extreme_move_multiplier: float,
    extreme_move_covered_fraction: float,
) -> list[dict]:
    """SPAN'in standart 16 risk senaryosunu üretir.

    Senaryolar tipik olarak: fiyat için {0, +1/3, +2/3, +3/3 PSR} x
    {up, down} ve volatilite için {+VSR, -VSR} kombinasyonlarından
    (bu 14'ü oluşturur) artı 2 extreme move senaryosundan (fiyat
    +/- extreme_move_multiplier * PSR, sadece
    extreme_move_covered_fraction kadarı kapsanır) oluşur.

    ÖNEMLİ -- volatilite şoku ÇARPIMSALDIR: yeni_vol = vol * (1 +/- VSR),
    (vol +/- VSR gibi TOPLAMSAL değil). Bu, referans bir Excel
    hesaplayıcısıyla (THYAO_SPAN_Hesaplama) hücre hücre doğrulanmıştır --
    toplamsal şok kullanmak, özellikle yüksek volatiliteli hisselerde
    Scan Risk'i ciddi ölçüde yanlış hesaplatıyordu.

    Extreme move senaryolarında vol_shock=0'dır (volatilite şoklanmaz,
    sadece fiyat şoklanır). Bu, Takasbank'ın kendi resmi PC-SPAN üretim
    dosyasındaki (spanFile/pointDef/scanPointDef, point 15-16) global
    tanımla doğrulanmıştır: bu noktalarda volScanDef.mult=0.0'dır.
    (Not: bir kullanıcı Excel'i daha önce "extreme'de her zaman vol
    yukarı" varsayımıyla test edilmişti; Takasbank'ın kendi üretim
    verisiyle karşılaştırıldığında bu varsayımın yanlış olduğu görüldü
    ve resmi kaynak lehine geri alındı.)

    Args:
        spot: Dayanak varlık güncel fiyatı.
        volatility: Güncel (historical) volatilite.
        price_scan_range: PSR.
        volatility_scan_range: VSR.
        extreme_move_multiplier: Extreme move çarpanı.
        extreme_move_covered_fraction: Extreme move'un kapsanan kısmı.

    Returns:
        16 elemanlı liste; her eleman şu anahtarları içeren dict:
        - "price_shock": spot'a göre bağıl fiyat şoku (ör. +0.05 -> spot*1.05)
        - "vol_shock": volatiliteye ÇARPIMSAL uygulanacak bağıl şok
          (ör. +0.28 -> vol*1.28, TOPLAMSAL değil)
        - "is_extreme": extreme move senaryosu mu
        - "covered_fraction": bu senaryonun P&L'ine uygulanacak kapsama
          oranı (normal senaryolarda 1.0, extreme senaryolarda
          extreme_move_covered_fraction)
    """
    scenarios: list[dict] = []

    for fraction in _PRICE_SHOCK_FRACTIONS:
        directions = (0,) if fraction == 0 else (1, -1)
        for direction in directions:
            price_shock = direction * fraction * price_scan_range
            for vol_direction in (1, -1):
                scenarios.append(
                    {
                        "price_shock": price_shock,
                        "vol_shock": vol_direction * volatility_scan_range,
                        "is_extreme": False,
                        "covered_fraction": 1.0,
                    }
                )

    for direction in (1, -1):
        scenarios.append(
            {
                "price_shock": direction * extreme_move_multiplier * price_scan_range,
                "vol_shock": 0.0,  # Takasbank PC-SPAN: extreme'de vol şoklanmaz
                "is_extreme": True,
                "covered_fraction": extreme_move_covered_fraction,
            }
        )

    return scenarios


def apply_price_shock(spot: float, price_shock: float) -> float:
    """Bir price_shock'u spot'a uygular, sonucu küçük bir pozitif tabana sabitler.

    Spot fiyat teorik olarak negatif olamaz; anormal derecede büyük bir
    price_shock (ör. yanlış yapılandırılmış bir extreme move çarpanı)
    verilse bile bu taban aşılmaz.
    """
    return max(spot * (1 + price_shock), _MIN_SPOT)


def apply_vol_shock(volatility: float, vol_shock: float) -> float:
    """Bir vol_shock'u volatiliteye ÇARPIMSAL uygular, tabana sabitler.

    yeni_vol = vol * (1 + vol_shock) -- TOPLAMSAL değil (vol + vol_shock
    DEĞİL); bkz. generate_risk_scenarios docstring'i.
    """
    return max(volatility * (1 + vol_shock), _MIN_VOLATILITY)


def calculate_scenario_pnl(
    position: OptionPosition,
    spot: float,
    volatility: float,
    scenario: dict,
    base_price: float | None = None,
) -> float:
    """Tek bir senaryo altında pozisyonun kâr/zararını hesaplar.

    Senaryodaki price_shock ve vol_shock'u spot/volatility'ye uygulayıp
    black_scholes_price ile şoklu fiyatı bulur, TABAN fiyatla farkını
    kontrat sayısı VE kontrat büyüklüğü (position.contract_size) ile
    çarpar. black_scholes_price birim (pay başına) fiyat döndürdüğü
    için, kontrat büyüklüğü çarpanı olmadan P&L 100 kat (VİOP'ta
    standart 100 pay/kontrat için) küçük çıkar -- bu da Scan Risk'i
    SOM ile aynı birime getirmeden (ikisi de "kontrat başına toplam TL"
    olmalı) yanlış karşılaştırmaya yol açar.

    Args:
        position: Opsiyon pozisyonu.
        spot: Güncel dayanak fiyatı.
        volatility: Güncel volatilite.
        scenario: generate_risk_scenarios çıktısındaki tek bir senaryo.
        base_price: "Fark" hesabının taban fiyatı. Verilmezse (None),
            spot/volatility ile TEORİK Black-Scholes fiyatı hesaplanıp
            taban olarak kullanılır (eski davranış, Excel referansıyla
            doğrulanan testler bunu kullanır). Verilirse (ör. Takasbank
            XML'in opt/p alanındaki GERÇEK piyasa/uzlaşma fiyatı), teorik
            hesap yerine DOĞRUDAN o değer taban olarak kullanılır -- bu,
            sonuçları Takasbank'ın kendi PC-SPAN Risk Array ekranına daha
            yakınlaştırır çünkü hem girdiler hem taban fiyat aynı
            kaynaktan (Takasbank) gelmiş olur.

    Returns:
        Senaryo altındaki P&L, kontrat başına toplam TL cinsinden
        (kısa pozisyonlarda fiyat artışı negatif P&L üretir).
    """
    if base_price is None:
        base_price = black_scholes_price(
            spot,
            position.strike,
            position.time_to_expiry,
            position.risk_free_rate,
            volatility,
            position.option_type,
        )
    shocked_spot = apply_price_shock(spot, scenario["price_shock"])
    shocked_volatility = apply_vol_shock(volatility, scenario["vol_shock"])
    shocked_price = black_scholes_price(
        shocked_spot,
        position.strike,
        position.time_to_expiry,
        position.risk_free_rate,
        shocked_volatility,
        position.option_type,
    )

    pnl = (shocked_price - base_price) * position.contracts * position.contract_size
    return pnl * scenario.get("covered_fraction", 1.0)


def scanning_risk(scenario_pnls: list[float]) -> float:
    """16 senaryo arasından en kötü (en büyük zarar) senaryoyu seçer.

    Args:
        scenario_pnls: Her senaryo için hesaplanmış P&L listesi.

    Returns:
        Scanning Risk (en kötü senaryodaki zarar, pozitif sayı olarak).
        Hiçbir senaryo zarar üretmiyorsa (tüm P&L'ler pozitifse) 0 döner.
    """
    worst_case_pnl = min(scenario_pnls)
    return max(-worst_case_pnl, 0.0)


def short_option_minimum(
    position: OptionPosition, risk_params: RiskParams
) -> float:
    """Short Option Minimum (SOM) tutarını hesaplar.

    Args:
        position: Opsiyon pozisyonu (kısa pozisyon varsayımıyla).
        risk_params: İlgili hissenin SPAN risk parametreleri.

    Returns:
        SOM tutarı. Sadece net kısa (short) opsiyon pozisyonları için
        pozitif bir değer döner -- uzun (long) pozisyonlarda 0'dır,
        çünkü SOM riskin sınırsız olabileceği yazıcı (writer)
        pozisyonlarına karşı bir taban teminat oluşturur.
    """
    short_contracts = max(0, -position.contracts)
    return short_contracts * risk_params.short_option_minimum


def calculate_span_margin(
    position: OptionPosition,
    spot: float,
    volatility: float,
    risk_params: RiskParams,
    intra_commodity_spread_charge: float = 0.0,
    delivery_risk: float = 0.0,
    inter_commodity_spread_credit: float = 0.0,
    base_price: float | None = None,
) -> dict:
    """Final SPAN başlangıç teminatını hesaplar.

    Total Initial Margin = max(
        SOM,
        Scan Risk + Intra-Commodity Spread Charge + Delivery Risk
            - Inter-Commodity Spread Credit
    )

    ÖNEMLİ: intra_commodity_spread_charge risk_params'tan OTOMATİK
    alınmaz -- `position`, tek bacaklı/tek vadeli bir pozisyonu temsil
    eder ve tek bir pozisyonda tanım gereği "spread" (aynı dayanak
    varlıkta birden fazla vade) olamaz. Bu ücret sadece çağıran taraf,
    portföyde gerçekten bir vadeler arası spread pozisyonu olduğunu
    bilip bunu açıkça geçtiğinde uygulanmalıdır (spread birimi sayısı ×
    risk_params.intra_commodity_spread_charge); aksi halde varsayılan
    olan 0 kullanılmalıdır.

    Args:
        position: Opsiyon pozisyonu.
        spot: Güncel dayanak fiyatı.
        volatility: Güncel historical volatility.
        risk_params: İlgili hissenin SPAN risk parametreleri (SOM ve
            scanning risk senaryoları için kullanılır).
        intra_commodity_spread_charge: Portföyde gerçek bir vadeler
            arası spread pozisyonu varsa uygulanacak ücret (varsayılan
            0 -- tek bacaklı/tek vadeli pozisyonlarda doğru değer budur).
        delivery_risk: Teslimat riski bileşeni (varsayılan 0).
        inter_commodity_spread_credit: Emtialar arası spread kredisi
            (varsayılan 0, tek pozisyon için genelde 0).
        base_price: 16 senaryonun "Fark" hesabında kullanılacak taban
            fiyat. Verilmezse (None) her senaryoda teorik Black-Scholes
            hesaplanır (bkz. calculate_scenario_pnl). Verilirse (ör.
            Takasbank XML'in opt/p'si), o sabit değer taban olarak
            kullanılır.

    Returns:
        Hesabın ara adımlarını da içeren dict:
        {"scan_risk", "intra_commodity_spread_charge", "delivery_risk",
        "inter_commodity_spread_credit", "short_option_minimum",
        "total_initial_margin"}.
    """
    scenarios = generate_risk_scenarios(
        spot=spot,
        volatility=volatility,
        price_scan_range=risk_params.price_scan_range,
        volatility_scan_range=risk_params.volatility_scan_range,
        extreme_move_multiplier=risk_params.extreme_move_multiplier,
        extreme_move_covered_fraction=risk_params.extreme_move_covered_fraction,
    )
    scenario_pnls = [
        calculate_scenario_pnl(position, spot, volatility, scenario, base_price)
        for scenario in scenarios
    ]

    scan_risk = scanning_risk(scenario_pnls)
    som = short_option_minimum(position, risk_params)
    scan_component = (
        scan_risk
        + intra_commodity_spread_charge
        + delivery_risk
        - inter_commodity_spread_credit
    )

    return {
        "scan_risk": scan_risk,
        "intra_commodity_spread_charge": intra_commodity_spread_charge,
        "delivery_risk": delivery_risk,
        "inter_commodity_spread_credit": inter_commodity_spread_credit,
        "short_option_minimum": som,
        "total_initial_margin": max(som, scan_component),
    }
