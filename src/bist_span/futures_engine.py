"""Vadeli işlem (futures) sözleşmeleri için SPAN başlangıç teminatı motoru.

BAĞIMSIZ KATMAN: span_engine.py'ye HİÇBİR ŞEKİLDE dokunmadan yazılmıştır
-- sadece o modülün PUBLIC, DEĞİŞTİRİLMEMİŞ 16-senaryo/scanning-risk
fonksiyonlarını (generate_risk_scenarios, apply_price_shock,
scanning_risk) salt-okunur olarak import edip yeniden kullanır. Amaç:
"BIST Vadeli İşlem" özelliği istenirse opsiyon özelliğini hiç
etkilemeden tek seferde silinebilsin (bkz. futures_xml.py docstring'i).

Neden opsiyonlardan BASİT: Vadeli işlem DOĞRUSAL bir enstrümandır --
delta her zaman 1'dir, Black-Scholes/volatilite/strike gibi kavramlar
YOKTUR. Şoklu fiyat = fiyat × (1 + price_shock); P&L, bu farkın kontrat
sayısı ve kontrat çarpanıyla çarpılmasından ibarettir. Volatilite
şokunun (VSR) etkisi yoktur (Takasbank'ın kendi verisinde de vadeli
işlemler için volScan HER ZAMAN 0.0 -- bkz. futures_xml.py), bu yüzden
generate_risk_scenarios'a volatility_scan_range=0.0 verilir; bu da
vol-yukarı/vol-aşağı çift senaryolarının (1-2, 3-4, ...) otomatik
olarak birbirine eşit P&L üretmesini sağlar -- Takasbank'ın kendi
<fut><ra> risk dizisinde de GÖZLENEN davranış budur.

Formül farkı (Madde 33-38, bkz. proje sohbet geçmişi): Short Option
Minimum (SOM), Net Opsiyon Değeri (NOV) ve Opsiyon Prim Değeri
resmi metinde AÇIKÇA "Kısa OPSİYON Pozisyonu" diye tanımlanır --
vadeli işlemlere uygulanmaz. Bu yüzden vadeli işlem başlangıç
teminatı SADECE:
    Başlangıç Teminatı = Tarama Riski + Vadeler Arası Yayılma Riski
                          - Ürünler Arası Yayılma İndirimi
(SOM/NOV/Opsiyon Prim Değeri YOK.)
"""

from __future__ import annotations

from dataclasses import dataclass

from bist_span.span_engine import apply_price_shock, generate_risk_scenarios, scanning_risk


@dataclass
class FuturesPosition:
    """Bir vadeli işlem pozisyonu.

    Attributes:
        ticker: BIST sembolü.
        contracts: Kontrat sayısı (kısa pozisyon için negatif).
        contract_size: Kontrat başına dayanak varlık miktarı (ör. 100).
    """

    ticker: str
    contracts: int
    contract_size: float = 100.0


def calculate_futures_scenario_pnl(position: FuturesPosition, price: float, scenario: dict) -> float:
    """Bir vadeli işlem sözleşmesinin tek bir SPAN senaryosundaki P&L'ini hesaplar.

    Opsiyonlardan farklı olarak Black-Scholes YOK -- şoklu fiyat doğrudan
    price × (1 + price_shock)'tır (delta=1 varsayımı, gerçek Takasbank
    verisiyle doğrulanmıştır: <fut><d> her zaman 1.0).
    """
    shocked_price = apply_price_shock(price, scenario["price_shock"])
    pnl = (shocked_price - price) * position.contracts * position.contract_size
    return pnl * scenario.get("covered_fraction", 1.0)


def calculate_futures_margin(
    position: FuturesPosition,
    price: float,
    price_scan_range: float,
    extreme_move_multiplier: float,
    extreme_move_covered_fraction: float,
    intra_commodity_spread_charge: float = 0.0,
    inter_commodity_spread_credit: float = 0.0,
) -> dict:
    """Final vadeli işlem SPAN başlangıç teminatını hesaplar.

    Başlangıç Teminatı = Tarama Riski + Vadeler Arası Yayılma Riski
                          - Ürünler Arası Yayılma İndirimi
    (SOM/NOV/Opsiyon Prim Değeri YOK -- bkz. modül docstring'i.)

    Args:
        position: Vadeli işlem pozisyonu.
        price: Sözleşmenin güncel fiyatı (Takasbank XML'in <fut><p>'si).
        price_scan_range: PSR, ondalık.
        extreme_move_multiplier, extreme_move_covered_fraction: Bu
            sözleşmenin risk grubuna ait Extreme Move parametreleri.
        intra_commodity_spread_charge, inter_commodity_spread_credit:
            Portföyde gerçek bir vadeler arası spread pozisyonu/emtialar
            arası kredi varsa (varsayılan 0 -- tek bacaklı pozisyon).

    Returns:
        {"scan_risk", "intra_commodity_spread_charge",
        "inter_commodity_spread_credit", "total_initial_margin"}.
    """
    scenarios = generate_risk_scenarios(
        spot=price,
        volatility=0.0,
        price_scan_range=price_scan_range,
        volatility_scan_range=0.0,  # vadeli işlemde volatilite riski yok
        extreme_move_multiplier=extreme_move_multiplier,
        extreme_move_covered_fraction=extreme_move_covered_fraction,
    )
    scenario_pnls = [calculate_futures_scenario_pnl(position, price, s) for s in scenarios]
    scan_risk = scanning_risk(scenario_pnls)

    total_initial_margin = (
        scan_risk + intra_commodity_spread_charge - inter_commodity_spread_credit
    )

    return {
        "scan_risk": scan_risk,
        "intra_commodity_spread_charge": intra_commodity_spread_charge,
        "inter_commodity_spread_credit": inter_commodity_spread_credit,
        "total_initial_margin": total_initial_margin,
    }
