"""Kullanıcı Arayüzü.

Basit bir CLI ve Streamlit dashboard: kullanıcı hisse adı, strike ve
vade girer; sistem güncel fiyat/volatiliteyi (yfinance) ve Takasbank
risk parametrelerini (PDF) otomatik çeker, 1 kısa kontrat üzerinden hem
call hem put için minimum SPAN teminatını hesaplar. Otomatik çekilen
her bileşen (spot, volatilite, PSR, VSR, Extreme Move Multiplier,
Extreme Move Covered Fraction, Short Option Minimum) tek tek manuel
override edilebilir. Intra-Commodity Spread Charge farklıdır: tek
bacaklı/tek vadeli bir pozisyonda tanım gereği spread olamayacağı için
varsayılan olarak 0 uygulanır (Takasbank'ın o hisse için yayınladığı
referans değer sadece bilgi amaçlı gösterilir); sadece gerçek bir
vadeler arası spread pozisyonun varsa açıkça bir tutar girip
uygulatabilirsin.

CLI kullanımı:
    # Hem call hem put (varsayılan, --option-type verilmezse):
    python -m bist_span.main --ticker AKBNK --strike 65 --expiry 2026-09-18

    # Tek taraf + manuel override örneği:
    python -m bist_span.main --ticker AKBNK --strike 65 --option-type call \\
        --expiry 2026-09-18 --volatility-scan-range 0.40

Streamlit kullanımı:
    streamlit run src/bist_span/main.py
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import pandas as pd

# `streamlit run src/bist_span/main.py` bu dosyayı bağımsız bir script
# olarak çalıştırır (paket içi bir modül olarak değil), bu yüzden göreli
# importlar ("from . import ...") ImportError verir. src/ dizinini
# sys.path'e ekleyip mutlak import kullanmak, dosyayı hem
# `python -m bist_span.main` hem `streamlit run src/bist_span/main.py`
# hem de pytest ile çalıştırılabilir hale getirir.
_SRC_DIR = str(Path(__file__).resolve().parent.parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from bist_span import data_fetch
from bist_span.risk_params import RiskParams, RiskParamsStore, parse_takasbank_span_file
from bist_span.span_engine import (
    OptionPosition,
    OptionType,
    apply_price_shock,
    apply_vol_shock,
    black_scholes_price,
    calculate_scenario_pnl,
    calculate_span_margin,
    generate_risk_scenarios,
)

# Takasbank'ın per-ticker PSR/VSR listesinde geçen ama gerçek bir hisse
# olmayan (döviz/endeks vadeli işlem) semboller -- hisse seçim listesinden
# ve senaryo tablosu başlıklarından hariç tutulur.
_NON_EQUITY_TICKERS = {"USDTRY", "EURTRY", "XU030", "X10XB", "XLBNK", "XSD25"}

# Projedeki en güncel Takasbank risk parametre dosyası. Takasbank yeni bir
# "Risk Parametrelerinin Güncellenmesi" mektubu yayınladığında bu dosyayı
# güncelleyip --risk-params-file ile (CLI) veya arayüzdeki dosya yolu
# alanından (Streamlit) farklı bir dosya vererek override edebilirsin.
DEFAULT_RISK_PARAMS_FILE = (
    Path(__file__).resolve().parent.parent.parent
    / "tests"
    / "fixtures"
    / "takasbank_span_sample_2.pdf"
)


def _normalize_ticker(ticker: str) -> str:
    """BIST sembolünü yfinance formatına ('.IS' sonekli) çevirir."""
    ticker = ticker.upper().strip()
    return ticker if ticker.endswith(".IS") else f"{ticker}.IS"


@dataclass
class SpanCalculationInput:
    """CLI/Streamlit'ten toplanan ham kullanıcı girdisi.

    Attributes:
        ticker: BIST sembolü (".IS" sonekli veya soneksiz olabilir).
        strike: Kullanım fiyatı.
        option_type: "call" veya "put".
        contracts: Kontrat sayısı (kısa pozisyon için negatif, örn. -1).
        expiry: Vade tarihi.
        risk_params_file: Takasbank PDF'i (veya daha önce RiskParamsStore.save
            ile kaydedilmiş bir JSON cache) yolu. Varsayılan:
            DEFAULT_RISK_PARAMS_FILE.
        risk_free_rate: Risksiz faiz oranı (varsayılan 0.45).
        period: Historical volatility hesabı için geçmiş veri periyodu.
        spot_override: Verilirse yfinance yerine bu spot fiyatı kullanılır.
        volatility_override: Verilirse hesaplanan historical volatility
            yerine bu değer kullanılır.
        time_to_expiry_override: Verilirse (expiry - bugün).gün/365'ten
            hesaplanan T yerine bu değer (yıl cinsinden) doğrudan kullanılır.
            Takvim tarihi her zaman TAM gün sayısı verir (ör. 37/365 =
            0.10136986...); bir referans hesaplayıcı (Excel vb.) T'yi
            doğrudan ondalık olarak giriyorsa (ör. 0.1014), birebir
            karşılaştırma için bu alanı kullan.
        price_scan_range_override, volatility_scan_range_override,
        extreme_move_multiplier_override, extreme_move_covered_fraction_override,
        short_option_minimum_override:
            Verilirse PDF'ten parse edilen ilgili risk parametresi yerine
            bu değer kullanılır.
        intra_commodity_spread_charge_override: ÖNEMLİ -- bu, Takasbank'ın
            PDF'teki referans değerini OVERRIDE ETMEZ (o değer risk_params'ta
            olduğu gibi kalır, sadece bilgi amaçlıdır). Bunun yerine, bu
            pozisyon için SPAN formülüne fiilen uygulanacak spread ücretidir
            ve varsayılanı 0'dır -- çünkü `position` tek bacaklı/tek vadeli
            bir pozisyonu temsil eder ve tek bir pozisyonda tanım gereği
            "vadeler arası spread" olamaz. Sadece gerçekten aynı dayanak
            varlıkta birden fazla vadeli bir spread pozisyonun olduğunu
            biliyorsan, o zaman ilgili ücreti buraya açıkça gir.
    """

    ticker: str
    strike: float
    option_type: OptionType
    contracts: int
    expiry: date
    risk_params_file: Path = DEFAULT_RISK_PARAMS_FILE
    risk_free_rate: float = 0.45
    period: str = "1y"
    spot_override: float | None = None
    volatility_override: float | None = None
    time_to_expiry_override: float | None = None
    price_scan_range_override: float | None = None
    volatility_scan_range_override: float | None = None
    extreme_move_multiplier_override: float | None = None
    extreme_move_covered_fraction_override: float | None = None
    intra_commodity_spread_charge_override: float | None = None
    short_option_minimum_override: float | None = None


# SpanCalculationInput'taki override alanı adı -> RiskParams'taki karşılık.
# intra_commodity_spread_charge_override BURADA YOK -- o alan risk_params'ı
# değil, doğrudan SPAN formülüne uygulanan ücreti kontrol eder (bkz.
# SpanCalculationInput docstring'i ve compute_span_result).
_RISK_PARAM_OVERRIDE_FIELDS = {
    "price_scan_range_override": "price_scan_range",
    "volatility_scan_range_override": "volatility_scan_range",
    "extreme_move_multiplier_override": "extreme_move_multiplier",
    "extreme_move_covered_fraction_override": "extreme_move_covered_fraction",
    "short_option_minimum_override": "short_option_minimum",
}


def _apply_risk_params_overrides(
    risk_params: RiskParams, inputs: SpanCalculationInput
) -> RiskParams:
    """Kullanıcı tarafından girilen override'ları risk_params'a uygular.

    Sadece None olmayan override alanları uygulanır; geri kalanı
    PDF'ten parse edilen otomatik değerde kalır.
    """
    active_overrides = {
        rp_field: getattr(inputs, input_field)
        for input_field, rp_field in _RISK_PARAM_OVERRIDE_FIELDS.items()
        if getattr(inputs, input_field) is not None
    }
    if not active_overrides:
        return risk_params
    return replace(risk_params, **active_overrides)


def _load_risk_params_store(risk_params_file: Path) -> RiskParamsStore:
    """Dosya uzantısına göre PDF'i parse eder ya da JSON cache'i yükler."""
    store = RiskParamsStore()
    if risk_params_file.suffix.lower() == ".json":
        store.load(risk_params_file)
    else:
        store.load_from_dataframe(parse_takasbank_span_file(risk_params_file))
    return store


def available_tickers(risk_params_file: Path = DEFAULT_RISK_PARAMS_FILE) -> list[str]:
    """Verilen risk parametre dosyasında tam opsiyon verisi olan hisseleri döner.

    Yani: bu listedeki her ticker için call/put SPAN hesabı yapılabilir.
    Döviz/endeks vadeli işlemleri (_NON_EQUITY_TICKERS) hariç tutulur.

    Not: Bu, "resmi BIST30 endeks listesi" DEĞİL -- Takasbank'ın bu
    belgede opsiyon risk parametresi yayınladığı hisselerin listesidir
    (çoğu BIST30 üyesiyle örtüşür, ama endeks kompozisyonu zamanla
    değişebileceği için garantili bire bir eşleşme değildir).
    """
    store = _load_risk_params_store(risk_params_file)
    return [t for t in store.tickers() if t not in _NON_EQUITY_TICKERS]


_FRACTION_LABELS = ((0.0, "sabit"), (1 / 3, "1/3 PSR"), (2 / 3, "2/3 PSR"), (1.0, "tam PSR"))


def _scenario_description(
    price_multiplier: float, vol_direction: float, is_extreme: bool, emm: float, emcf: float
) -> str:
    """Kullanıcının Excel'indeki (THYAO_SPAN_Hesaplama) "Açıklama" kolonuyla
    birebir aynı formatta bir senaryo açıklaması üretir."""
    vol_word = "yukarı" if vol_direction > 0 else "aşağı"
    if is_extreme:
        direction_word = "yukarı" if price_multiplier > 0 else "aşağı"
        emm_str = f"{emm:g}"
        emcf_str = f"{emcf * 100:g}"
        return f"Aşırı hareket {direction_word} ({emm_str}×PSR, %{emcf_str})"

    for fraction, label in _FRACTION_LABELS:
        if math.isclose(abs(price_multiplier), fraction, abs_tol=1e-9):
            if fraction == 0.0:
                return f"Fiyat sabit / Vol {vol_word}"
            sign = "+" if price_multiplier > 0 else "-"
            return f"Fiyat {sign}{label} / Vol {vol_word}"
    return f"Fiyat {price_multiplier:+.4f}×PSR / Vol {vol_word}"


def _build_scenario_table(
    position: OptionPosition, spot: float, volatility: float, risk_params: RiskParams
) -> pd.DataFrame:
    """SPAN'in 16 risk senaryosunu, kullanıcının Excel'iyle (THYAO_SPAN_Hesaplama)
    BİREBİR AYNI sütun yapısında bir tabloya döker: Sen., Açıklama, Fiyat
    Çarpanı, Vol Yönü, S_yeni, IV_yeni, {Call|Put} Fiyatı, Fark, Kısa K/Z (TL).

    Scanning Risk (bkz. span_engine.scanning_risk), bu 16 K/Z'nin en
    kötüsünden (en büyük zarardan) hesaplanır -- Excel'deki "Aktif
    Senaryo #" değeri df.attrs["worst_scenario_no"]'da, o anki (şoksuz)
    opsiyon fiyatı df.attrs["current_price"]'ta saklanır.
    """
    scenarios = generate_risk_scenarios(
        spot=spot,
        volatility=volatility,
        price_scan_range=risk_params.price_scan_range,
        volatility_scan_range=risk_params.volatility_scan_range,
        extreme_move_multiplier=risk_params.extreme_move_multiplier,
        extreme_move_covered_fraction=risk_params.extreme_move_covered_fraction,
    )
    current_price = black_scholes_price(
        spot,
        position.strike,
        position.time_to_expiry,
        position.risk_free_rate,
        volatility,
        position.option_type,
    )
    price_column = "Call Fiyatı" if position.option_type == "call" else "Put Fiyatı"

    rows = []
    for i, scenario in enumerate(scenarios, start=1):
        pnl = calculate_scenario_pnl(position, spot, volatility, scenario)
        # apply_price_shock/apply_vol_shock -- calculate_scenario_pnl'in
        # kullandığı AYNI fonksiyonlar (span_engine'den paylaşılan), böylece
        # burada gösterilen S_yeni/IV_yeni gerçek P&L hesabıyla her zaman
        # birebir tutarlı kalır ve negatif spot gibi uç durumlar da aynı
        # şekilde korunur.
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
        price_multiplier = (
            scenario["price_shock"] / risk_params.price_scan_range
            if risk_params.price_scan_range
            else 0.0
        )
        vol_direction = (
            scenario["vol_shock"] / risk_params.volatility_scan_range
            if risk_params.volatility_scan_range
            else 0.0
        )
        rows.append(
            {
                "Sen.": i,
                "Açıklama": _scenario_description(
                    price_multiplier,
                    vol_direction,
                    scenario["is_extreme"],
                    risk_params.extreme_move_multiplier,
                    risk_params.extreme_move_covered_fraction,
                ),
                "Fiyat Çarpanı": round(price_multiplier, 6),
                "Vol Yönü": round(vol_direction, 6),
                "S_yeni": round(shocked_spot, 4),
                "IV_yeni": round(shocked_volatility, 6),
                price_column: round(shocked_price, 6),
                "Fark": round(shocked_price - current_price, 6),
                "Kısa K/Z (TL)": round(pnl, 4),
            }
        )
    df = pd.DataFrame(rows)
    worst_idx = df["Kısa K/Z (TL)"].idxmin()
    df.attrs["worst_scenario_no"] = int(df.loc[worst_idx, "Sen."])
    df.attrs["current_price"] = current_price
    return df


def compute_span_result(inputs: SpanCalculationInput) -> dict:
    """Tüm katmanları birleştirip tam SPAN hesabını yapar.

    Args:
        inputs: Kullanıcı girdisi.

    Returns:
        SPAN sonucunu ve hesaplamada kullanılan ara değerleri
        (spot, volatility, risk_params, time_to_expiry, 16 senaryonun
        P&L dökümü) içeren dict:
        {"spot", "volatility", "risk_params", "time_to_expiry", "span", "scenarios"}.
        "risk_params", override edilmişse override edilmiş halidir.

    Raises:
        ValueError: Vade tarihi bugüne eşit ya da geçmişse.
        KeyError: Risk parametre deposunda bu hisse yoksa.
    """
    ticker = _normalize_ticker(inputs.ticker)

    price_data = data_fetch.get_price_data(ticker, period=inputs.period)
    spot = (
        inputs.spot_override
        if inputs.spot_override is not None
        else price_data.current_price
    )
    volatility = (
        inputs.volatility_override
        if inputs.volatility_override is not None
        else price_data.historical_volatility
    )

    store = _load_risk_params_store(inputs.risk_params_file)
    risk_params = _apply_risk_params_overrides(store.get(ticker), inputs)

    if inputs.time_to_expiry_override is not None:
        time_to_expiry = inputs.time_to_expiry_override
        if time_to_expiry <= 0:
            raise ValueError("time_to_expiry_override sıfırdan büyük olmalı")
    else:
        # Takvim tarihinden hesaplanan T her zaman TAM gün sayısıdır (ör.
        # 37/365); bir referans hesaplayıcının ondalık T'siyle (ör. 0.1014)
        # birebir eşleşmesi beklenmemeli -- gerekiyorsa time_to_expiry_override
        # kullan.
        time_to_expiry = (inputs.expiry - date.today()).days / 365
        if time_to_expiry <= 0:
            raise ValueError(f"Vade tarihi ({inputs.expiry}) bugünden ileride olmalı")

    position = OptionPosition(
        ticker=ticker,
        strike=inputs.strike,
        option_type=inputs.option_type,
        contracts=inputs.contracts,
        time_to_expiry=time_to_expiry,
        risk_free_rate=inputs.risk_free_rate,
    )

    # intra_commodity_spread_charge risk_params'tan OTOMATİK alınmaz --
    # tek bacaklı/tek vadeli bir pozisyonda spread olamayacağı için
    # varsayılan 0'dır. Kullanıcı gerçekten bir spread pozisyonu olduğunu
    # bilip bunu açıkça belirtmişse (intra_commodity_spread_charge_override)
    # o değer uygulanır. Bkz. SpanCalculationInput docstring'i.
    intra_commodity_spread_charge = (
        inputs.intra_commodity_spread_charge_override
        if inputs.intra_commodity_spread_charge_override is not None
        else 0.0
    )

    span_result = calculate_span_margin(
        position=position,
        spot=spot,
        volatility=volatility,
        risk_params=risk_params,
        intra_commodity_spread_charge=intra_commodity_spread_charge,
    )

    scenarios = _build_scenario_table(position, spot, volatility, risk_params)

    return {
        "spot": spot,
        "volatility": volatility,
        "risk_params": risk_params,
        "time_to_expiry": time_to_expiry,
        "span": span_result,
        "scenarios": scenarios,
    }


def compute_call_and_put(inputs: SpanCalculationInput) -> dict[str, dict]:
    """Aynı girdilerle hem call hem put için compute_span_result çalıştırır.

    inputs.option_type yok sayılır; hem "call" hem "put" hesaplanır.

    Returns:
        {"call": <compute_span_result çıktısı>, "put": <compute_span_result çıktısı>}
    """
    return {
        "call": compute_span_result(replace(inputs, option_type="call")),
        "put": compute_span_result(replace(inputs, option_type="put")),
    }


def _sonuclar_rows(span: dict, scenarios: pd.DataFrame) -> dict:
    """Excel'in (THYAO_SPAN_Hesaplama) "SONUÇLAR" bloğuyla birebir aynı
    sıra/etiketlerde satırları döner.

    Not: "Inter-Commodity Spread Credit" gösterilmiyor -- bu uygulama tek
    bir opsiyon pozisyonu için hesap yapıyor; ürün grupları arası spread
    kredisi ancak birden fazla farklı dayanak varlıkta pozisyon olan bir
    portföyde anlamlı olur (bkz. calculate_span_margin, iç hesaplamada
    hâlâ 0 olarak var, sadece ekranda gösterilmiyor).
    """
    toplam_somsuz = (
        span["scan_risk"]
        + span["intra_commodity_spread_charge"]
        + span["delivery_risk"]
        - span["inter_commodity_spread_credit"]
    )
    return {
        "Scanning Risk (TL)": round(span["scan_risk"], 2),
        "+ Intra-Commodity Spread": round(span["intra_commodity_spread_charge"], 2),
        "+ Delivery Risk": round(span["delivery_risk"], 2),
        "Toplam (SOMsuz)": round(toplam_somsuz, 2),
        "Short Option Minimum (SOM)": round(span["short_option_minimum"], 2),
        "TOPLAM BAŞLANGIÇ TEMİNATI": round(span["total_initial_margin"], 2),
        "Aktif Senaryo #": scenarios.attrs["worst_scenario_no"],
    }


def _format_result_table(inputs: SpanCalculationInput, result: dict) -> pd.DataFrame:
    """Tek bir opsiyon tipi için sonucu tek kolonluk bir DataFrame'e çevirir."""
    risk_params = result["risk_params"]
    span = result["span"]
    rows = {
        "Hisse": inputs.ticker,
        "Strike": inputs.strike,
        "Opsiyon Tipi": inputs.option_type,
        "Kontrat Sayısı": inputs.contracts,
        "Vade Tarihi": inputs.expiry.isoformat(),
        "Vadeye Kalan Süre (yıl)": round(result["time_to_expiry"], 4),
        "Güncel Fiyat (Spot)": round(result["spot"], 4),
        "Historical Volatility": round(result["volatility"], 4),
        "Opsiyon Fiyatı (şoksuz)": round(result["scenarios"].attrs["current_price"], 6),
        "--- Risk Parametreleri ---": "",
        "Price Scan Range (PSR)": risk_params.price_scan_range,
        "Volatility Scan Range (VSR)": risk_params.volatility_scan_range,
        "Extreme Move Multiplier": risk_params.extreme_move_multiplier,
        "Extreme Move Covered Fraction": risk_params.extreme_move_covered_fraction,
        "Intra-Commodity Spread Charge (Takasbank referans)": (
            risk_params.intra_commodity_spread_charge
        ),
        "--- SONUÇLAR ---": "",
        **_sonuclar_rows(span, result["scenarios"]),
    }
    return pd.DataFrame(rows.items(), columns=["Alan", "Değer"])


def _format_comparison_table(
    inputs: SpanCalculationInput, call_result: dict, put_result: dict
) -> pd.DataFrame:
    """Call ve put sonuçlarını yan yana tek bir DataFrame'de gösterir."""
    risk_params = call_result["risk_params"]  # call/put aynı risk_params'ı kullanır
    call_span = call_result["span"]
    put_span = put_result["span"]

    rows = [
        ("Hisse", inputs.ticker, inputs.ticker),
        ("Strike", inputs.strike, inputs.strike),
        ("Kontrat Sayısı", inputs.contracts, inputs.contracts),
        ("Vade Tarihi", inputs.expiry.isoformat(), inputs.expiry.isoformat()),
        (
            "Vadeye Kalan Süre (yıl)",
            round(call_result["time_to_expiry"], 4),
            round(put_result["time_to_expiry"], 4),
        ),
        (
            "Güncel Fiyat (Spot)",
            round(call_result["spot"], 4),
            round(put_result["spot"], 4),
        ),
        (
            "Historical Volatility",
            round(call_result["volatility"], 4),
            round(put_result["volatility"], 4),
        ),
        (
            "Opsiyon Fiyatı (şoksuz)",
            round(call_result["scenarios"].attrs["current_price"], 6),
            round(put_result["scenarios"].attrs["current_price"], 6),
        ),
        ("--- Risk Parametreleri ---", "", ""),
        (
            "Price Scan Range (PSR)",
            risk_params.price_scan_range,
            risk_params.price_scan_range,
        ),
        (
            "Volatility Scan Range (VSR)",
            risk_params.volatility_scan_range,
            risk_params.volatility_scan_range,
        ),
        (
            "Extreme Move Multiplier",
            risk_params.extreme_move_multiplier,
            risk_params.extreme_move_multiplier,
        ),
        (
            "Extreme Move Covered Fraction",
            risk_params.extreme_move_covered_fraction,
            risk_params.extreme_move_covered_fraction,
        ),
        (
            "Intra-Commodity Spread Charge (Takasbank referans)",
            risk_params.intra_commodity_spread_charge,
            risk_params.intra_commodity_spread_charge,
        ),
        ("--- SONUÇLAR ---", "", ""),
    ]
    call_sonuclar = _sonuclar_rows(call_span, call_result["scenarios"])
    put_sonuclar = _sonuclar_rows(put_span, put_result["scenarios"])
    for label in call_sonuclar:
        rows.append((label, call_sonuclar[label], put_sonuclar[label]))

    return pd.DataFrame(rows, columns=["Alan", "Call", "Put"])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI argümanlarını parse eder."""
    parser = argparse.ArgumentParser(
        description=(
            "BIST hisse opsiyonları için minimum SPAN teminatı hesaplar. "
            "--option-type verilmezse hem call hem put hesaplanıp karşılaştırılır."
        )
    )
    parser.add_argument(
        "--ticker", required=True, help="BIST sembolü, örn. AKBNK.IS veya AKBNK"
    )
    parser.add_argument("--strike", required=True, type=float, help="Kullanım fiyatı")
    parser.add_argument(
        "--option-type",
        choices=["call", "put"],
        default=None,
        help="Opsiyon tipi (verilmezse hem call hem put hesaplanır)",
    )
    parser.add_argument(
        "--contracts",
        type=int,
        default=-1,
        help="Kontrat sayısı (kısa/yazılmış pozisyon için negatif; varsayılan: -1)",
    )
    parser.add_argument(
        "--expiry",
        required=True,
        type=date.fromisoformat,
        help="Vade tarihi, YYYY-MM-DD formatında",
    )
    parser.add_argument(
        "--risk-params-file",
        type=Path,
        default=DEFAULT_RISK_PARAMS_FILE,
        help=(
            "Takasbank SPAN risk parametre PDF'i (veya JSON cache). "
            f"Varsayılan: {DEFAULT_RISK_PARAMS_FILE}"
        ),
    )
    parser.add_argument(
        "--risk-free-rate",
        type=float,
        default=0.45,
        help="Risksiz faiz oranı, ondalık (varsayılan: 0.45)",
    )
    parser.add_argument(
        "--period",
        default="1y",
        help="Historical volatility için geçmiş veri periyodu (varsayılan: 1y)",
    )
    parser.add_argument(
        "--spot", type=float, default=None, help="Güncel fiyatı manuel override eder"
    )
    parser.add_argument(
        "--volatility",
        type=float,
        default=None,
        help="Historical volatility'yi manuel override eder",
    )
    parser.add_argument(
        "--time-to-expiry",
        type=float,
        default=None,
        dest="time_to_expiry_override",
        help=(
            "Vadeye kalan süreyi (T, yıl) --expiry tarihinden hesaplanan "
            "değer yerine doğrudan verir. --expiry hâlâ zorunludur ama bu "
            "verilince yoksayılır -- bir referans hesaplayıcıyla (Excel "
            "vb.) ondalık T birebir karşılaştırmak için kullan."
        ),
    )
    parser.add_argument(
        "--price-scan-range",
        type=float,
        default=None,
        dest="price_scan_range_override",
        help="PSR'yi manuel override eder (ondalık, örn. 0.157)",
    )
    parser.add_argument(
        "--volatility-scan-range",
        type=float,
        default=None,
        dest="volatility_scan_range_override",
        help="VSR'yi manuel override eder (ondalık, örn. 0.31)",
    )
    parser.add_argument(
        "--extreme-move-multiplier",
        type=float,
        default=None,
        dest="extreme_move_multiplier_override",
        help="Extreme Move Multiplier'ı manuel override eder",
    )
    parser.add_argument(
        "--extreme-move-covered-fraction",
        type=float,
        default=None,
        dest="extreme_move_covered_fraction_override",
        help="Extreme Move Covered Fraction'ı manuel override eder (ondalık)",
    )
    parser.add_argument(
        "--intra-commodity-spread-charge",
        type=float,
        default=None,
        dest="intra_commodity_spread_charge_override",
        help=(
            "Gerçek bir vadeler arası spread pozisyonun varsa uygulanacak "
            "ücret (TL). Verilmezse 0 kullanılır -- tek bacaklı/tek vadeli "
            "bir pozisyonda bu her zaman doğru değerdir."
        ),
    )
    parser.add_argument(
        "--short-option-minimum",
        type=float,
        default=None,
        dest="short_option_minimum_override",
        help="Short Option Minimum'u manuel override eder (TL)",
    )
    parser.add_argument(
        "--hide-scenarios",
        action="store_true",
        help="16 SPAN senaryosu + P&L dökümünü çıktıdan gizler",
    )
    return parser.parse_args(argv)


def run_cli(argv: list[str] | None = None) -> None:
    """CLI akışını çalıştırır: girdi al, hesapla, tabloyu bastır."""
    args = parse_args(argv)
    inputs = SpanCalculationInput(
        ticker=args.ticker,
        strike=args.strike,
        option_type=args.option_type or "call",  # option_type=None ise yok sayılır
        contracts=args.contracts,
        expiry=args.expiry,
        risk_params_file=args.risk_params_file,
        risk_free_rate=args.risk_free_rate,
        period=args.period,
        spot_override=args.spot,
        volatility_override=args.volatility,
        time_to_expiry_override=args.time_to_expiry_override,
        price_scan_range_override=args.price_scan_range_override,
        volatility_scan_range_override=args.volatility_scan_range_override,
        extreme_move_multiplier_override=args.extreme_move_multiplier_override,
        extreme_move_covered_fraction_override=args.extreme_move_covered_fraction_override,
        intra_commodity_spread_charge_override=args.intra_commodity_spread_charge_override,
        short_option_minimum_override=args.short_option_minimum_override,
    )

    if args.option_type is None:
        results = compute_call_and_put(inputs)
        table = _format_comparison_table(inputs, results["call"], results["put"])
        print(table.to_string(index=False))

        if not args.hide_scenarios:
            for label, result in (("CALL", results["call"]), ("PUT", results["put"])):
                print(f"\n--- {label} — 16 SPAN Senaryosu ve P&L ---")
                print(result["scenarios"].to_string(index=False))
    else:
        result = compute_span_result(inputs)
        table = _format_result_table(inputs, result)
        print(table.to_string(index=False))

        if not args.hide_scenarios:
            print("\n--- 16 SPAN Senaryosu ve P&L ---")
            print(result["scenarios"].to_string(index=False))


def _scenario_display_table(df: pd.DataFrame, price_column: str) -> pd.DataFrame:
    """16-senaryo tablosunu, sabit ondalıklı STRING sütunlara çevirip
    st.table (düz HTML tablo) ile göstermeye hazır hale getirir.

    st.dataframe, Streamlit'in canvas tabanlı ("glide-data-grid")
    interaktif ızgara bileşenini kullanır; bu bileşen bazı ekranlarda/
    içerik kombinasyonlarında (özellikle Call'daki büyük -153.24- ve
    Put'taki çok küçük -0.00253- değerler yan yana olunca) bulanık/
    pikselli render edebiliyor -- bu, veri ya da format sorunu değil,
    bileşenin kendi render motorunun bir sınırlaması. Kesin çözüm için
    canvas'ı hiç devreye sokmayan düz bir HTML tabloya (st.table)
    geçiyoruz; bu yüzden sayıları burada elle sabit ondalıklı string'e
    çeviriyoruz (st.table column_config desteklemez).
    """
    display = df.copy()
    display["Sen."] = display["Sen."].map(lambda v: f"{int(v)}")
    display["Fiyat Çarpanı"] = display["Fiyat Çarpanı"].map(lambda v: f"{v:.4f}")
    display["Vol Yönü"] = display["Vol Yönü"].map(lambda v: f"{v:+.0f}")
    display["S_yeni"] = display["S_yeni"].map(lambda v: f"{v:.4f}")
    display["IV_yeni"] = display["IV_yeni"].map(lambda v: f"{v:.6f}")
    display[price_column] = display[price_column].map(lambda v: f"{v:.6f}")
    display["Fark"] = display["Fark"].map(lambda v: f"{v:+.6f}")
    display["Kısa K/Z (TL)"] = display["Kısa K/Z (TL)"].map(lambda v: f"{v:+.4f}")
    # st.table her zaman index'i gösterir (hide_index parametresi yok);
    # "Sen." kolonuyla çakışan bir 0..15 numaralı ikinci kolon görünmesin
    # diye index'i boşaltıyoruz.
    display.index = [""] * len(display)
    return display


def _format_natural(value: float, max_decimals: int = 6) -> str:
    """Değeri, sona yapay sıfır eklemeden 'gerçek hâliyle' string'e çevirir.

    Ör: 0.31 -> "0.31" (0.3100 değil), 3.0 -> "3" (3.0000 değil).
    """
    text = f"{value:.{max_decimals}f}".rstrip("0").rstrip(".")
    return text if text and text != "-" else "0"


def _streamlit_override_row(
    st,
    label: str,
    auto_value: float,
    key: str,
    source: str | None = None,
    decimals: int = 4,
    live: bool = False,
) -> float:
    """Tek bir 'otomatik değer + manuel giriş + Değiştir onayı' satırı çizer.

    Değiştir onay kutusu satırın sağ kenarındadır. Manuel giriş alanı,
    Değiştir işaretlenene kadar KİLİTLİDİR (disabled) -- Streamlit'in
    kendi devre dışı bırakma efektiyle soluklaşır, bu da alanın kilitli
    olduğunu görsel olarak belli eder. Değiştir işaretlenince kilit kalkar.

    Args:
        source: Verinin nereden geldiğini belirten kısa not (ör.
            "yfinance" ya da "Takasbank dökümanı"). Verilirse otomatik
            değerin yanında gösterilir.
        decimals: Manuel giriş alanının ondalık basamak sayısı (adım
            büyüklüğü de buna göre ayarlanır).
        live: True ise, yeşil noktalı küçük bir "canlı" göstergesi eklenir
            (sadece gerçekten anlık/canlı çekilen veriler için, ör. spot).

    Returns:
        Değiştir işaretliyse manuel girilen değer, değilse otomatik değer.
    """
    val_key = f"{key}_val"
    chk_key = f"{key}_chk"

    # Değiştir kapalıyken manuel alanın session_state'ini her zaman güncel
    # otomatik değere senkron tut. number_input'a hem "value" hem "key"
    # birlikte verilip key zaten session_state'te varsa Streamlit "value"yi
    # yok sayabiliyor -- bu da Değiştir ilk işaretlendiğinde alanın 0'a
    # sıfırlanmasına yol açıyordu. Tek doğru kaynak session_state olsun diye
    # "value" parametresini hiç vermiyoruz, sadece burada elle senkronluyoruz.
    if not st.session_state.get(chk_key, False):
        st.session_state[val_key] = round(float(auto_value), decimals)

    c1, c2, c3 = st.columns([2.2, 1.5, 1])
    with c1:
        st.markdown(f"**{label}**")
        caption = f"Otomatik: {_format_natural(auto_value)}"
        if source:
            caption += f"  ·  _{source}_"
        if live:
            caption += "  ·  :green[●] canlı"
        st.caption(caption)
    with c3:
        use_override = st.checkbox("Değiştir", key=chk_key)
    with c2:
        manual_value = st.number_input(
            "Manuel değer",
            step=10**-decimals,
            format=f"%.{decimals}f",
            key=val_key,
            label_visibility="collapsed",
            disabled=not use_override,
        )
    return manual_value if use_override else auto_value


def run_streamlit() -> None:
    """Streamlit dashboard: firma + vade + strike gir, call/put min teminatı gör.

    Otomatik çekilen her bileşen (spot, volatilite, PSR, VSR, Extreme
    Move Multiplier, Extreme Move Covered Fraction, Intra-Commodity
    Spread Charge, SOM) checkbox ile açılan bir alana manuel değer
    girerek override edilebilir.
    """
    import streamlit as st

    st.set_page_config(page_title="BIST SPAN Teminatı", layout="wide")
    st.title("BIST Opsiyonları — Minimum SPAN Teminatı (Call & Put)")
    st.markdown(
        "Bir opsiyonu satıp (yazıp) kısa pozisyon aldığında, Takasbank'ın "
        "senden isteyeceği minimum başlangıç teminatını SPAN metodolojisiyle hesaplar.  \n"
        "Opsiyonu alan (uzun pozisyon) taraf için teminat gerekmez — bu hesap sadece opsiyon satıcıları içindir."
    )
    st.caption(
        "Firma ve vade gir, 'Verileri Çek'e bas. Güncel fiyat/volatilite ve "
        "Takasbank risk parametreleri otomatik çekilir; istersen her bileşeni "
        "aşağıda tek tek değiştirebilirsin."
    )

    with st.expander("Gelişmiş ayarlar"):
        risk_params_file = st.text_input(
            "Takasbank Risk Parametre Dosyası (PDF/JSON yolu)",
            value=str(DEFAULT_RISK_PARAMS_FILE),
        )
        contracts = st.number_input(
            "Kontrat Sayısı (kısa pozisyon için negatif)", value=-1, step=1
        )
        risk_free_rate = st.number_input(
            "Risksiz Faiz Oranı", min_value=0.0, value=0.45
        )

    @st.cache_data(show_spinner=False)
    def _cached_available_tickers(path_str: str) -> list[str]:
        # Streamlit her etkileşimde tüm scripti yeniden çalıştırır; bu
        # cache olmadan PDF her checkbox tıklamasında yeniden parse edilir.
        return available_tickers(Path(path_str))

    try:
        tickers = _cached_available_tickers(risk_params_file)
    except Exception as exc:
        st.error(f"Risk parametre dosyası okunamadı: {exc}")
        return

    col1, col2 = st.columns(2)
    with col1:
        ticker = st.selectbox(
            "Hisse (opsiyon verisi olan hisseler)",
            options=tickers,
            index=tickers.index("AKBNK") if "AKBNK" in tickers else 0,
            help=(
                "Bu liste, seçili risk parametre dosyasında tam opsiyon "
                "verisi (PSR/VSR/SOM vb.) bulunan hisselerdir -- resmi "
                "BIST30 endeks listesiyle birebir aynı olmayabilir."
            ),
        )
    with col2:
        expiry = st.date_input("Vade Tarihi")

    if st.button("Verileri Çek", type="primary"):
        normalized = _normalize_ticker(ticker)
        try:
            with st.spinner("Fiyat/volatilite ve risk parametreleri çekiliyor..."):
                price_data = data_fetch.get_price_data(normalized)
                store = _load_risk_params_store(Path(risk_params_file))
                risk_params = store.get(normalized)
        except Exception as exc:
            st.error(f"Veri çekilemedi: {exc}")
            st.session_state.pop("fetched", None)
        else:
            st.session_state["fetched"] = {
                "ticker": normalized,
                "spot": price_data.current_price,
                "volatility": price_data.historical_volatility,
                "risk_params": risk_params,
            }

    fetched = st.session_state.get("fetched")
    if not fetched or fetched["ticker"] != _normalize_ticker(ticker):
        if fetched:
            st.info("Hisse değişti — tekrar 'Verileri Çek'e bas.")
        return

    st.divider()
    st.subheader("Strike")
    strike = st.number_input(
        "Kullanım Fiyatı", min_value=0.0, value=round(fetched["spot"], 2)
    )

    auto_tte = (expiry - date.today()).days / 365
    # bkz. _streamlit_override_row -- aynı sebeple "value" değil session_state
    # kullanıyoruz (Değiştir ilk işaretlendiğinde 0'a sıfırlanma bug'ı).
    if not st.session_state.get("tte_chk", False):
        st.session_state["tte_val"] = round(auto_tte, 4)

    c1, c2, c3 = st.columns([2.2, 1.5, 1])
    with c1:
        st.markdown("**Vadeye Kalan Süre (T, yıl)**")
        st.caption(
            f"Otomatik: {_format_natural(auto_tte)} "
            f"({(expiry - date.today()).days} gün / 365)  ·  _hesaplanan_"
        )
    with c3:
        override_tte = st.checkbox("Değiştir", key="tte_chk")
    with c2:
        manual_tte = st.number_input(
            "T (yıl)",
            step=0.0001,
            format="%.4f",
            key="tte_val",
            label_visibility="collapsed",
            disabled=not override_tte,
            help="Bir referans hesaplayıcının (Excel vb.) ondalık T'siyle birebir karşılaştırmak için kullan.",
        )
    time_to_expiry_override = manual_tte if override_tte else None

    st.subheader("Otomatik Çekilen Değerler (istersen değiştir)")
    rp = fetched["risk_params"]
    spot = _streamlit_override_row(
        st, "Güncel Fiyat (Spot)", fetched["spot"], "spot", source="yfinance", live=True
    )
    volatility = _streamlit_override_row(
        st,
        "Historical Volatility",
        fetched["volatility"],
        "vol",
        source="yfinance, geçmiş fiyatlardan hesaplanan",
    )
    psr = _streamlit_override_row(
        st,
        "Price Scan Range (PSR)",
        rp.price_scan_range,
        "psr",
        source="Takasbank dökümanı",
        decimals=4,
    )
    vsr = _streamlit_override_row(
        st,
        "Volatility Scan Range (VSR)",
        rp.volatility_scan_range,
        "vsr",
        source="Takasbank dökümanı",
    )
    emm = _streamlit_override_row(
        st,
        "Extreme Move Multiplier",
        rp.extreme_move_multiplier,
        "emm",
        source="Takasbank dökümanı",
    )
    emcf = _streamlit_override_row(
        st,
        "Extreme Move Covered Fraction",
        rp.extreme_move_covered_fraction,
        "emcf",
        source="Takasbank dökümanı",
    )
    som = _streamlit_override_row(
        st,
        "Short Option Minimum (SOM)",
        rp.short_option_minimum,
        "som",
        source="Takasbank dökümanı",
    )

    st.divider()
    st.subheader("Vadeler Arası Spread Ücreti (Intra-Commodity Spread Charge)")
    st.caption(
        f"Takasbank'ın {fetched['ticker']} için yayınladığı referans değer: "
        f"**{rp.intra_commodity_spread_charge:,.2f} TL / spread birimi**. "
        "Bu ücret SADECE aynı dayanak varlıkta birden fazla vadeli gerçek bir "
        "spread pozisyonun varsa uygulanır. Aşağıdaki tek bacaklı/tek vadeli "
        "pozisyon için doğru değer **0**'dır — spread pozisyonun olduğunu "
        "biliyorsan alanı değiştir."
    )
    icsc = st.number_input(
        "Uygulanacak Spread Ücreti (TL)", value=0.0, min_value=0.0, key="icsc_applied"
    )

    if st.button("Hesapla", type="primary"):
        inputs = SpanCalculationInput(
            ticker=fetched["ticker"],
            strike=strike,
            option_type="call",  # compute_call_and_put içinde yok sayılır
            contracts=int(contracts),
            expiry=expiry,
            risk_params_file=Path(risk_params_file),
            risk_free_rate=risk_free_rate,
            spot_override=spot,
            volatility_override=volatility,
            time_to_expiry_override=time_to_expiry_override,
            price_scan_range_override=psr,
            volatility_scan_range_override=vsr,
            extreme_move_multiplier_override=emm,
            extreme_move_covered_fraction_override=emcf,
            intra_commodity_spread_charge_override=icsc,
            short_option_minimum_override=som,
        )
        try:
            st.session_state["results"] = compute_call_and_put(inputs)
            st.session_state["results_inputs"] = inputs
        except Exception as exc:
            st.error(str(exc))
            st.session_state.pop("results", None)

    results = st.session_state.get("results")
    if results is None:
        return
    result_inputs = st.session_state["results_inputs"]

    st.divider()
    m1, m2 = st.columns(2)
    m1.metric(
        "Call — Min. Teminat",
        f"{results['call']['span']['total_initial_margin']:,.2f} TL",
    )
    m2.metric(
        "Put — Min. Teminat",
        f"{results['put']['span']['total_initial_margin']:,.2f} TL",
    )

    if st.button("Bileşenler"):
        st.session_state["show_components"] = not st.session_state.get(
            "show_components", False
        )
    if st.session_state.get("show_components", False):
        table = _format_comparison_table(result_inputs, results["call"], results["put"])
        st.dataframe(table, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("SPAN Mekanizması Nasıl Çalışır?")
    st.write(
        "SPAN, dayanak varlığın fiyatının ve volatilitesinin farklı yönlerde "
        "hareket ettiği **16 farklı risk senaryosu** kurar: fiyat için "
        "Price Scan Range'in (PSR) 0, ±1/3, ±2/3 ve ±tamamı kadar şoklar, "
        "her fiyat seviyesinde volatilite için hem yukarı hem aşağı şoklar "
        "(bu 14 senaryoyu oluşturur), artı PSR'nin çok daha büyük bir katı "
        "kadar (Extreme Move Multiplier) 2 'aşırı hareket' senaryosu daha.\n\n"
        "Her senaryoda opsiyon Black-Scholes ile yeniden fiyatlanır ve kısa "
        "pozisyonun o senaryodaki kâr/zararı hesaplanır. **En kötü (en büyük "
        "zararlı) senaryo** 'Scanning Risk' olarak seçilir — çünkü teminat, "
        "olabilecek en kötü tek günlük hareketi karşılayacak kadar olmalıdır."
    )

    st.subheader("16 SPAN Senaryosu ve P&L (Scanning Risk dökümü)")
    sc1, sc2 = st.columns(2)
    with sc1:
        st.markdown(
            f"**Call** — en kötü senaryo: "
            f"#{results['call']['scenarios'].attrs['worst_scenario_no']}"
        )
        st.table(
            _scenario_display_table(results["call"]["scenarios"], "Call Fiyatı")
        )
    with sc2:
        st.markdown(
            f"**Put** — en kötü senaryo: "
            f"#{results['put']['scenarios'].attrs['worst_scenario_no']}"
        )
        st.table(
            _scenario_display_table(results["put"]["scenarios"], "Put Fiyatı")
        )


if __name__ == "__main__":
    try:
        import streamlit.runtime as _st_runtime

        _in_streamlit = _st_runtime.exists()
    except Exception:
        _in_streamlit = False

    if _in_streamlit:
        run_streamlit()
    else:
        run_cli()
