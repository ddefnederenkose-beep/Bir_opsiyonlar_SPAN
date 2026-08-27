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
import json
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

from bist_span import data_fetch, takasbank_xml
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

# Takasbank'ın per-ticker PSR/VSR listesinde geçen ama HENÜZ desteklenmeyen
# (opsiyon serisi bu uygulamada işlenmeyen) döviz/endeks vadeli işlem
# sembolleri -- hisse seçim listesinden ve senaryo tablosu başlıklarından
# hariç tutulur. "USDTRY" de burada -- PDF'teki bu KISALTILMIŞ isim,
# günlük XML'de kendi opsiyon serisine sahip DEĞİL (sadece referans
# fiyatı var); gerçek ürünler kendi GERÇEK adlarıyla (USDTRYK, USDTRYKP)
# _EXTRA_TAKASBANK_TICKERS ile ayrıca eklenir -- bkz. aşağısı.
_NON_EQUITY_TICKERS = {"USDTRY", "EURTRY", "X10XB", "XLBNK", "XSD25"}

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
            yerine bu değer kullanılır (hem call hem put için paylaşılan
            varsayılan). call_volatility_override/put_volatility_override
            verilmişse, o taraf için ONLAR kullanılır (Takasbank implied
            volatility -- her strike/tip için farklı olabilir).
        call_volatility_override, put_volatility_override: Verilirse, o
            tarafın volatilitesi olarak (volatility_override yerine)
            kullanılır. Streamlit arayüzü bunları Takasbank XML'inden
            gelen implied volatility ile doldurur.
        market_price_override, call_market_price_override, put_market_price_override:
            16 SPAN senaryosundaki "Fark" hesabının TABAN fiyatı (bkz.
            span_engine.calculate_scenario_pnl'in base_price parametresi).
            Verilmezse (üçü de None), taban olarak kendi Black-Scholes
            hesabımızın TEORİK fiyatı kullanılır (eski davranış). Verilirse
            (ör. Takasbank XML'in opt/p'si -- gerçek piyasa/uzlaşma fiyatı),
            teorik hesap yerine doğrudan o kullanılır. call/put versiyonları
            volatility_override'daki gibi tarafa özel önceliklidir.
        time_to_expiry_override: Verilirse, T için hem Takasbank XML'in
            kendi <t> alanı HEM DE takvim hesabı yerine doğrudan bu değer
            (yıl cinsinden) kullanılır. Verilmezse (None) T ARTIK ANA
            KAYNAK olarak Takasbank XML'den gelir (spot/taban fiyat gibi);
            Takasbank'ta bu strike/vade/tip bulunamazsa (expiry - bugün)
            .gün/365'e (takvim günü) düşülür. Bu ikisi GENELDE FARKLIDIR
            -- Takasbank'ın T'si takvim günü/365 değildir (ör. 33.54/365,
            35 takvim günü değil) -- ve PC-SPAN'ın Risk Array'iyle birebir
            karşılaştırıldığında (bkz. proje sohbet geçmişi) bu farkın
            Scanning Risk'i belirgin ölçüde kaydırdığı doğrulandı. Bir
            referans hesaplayıcı (Excel vb.) T'yi kendi ondalık değeriyle
            giriyorsa, birebir karşılaştırma için bu alanı kullan.
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
        position_opened_today, execution_price_override: Opsiyon Prim
            Değeri (Madde 37/3) SADECE pozisyon BUGÜN açıldıysa/kapatıldıysa
            uygulanır -- taşınan (mevcut) pozisyonlar için tanım gereği 0'dır
            (varsayılan budur, position_opened_today=False). True verilip
            execution_price_override de verilirse (kısa pozisyondan tahsil
            edilen prim, yani işlem anındaki fiyat), Opsiyon Prim Değeri =
            |kontrat| × execution_price_override × kontrat çarpanı olarak
            hesaplanıp teminattan DÜŞÜLÜR (o parayı zaten tahsil ettin).
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
    call_volatility_override: float | None = None
    put_volatility_override: float | None = None
    market_price_override: float | None = None
    call_market_price_override: float | None = None
    put_market_price_override: float | None = None
    time_to_expiry_override: float | None = None
    price_scan_range_override: float | None = None
    volatility_scan_range_override: float | None = None
    extreme_move_multiplier_override: float | None = None
    extreme_move_covered_fraction_override: float | None = None
    intra_commodity_spread_charge_override: float | None = None
    short_option_minimum_override: float | None = None
    position_opened_today: bool = False
    execution_price_override: float | None = None


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


# USDTRYK (asıl/birincil) ve USDTRYKP (fiziki teslimatlı varyant) --
# ikisi de gerçek USD/TRY opsiyon ürünleri (bkz. takasbank_xml.py modül
# dokümantasyonu) ama Takasbank'ın PDF risk parametre dosyasında KENDİ
# satırlarına sahip DEĞİLLER -- PDF'te sadece kısaltılmış "USDTRY" satırı
# var. Gerçek XML'de PSR/VSR'ları BİREBİR AYNI (11.0%/0.32) olduğu
# doğrulanmıştır; SOM/Intra-Commodity Spread Charge için ayrı bir kaynak
# olmadığından ikisi de "USDTRY" satırınınkini ödünç alıyor -- bu
# YAKLAŞIKTIR, Takasbank ileride bu ürünler için ayrı satır yayınlarsa
# burası güncellenmeli.
_RISK_PARAMS_FALLBACK_TICKER = {"USDTRYK": "USDTRY", "USDTRYKP": "USDTRY"}

# PDF'te kendi satırı olmayan ama (yukarıdaki fallback ile) risk
# parametresi türetilebilen, günlük XML'de gerçek opsiyon serisi
# doğrulanmış ek Takasbank ürünleri -- available_tickers()'a PDF listesine
# ek olarak eklenir.
_EXTRA_TAKASBANK_TICKERS = tuple(_RISK_PARAMS_FALLBACK_TICKER)


def _get_risk_params(store: RiskParamsStore, ticker: str) -> RiskParams:
    """store.get(ticker) dener; PDF'te kendi satırı yoksa (bkz.
    _RISK_PARAMS_FALLBACK_TICKER) başka bir ticker'ın parametrelerini
    ÖDÜNÇ ALIP ticker alanını isteneni yansıtacak şekilde döner."""
    try:
        return store.get(ticker)
    except KeyError:
        # store.get() ".IS" sonekini kendi içinde temizler (ör.
        # "AKBNK.IS" -> "AKBNK") -- fallback eşlemesi de aynı normalize
        # edilmiş isimle çalışmalı, yoksa ".IS" soneki taşıyan bir
        # çağrıda (compute_span_result'ın normalize ettiği ticker gibi)
        # eşleşme hiç bulunamaz.
        normalized = ticker.removesuffix(".IS")
        fallback = _RISK_PARAMS_FALLBACK_TICKER.get(normalized)
        if fallback is None:
            raise
        return replace(store.get(fallback), ticker=normalized)


def available_tickers(risk_params_file: Path = DEFAULT_RISK_PARAMS_FILE) -> list[str]:
    """Verilen risk parametre dosyasında tam opsiyon verisi olan hisseleri döner.

    Yani: bu listedeki her ticker için call/put SPAN hesabı yapılabilir.
    Döviz/endeks vadeli işlemleri (_NON_EQUITY_TICKERS) hariç tutulur;
    PDF'te kendi satırı olmayan ama risk parametresi türetilebilen ek
    ürünler (_EXTRA_TAKASBANK_TICKERS, ör. USDTRYKP) dahil edilir.

    Not: Bu, "resmi BIST30 endeks listesi" DEĞİL -- Takasbank'ın bu
    belgede opsiyon risk parametresi yayınladığı hisselerin listesidir
    (çoğu BIST30 üyesiyle örtüşür, ama endeks kompozisyonu zamanla
    değişebileceği için garantili bire bir eşleşme değildir).
    """
    store = _load_risk_params_store(risk_params_file)
    base = {t for t in store.tickers() if t not in _NON_EQUITY_TICKERS}
    return sorted(base | set(_EXTRA_TAKASBANK_TICKERS))


def fetch_takasbank_series(ticker: str) -> dict | None:
    """Bir hisse için Takasbank günlük PC-SPAN XML'inden vade/strike verisini çeker.

    Günlük cache'i (yoksa indirip) hazırlar, sonra sadece bu hisseye ait
    kısmı döner. XML'de bu hisse yoksa (veri kaynağı erişilemez, hisse
    opsiyonu yok vb.) None döner -- arayüz bu durumda eski (manuel tarih/
    strike girişi) akışa nazikçe düşer.

    Returns:
        {"20260831": {"t":..., "intrRate":..., "psr":..., "vsr":...,
        "options": [{"k":..., "o":"C"/"P", "v":...}, ...]}, ...} ya da None.
    """
    normalized = ticker.upper().strip().removesuffix(".IS")
    xml_pfcode = takasbank_xml.to_xml_pfcode(normalized)
    try:
        _trading_day, cache_path = takasbank_xml.ensure_daily_cache()
        distilled = json.loads(cache_path.read_text())
    except Exception:
        return None
    return distilled.get("products", {}).get(xml_pfcode)


def _available_expiries(series: dict) -> list[date]:
    """fetch_takasbank_series çıktısındaki vade string'lerini (YYYYMMDD)
    sıralı date listesine çevirir."""
    dates = []
    for expiry_str in series:
        try:
            dates.append(
                date(int(expiry_str[:4]), int(expiry_str[4:6]), int(expiry_str[6:8]))
            )
        except ValueError:
            continue
    return sorted(dates)


def _available_strikes(series: dict, expiry: date) -> list[float]:
    """Bir vadedeki TÜM strike'ları (call+put birleşimi) sıralı döner.

    T/faiz/PSR/VSR seri (vade) bazında ortak olduğu için call'da olup
    put'ta olmayan (ya da tam tersi) bir strike de listelenir. Böyle bir
    durumda o TARAF için (implied volatility, taban fiyat, sonuç) hiçbir
    teorik/uydurulmuş değer üretilmez -- run_streamlit o tarafı "bu tarihte
    işlem görmemektedir" uyarısıyla tamamen hesaplama dışı bırakır.
    """
    expiry_data = series.get(expiry.strftime("%Y%m%d"))
    if not expiry_data:
        return []
    return sorted({opt["k"] for opt in expiry_data["options"]})


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
    position: OptionPosition,
    spot: float,
    volatility: float,
    risk_params: RiskParams,
    base_price: float | None = None,
) -> pd.DataFrame:
    """SPAN'in 16 risk senaryosunu, kullanıcının Excel'iyle (THYAO_SPAN_Hesaplama)
    BİREBİR AYNI sütun yapısında bir tabloya döker: Sen., Açıklama, Fiyat
    Çarpanı, Vol Yönü, S_yeni, IV_yeni, {Call|Put} Fiyatı, Fark, Kısa K/Z (TL).

    Args:
        base_price: "Fark"/"Kısa K/Z" hesabının taban fiyatı (ör. Takasbank
            XML'in opt/p'si -- gerçek piyasa fiyatı). Verilmezse (None),
            spot/volatility ile hesaplanan TEORİK Black-Scholes fiyatı
            taban olarak kullanılır.

    Scanning Risk (bkz. span_engine.scanning_risk), bu 16 K/Z'nin en
    kötüsünden (en büyük zarardan) hesaplanır -- Excel'deki "Aktif
    Senaryo #" değeri df.attrs["worst_scenario_no"]'da saklanır.
    df.attrs["current_price"], fiilen KULLANILAN taban fiyattır (market
    varsa market, yoksa teorik); df.attrs["theoretical_price"] her zaman
    teorik Black-Scholes fiyatıdır (karşılaştırma/referans amaçlı, market
    fiyat kullanılsa bile).
    """
    scenarios = generate_risk_scenarios(
        spot=spot,
        volatility=volatility,
        price_scan_range=risk_params.price_scan_range,
        volatility_scan_range=risk_params.volatility_scan_range,
        extreme_move_multiplier=risk_params.extreme_move_multiplier,
        extreme_move_covered_fraction=risk_params.extreme_move_covered_fraction,
    )
    theoretical_price = black_scholes_price(
        spot,
        position.strike,
        position.time_to_expiry,
        position.risk_free_rate,
        volatility,
        position.option_type,
    )
    effective_base = base_price if base_price is not None else theoretical_price
    price_column = "Call Fiyatı" if position.option_type == "call" else "Put Fiyatı"

    rows = []
    for i, scenario in enumerate(scenarios, start=1):
        pnl = calculate_scenario_pnl(
            position, spot, volatility, scenario, base_price=effective_base
        )
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
                "Fark": round(shocked_price - effective_base, 6),
                "Kısa K/Z (TL)": round(pnl, 4),
            }
        )
    df = pd.DataFrame(rows)
    worst_idx = df["Kısa K/Z (TL)"].idxmin()
    df.attrs["worst_scenario_no"] = int(df.loc[worst_idx, "Sen."])
    df.attrs["current_price"] = effective_base
    df.attrs["theoretical_price"] = theoretical_price
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

    # historical volatility hesabı için (ve spot'un Takasbank'ta
    # bulunamadığı durumlarda fallback için) yfinance'e hâlâ ihtiyaç var.
    # AMA artık ZORUNLU değil: USDTRY/XU030 gibi bazı ürünler yfinance'te
    # standart bir hisse sembolü olarak bulunmaz -- Takasbank zaten her
    # şeyi (spot/T/contract_size) sağlayabildiği için burada patlamak
    # yerine None'a düşüp devam ediyoruz.
    try:
        price_data = data_fetch.get_price_data(ticker, period=inputs.period)
    except Exception:
        price_data = None

    # Bu strike/vade/tip için Takasbank'ın kendi parametrelerini BİR KEZ
    # çekiyoruz -- hem T hem contract_size (kontrat çarpanı) buradan
    # gelir (spot ile aynı öncelik mantığı, bkz. aşağısı). Hisse
    # opsiyonlarında çarpan hep 100'dür ama SABİT DEĞİLDİR -- ör. XU030D
    # (BIST30 endeks opsiyonu) 10, USDTRYKP (USD/TRY opsiyonu) 1 kullanır
    # (bkz. takasbank_xml.TakasbankOptionParams.contract_size docstring'i).
    try:
        takasbank_params = takasbank_xml.get_option_params(
            ticker, inputs.expiry, inputs.strike, inputs.option_type
        )
    except Exception:
        takasbank_params = None

    # Spot fiyat ARTIK ANA KAYNAK olarak Takasbank'ın günlük XML'inden
    # gelir (yfinance sadece Takasbank'ta bu hisse/gün bulunamazsa
    # fallback olarak kullanılır). Bkz. proje sohbet geçmişi -- bu bilinçli
    # bir karar, spot'un artık yfinance'ten gelmesi İSTENMİYOR.
    if inputs.spot_override is not None:
        spot = inputs.spot_override
    else:
        try:
            spot = takasbank_xml.get_spot_price(ticker).price
        except Exception:
            if price_data is None:
                raise ValueError(
                    f"{ticker}: spot fiyat ne Takasbank'tan ne yfinance'ten "
                    "alınabildi -- spot_override ile elle ver."
                )
            spot = price_data.current_price

    if inputs.volatility_override is not None:
        volatility = inputs.volatility_override
    elif price_data is not None:
        volatility = price_data.historical_volatility
    else:
        raise ValueError(
            f"{ticker}: volatilite ne yfinance historical'dan alınabildi "
            "(sembol yfinance'te bulunamadı) -- volatility_override ile elle ver."
        )

    store = _load_risk_params_store(inputs.risk_params_file)
    risk_params = _apply_risk_params_overrides(_get_risk_params(store, ticker), inputs)

    if inputs.time_to_expiry_override is not None:
        time_to_expiry = inputs.time_to_expiry_override
        if time_to_expiry <= 0:
            raise ValueError("time_to_expiry_override sıfırdan büyük olmalı")
    else:
        # T ARTIK ANA KAYNAK olarak Takasbank XML'in kendi <t> alanından
        # gelir (spot ile aynı öncelik mantığı -- bkz. yukarısı). Bu ÖNEMLİ:
        # Takasbank'ın T'si takvim günü/365 DEĞİLDİR (ör. 33.54/365, 35
        # takvim günü değil) -- PC-SPAN'ın kendi Risk Array'iyle gerçek
        # veride birebir karşılaştırıldığında (bkz. proje sohbet geçmişi)
        # takvim-günü yaklaşıklığının Fark/Scanning Risk'i belirgin ölçüde
        # kaydırdığı doğrulandı. Takasbank'ta bu strike/vade/tip yoksa
        # (ör. hisse/opsiyon XML'de bulunamıyorsa) takvim gününe düşülür.
        if takasbank_params is not None:
            time_to_expiry = takasbank_params.time_to_expiry
        else:
            time_to_expiry = (inputs.expiry - date.today()).days / 365
        if time_to_expiry <= 0:
            raise ValueError(f"Vade tarihi ({inputs.expiry}) bugünden ileride olmalı")

    # contract_size (kontrat başına dayanak varlık miktarı): Takasbank'ta
    # bu strike/vade/tip bulunduysa ORADAN, bulunamazsa OptionPosition'ın
    # kendi varsayılanından (100 -- hisse opsiyonlarının tamamı için doğru)
    # gelir.
    contract_size_kwargs = (
        {"contract_size": int(takasbank_params.contract_size)}
        if takasbank_params is not None
        else {}
    )

    position = OptionPosition(
        ticker=ticker,
        strike=inputs.strike,
        option_type=inputs.option_type,
        contracts=inputs.contracts,
        time_to_expiry=time_to_expiry,
        risk_free_rate=inputs.risk_free_rate,
        **contract_size_kwargs,
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

    # market_price_override: 16 senaryonun "Fark" hesabında VE Net Opsiyon
    # Değeri'nde (Madde 37/2) taban fiyat olarak kullanılır (ör. Takasbank
    # XML'in opt/p'si). Verilmezse (None), span_engine kendi teorik
    # Black-Scholes fiyatını taban alır. Bkz. SpanCalculationInput docstring'i.
    market_price = inputs.market_price_override

    # Opsiyon Prim Değeri (Madde 37/3): SADECE pozisyon bugün açıldıysa
    # uygulanır -- taşınan pozisyon için (varsayılan) 0'dır. Bkz.
    # SpanCalculationInput.position_opened_today docstring'i.
    option_premium_value = 0.0
    if inputs.position_opened_today and inputs.execution_price_override is not None:
        option_premium_value = (
            abs(inputs.contracts)
            * inputs.execution_price_override
            * position.contract_size
        )

    span_result = calculate_span_margin(
        position=position,
        spot=spot,
        volatility=volatility,
        risk_params=risk_params,
        intra_commodity_spread_charge=intra_commodity_spread_charge,
        base_price=market_price,
        option_premium_value=option_premium_value,
    )

    scenarios = _build_scenario_table(
        position, spot, volatility, risk_params, base_price=market_price
    )

    return {
        "spot": spot,
        "volatility": volatility,
        "risk_params": risk_params,
        "time_to_expiry": time_to_expiry,
        "span": span_result,
        "scenarios": scenarios,
        "market_price": market_price,  # None ise taban teoriktir
    }


def compute_call_and_put(inputs: SpanCalculationInput) -> dict[str, dict]:
    """Aynı girdilerle hem call hem put için compute_span_result çalıştırır.

    inputs.option_type yok sayılır; hem "call" hem "put" hesaplanır. Her
    taraf kendi volatilitesini ve taban fiyatını kullanır:
    call_volatility_override/put_volatility_override ve
    call_market_price_override/put_market_price_override verilmişse o
    taraf için ONLAR kullanılır (ör. Takasbank implied volatility/piyasa
    fiyatı, strike/tipe göre farklı olabilir); verilmemişse ikisi de
    paylaşımlı volatility_override/market_price_override'a (ya da onlar
    da yoksa historical volatility/teorik Black-Scholes fiyatına) düşer.

    Returns:
        {"call": <compute_span_result çıktısı>, "put": <compute_span_result çıktısı>}
    """

    def _resolve(shared: float | None, call_specific: float | None, put_specific: float | None):
        call_val = call_specific if call_specific is not None else shared
        put_val = put_specific if put_specific is not None else shared
        return call_val, put_val

    call_vol, put_vol = _resolve(
        inputs.volatility_override,
        inputs.call_volatility_override,
        inputs.put_volatility_override,
    )
    call_price, put_price = _resolve(
        inputs.market_price_override,
        inputs.call_market_price_override,
        inputs.put_market_price_override,
    )
    return {
        "call": compute_span_result(
            replace(
                inputs,
                option_type="call",
                volatility_override=call_vol,
                market_price_override=call_price,
            )
        ),
        "put": compute_span_result(
            replace(
                inputs,
                option_type="put",
                volatility_override=put_vol,
                market_price_override=put_price,
            )
        ),
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
        "--call-base-price",
        type=float,
        default=None,
        dest="call_market_price_override",
        help=(
            "Call için 16 senaryonun 'Fark' hesabında taban fiyat (ör. "
            "Takasbank'ın gerçek piyasa fiyatı). Verilmezse teorik "
            "Black-Scholes fiyatı taban alınır."
        ),
    )
    parser.add_argument(
        "--put-base-price",
        type=float,
        default=None,
        dest="put_market_price_override",
        help="Put için 16 senaryonun 'Fark' hesabında taban fiyat (bkz. --call-base-price).",
    )
    parser.add_argument(
        "--position-opened-today",
        action="store_true",
        dest="position_opened_today",
        help=(
            "Pozisyon BUGÜN açıldıysa/kapatıldıysa Opsiyon Prim Değeri'ni "
            "(Madde 37/3) hesaba katar -- --execution-price ile birlikte "
            "kullanılmalı. Verilmezse (varsayılan), taşınan pozisyon "
            "varsayılır ve bu terim 0'dır."
        ),
    )
    parser.add_argument(
        "--execution-price",
        type=float,
        default=None,
        dest="execution_price_override",
        help="--position-opened-today ile birlikte: pozisyonun bugünkü işlem fiyatı (TL).",
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
        call_market_price_override=args.call_market_price_override,
        put_market_price_override=args.put_market_price_override,
        position_opened_today=args.position_opened_today,
        execution_price_override=args.execution_price_override,
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
        decimals: Manuel giriş alanının EKRANDA gösterdiği/adımladığı
            ondalık basamak sayısı -- SADECE düzenleme kolaylığı içindir.
            Kullanıcı alanı gerçekten değiştirmediği sürece hesaba giden
            değer bu hassasiyete ASLA indirgenmez (bkz. aşağı).
        live: True ise, yeşil noktalı küçük bir "canlı" göstergesi eklenir
            (sadece gerçekten anlık/canlı çekilen veriler için, ör. spot).

    Returns:
        Değiştir işaretli VE kullanıcı gerçekten farklı bir değer
        girmişse o değer; aksi halde (Değiştir kapalı, ya da açık ama
        alan hâlâ otomatik değerin görüntülenen haliyle aynıysa) otomatik
        değerin TAM (yuvarlanmamış) hâli.
    """
    val_key = f"{key}_val"
    chk_key = f"{key}_chk"
    auto_display = round(float(auto_value), decimals)

    # Değiştir kapalıyken manuel alanın session_state'ini her zaman güncel
    # otomatik değere senkron tut. number_input'a hem "value" hem "key"
    # birlikte verilip key zaten session_state'te varsa Streamlit "value"yi
    # yok sayabiliyor -- bu da Değiştir ilk işaretlendiğinde alanın 0'a
    # sıfırlanmasına yol açıyordu. Tek doğru kaynak session_state olsun diye
    # "value" parametresini hiç vermiyoruz, sadece burada elle senkronluyoruz.
    # ÖNEMLİ: bu sadece EKRANDA gösterilecek başlangıç metnidir -- kullanıcı
    # dokunmadığı sürece dönüş değeri hâlâ auto_value'nun tam hassasiyeti
    # olacak (aşağıdaki "değişti mi" kontrolüne bkz.), yuvarlanmış bu
    # görüntü değeri formüle asla sızmaz.
    if not st.session_state.get(chk_key, False):
        st.session_state[val_key] = auto_display

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

    if not use_override:
        return auto_value
    # Kullanıcı kutuyu işaretleyip alana hiç dokunmadıysa (ekranda hâlâ
    # otomatik değerin yuvarlanmış görüntüsü duruyorsa), o yuvarlanmış
    # metni değil, auto_value'nun TAM hassasiyetini kullan. Sadece
    # kullanıcı gerçekten farklı bir sayı yazdıysa onu (manual_value)
    # kullan -- bu durumda hassasiyet, kullanıcının kendi girdisiyle sınırlı.
    if manual_value == auto_display:
        return auto_value
    return manual_value


def run_streamlit() -> None:
    """Streamlit dashboard: firma seç, vade/strike Takasbank'ın günlük
    XML'inden gelen GERÇEK mevcut değerlerden seç, call/put min teminatı gör.

    Hisse seçilip veriler çekildikten sonra: vade ve strike, Takasbank'ın
    o günkü PC-SPAN dosyasında gerçekten bulunan değerlerden dropdown ile
    seçilir (bkz. takasbank_xml.py). Spot fiyat (<phyPf><phy><p>), taban/
    piyasa fiyatı (<opt><p>, 16 senaryonun "Fark" hesabında Black-Scholes
    taban yerine kullanılır), T, faiz oranı, PSR, VSR, Extreme Move
    Multiplier/Covered Fraction ve implied volatility (call/put ayrı ayrı)
    bu strike/vade için Takasbank'tan otomatik çekilir; Takasbank verisi
    yoksa (hisse/vade/strike XML'de bulunamazsa) sırasıyla Takasbank PDF'i,
    yfinance (spot/historical volatility için fallback) ve kendi teorik
    Black-Scholes hesabımıza (taban fiyat için fallback) nazikçe düşülür.
    Otomatik çekilen her bileşen, "Değiştir" işaretlenip manuel bir değer
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

    ticker = st.selectbox(
        "Hisse",
        options=tickers,
        index=tickers.index("AKBNK") if "AKBNK" in tickers else 0,
        help=(
            "Bu liste, seçili risk parametre dosyasında tam opsiyon "
            "verisi (PSR/VSR/SOM vb.) bulunan hisselerdir -- resmi "
            "BIST30 endeks listesiyle birebir aynı olmayabilir."
        ),
    )

    if st.button("Verileri Çek", type="primary"):
        normalized = _normalize_ticker(ticker)
        try:
            with st.spinner(
                "Fiyat/volatilite, risk parametreleri ve Takasbank verileri çekiliyor "
                "(ilk çekişte ~10-30 saniye sürebilir)..."
            ):
                # yfinance -- artık SADECE fallback (spot: Takasbank yoksa;
                # volatility: Takasbank implied vol yoksa). USDTRY/XU030
                # gibi bazı ürünler yfinance'te standart bir hisse sembolü
                # olarak bulunmaz -- Takasbank zaten her şeyi sağlıyorsa bu
                # hiç sorun olmamalı, bu yüzden burada patlarsa (fetch
                # tamamen) durmak yerine None'a düşüp devam ediyoruz.
                try:
                    price_data = data_fetch.get_price_data(normalized)
                except Exception:
                    price_data = None
                store = _load_risk_params_store(Path(risk_params_file))
                risk_params = _get_risk_params(store, normalized)
                takasbank_series = fetch_takasbank_series(normalized)
        except Exception as exc:
            st.error(f"Veri çekilemedi: {exc}")
            st.session_state.pop("fetched", None)
        else:
            st.session_state["fetched"] = {
                "ticker": normalized,
                "spot": price_data.current_price if price_data else None,
                "volatility": price_data.historical_volatility if price_data else None,
                "risk_params": risk_params,
                "takasbank_series": takasbank_series,
            }

    fetched = st.session_state.get("fetched")
    if not fetched or fetched["ticker"] != _normalize_ticker(ticker):
        if fetched:
            st.info("Hisse değişti — tekrar 'Verileri Çek'e bas.")
        return

    takasbank_series = fetched.get("takasbank_series")
    # last_update_info() SADECE diskteki cache'i okur, kendisi asla tazelemez
    # -- tazeleme (gün içi cache 15 dakikadan eskiyse Takasbank'ta daha
    # güncel/nihai bir dosya çıkmış mı diye bakma) ensure_daily_cache()
    # içindedir. Önceden ensure_daily_cache() SADECE "Verileri Çek"e
    # basıldığında çağrılıyordu -- kullanıcı sayfada kalıp tekrar
    # basmadığı sürece "son güncelleme" saati hep İLK çekişte kalıyordu,
    # gerçekte daha yeni bir dosya çıksa bile. Burada -- Streamlit her
    # widget etkileşiminde tüm scripti yeniden çalıştırdığı için -- her
    # rerun'da (throttle'lı, çoğu zaman ağa hiç gitmeden) çağırarak sayfa
    # açık kaldığı sürece otomatik tazelenmesini sağlıyoruz.
    try:
        takasbank_xml.ensure_daily_cache()
    except Exception:
        pass
    takasbank_info = takasbank_xml.last_update_info()

    # Spot fiyat ARTIK ANA KAYNAK olarak Takasbank'ın günlük XML'inden
    # gelir (<phyPf><phy><p>); yfinance sadece Takasbank'ta bu hisse/gün
    # bulunamazsa fallback/karşılaştırma amaçlı kullanılır.
    try:
        takasbank_spot = takasbank_xml.get_spot_price(fetched["ticker"])
    except Exception:
        takasbank_spot = None
    if takasbank_spot is not None:
        spot_auto, spot_source = takasbank_spot.price, "Takasbank XML"
    else:
        spot_auto, spot_source = fetched["spot"], "yfinance (fallback — Takasbank'ta bulunamadı)"

    # Tek, konsolide bilgi bloğu -- spot/taban fiyat/T/faiz/PSR/VSR/Extreme
    # Move/implied volatility gibi aşağıdaki HER alanın yanına ayrı ayrı
    # "Takasbank XML" yazmak yerine, kaynağı ve son güncelleme zamanını
    # burada TEK SEFER açıklıyoruz.
    if takasbank_info:
        source_link = takasbank_xml.folder_url(takasbank_info["source_date"])
        durum = (
            "gün sonu (EOD, o günün nihai verisi)"
            if takasbank_info["is_final"]
            else "gün içi ara güncelleme — daha yeni bir dosya çıktıkça otomatik yenilenir"
        )
        # "son güncelleme" olarak Takasbank'ın bu belgeyi KENDİ sunucusunda
        # yayınladığı an gösterilir (published_at) -- bizim onu ne zaman
        # indirdiğimiz (cached_at) DEĞİL; bu ikisi karışınca kullanıcı
        # ekranın bayat kaldığını sanıyordu (bkz. proje sohbet geçmişi).
        # Eski cache'lerde published_at henüz yoksa (bu alan eklenmeden
        # önce yazılmışsa) cached_at'e nazikçe düşülür.
        published_at = takasbank_info.get("published_at")
        if published_at:
            update_line = (
                f"Takasbank'ın yayınladığı belge: "
                f"{published_at.strftime('%d.%m.%Y %H:%M')}"
            )
        else:
            update_line = (
                f"son güncelleme (bizim çekişimiz): "
                f"{takasbank_info['cached_at'].strftime('%d.%m.%Y %H:%M')}"
            )
        st.caption(
            f":green[●] Spot, taban/piyasa fiyatı, T, faiz oranı, PSR, VSR, "
            f"Extreme Move ve implied volatility [Takasbank'ın günlük PC-SPAN "
            f"dosyasından]({source_link}) otomatik çekiliyor · veri tarihi: "
            f"{takasbank_info['source_date'].strftime('%d.%m.%Y')} · {update_line} "
            f"({durum}). Aşağıda 'Değiştir' ile her alanı elle üzerine yazabilirsin."
        )
    else:
        st.caption(
            "⚠️ Takasbank'ın günlük XML verisi şu an çekilemedi — spot/taban fiyat "
            "ve risk parametreleri için yedek kaynaklara (PDF / yfinance / teorik "
            "hesap) düşülüyor."
        )

    st.divider()
    st.subheader("Vade ve Strike")

    if takasbank_series:
        available_expiries = _available_expiries(takasbank_series)
        expiry = st.selectbox(
            "Vade Tarihi",
            options=available_expiries,
            format_func=lambda d: d.strftime("%d.%m.%Y"),
            help="Takasbank'ın güncel dosyasında bu hisse için gerçekten mevcut olan vadeler.",
        )
    else:
        st.warning(
            "Bu hisse için Takasbank XML verisi bulunamadı — vade tarihini elle gir. "
            "T/faiz/PSR/VSR/volatilite otomatik çekilemeyecek, mevcut kaynaklara "
            "(Takasbank PDF / yfinance historical) düşülecek."
        )
        expiry = st.date_input("Vade Tarihi")

    available_strikes = (
        _available_strikes(takasbank_series, expiry) if takasbank_series else []
    )
    if available_strikes:
        closest_idx = min(
            range(len(available_strikes)),
            key=lambda i: abs(available_strikes[i] - spot_auto),
        )
        strike = st.selectbox(
            "Kullanım Fiyatı (Strike)",
            options=available_strikes,
            index=closest_idx,
            help="Takasbank'ın bu vade için gerçekten listelediği strike'lar (güncel fiyata en yakını varsayılan).",
        )
    else:
        strike = st.number_input(
            "Kullanım Fiyatı",
            min_value=0.0,
            value=round(spot_auto, 4),
            step=0.0001,
            format="%.4f",
        )

    # Bu strike/vade için Takasbank'tan T/faiz/PSR/VSR/IV çek -- call ve put
    # ayrı ayrı (implied volatility strike+tipe göre farklı olabilir; T/faiz/
    # PSR/VSR ikisinde de aynıdır, hangisi bulunduysa ondan alınır).
    takasbank_call = takasbank_put = None
    if takasbank_series:
        try:
            takasbank_call = takasbank_xml.get_option_params(
                fetched["ticker"], expiry, strike, "call"
            )
        except (KeyError, ValueError):
            pass
        try:
            takasbank_put = takasbank_xml.get_option_params(
                fetched["ticker"], expiry, strike, "put"
            )
        except (KeyError, ValueError):
            pass
    takasbank_common = takasbank_call or takasbank_put

    # Bir strike'ta call VEYA put'tan sadece biri işlem görüyor olabilir
    # (ör. derin ITM/OTM taraf o gün hiç işlem görmemiş). Bu durumda o
    # tarafa TEORİK/UYDURULMUŞ bir değer (historical vol, Black-Scholes
    # fiyatı vb.) UYGULANMAZ -- o taraf tamamen hesaplama dışı bırakılır
    # ve kullanıcıya "bu tarihte işlem görmemektedir" uyarısı gösterilir.
    call_missing = bool(takasbank_series) and takasbank_call is None
    put_missing = bool(takasbank_series) and takasbank_put is None
    if call_missing and put_missing:
        st.caption(
            f"CALL {strike:g} ve PUT {strike:g}, bu pozisyonlar bu tarihte "
            "işlem görmemektedir."
        )
    elif call_missing:
        st.caption(
            f"CALL {strike:g}, bu pozisyon bu tarihte işlem görmemektedir, "
            f"sadece PUT {strike:g} pozisyonu bulunmaktadır."
        )
    elif put_missing:
        st.caption(
            f"PUT {strike:g}, bu pozisyon bu tarihte işlem görmemektedir, "
            f"sadece CALL {strike:g} pozisyonu bulunmaktadır."
        )

    auto_tte = (
        takasbank_common.time_to_expiry
        if takasbank_common
        else (expiry - date.today()).days / 365
    )
    auto_tte_display = round(auto_tte, 4)
    # bkz. _streamlit_override_row -- aynı sebeple "value" değil session_state
    # kullanıyoruz (Değiştir ilk işaretlendiğinde 0'a sıfırlanma bug'ı).
    if not st.session_state.get("tte_chk", False):
        st.session_state["tte_val"] = auto_tte_display

    c1, c2, c3 = st.columns([2.2, 1.5, 1])
    with c1:
        st.markdown("**Vadeye Kalan Süre (T, yıl)**")
        if takasbank_common:
            st.caption(f"Otomatik: {_format_natural(auto_tte)}  ·  _(iş günü/250)_")
        else:
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
    # Değiştir işaretli ama alan hâlâ otomatik değerin yuvarlanmış
    # görüntüsündeyse (kullanıcı gerçekten dokunmadıysa), formüle giden T
    # yine tam hassasiyetli auto_tte olsun -- yuvarlanmış görüntü değil.
    # ÖNEMLİ: Değiştir kapalıyken de None DEĞİL, doğrudan auto_tte
    # (Takasbank XML'in tam hassasiyetli T'si) geçiyoruz -- None geçmek,
    # compute_span_result'ın kendi (daha kaba) takvim-günü fallback'ine
    # sessizce düşmesine yol açardı (bkz. proje sohbet geçmişi: bu tam
    # olarak PC-SPAN'ın Risk Array'inden sapmanın kök nedeniydi).
    if not override_tte:
        time_to_expiry_override = auto_tte
    elif manual_tte == auto_tte_display:
        time_to_expiry_override = auto_tte
    else:
        time_to_expiry_override = manual_tte

    rp = fetched["risk_params"]

    # Kaynak etiketini SADECE Takasbank XML'in DIŞINDAki bir kaynağa
    # (yedek/fallback) düşüldüğünde gösteriyoruz -- Takasbank XML zaten
    # yukarıdaki tek konsolide bilgi bloğunda belirtiliyor, her satırda
    # tekrar yazmıyoruz.
    def _src(label: str) -> str | None:
        return None if label == "Takasbank XML" else label

    with st.expander("Otomatik Çekilen Değerler"):
        spot = _streamlit_override_row(
            st, "Güncel Fiyat (Spot)", spot_auto, "spot", source=_src(spot_source), live=True
        )

        # PSR/VSR/faiz: Takasbank'ın GÜNLÜK XML'i varsa oradan (canlı, bu
        # vadeye özel), yoksa Takasbank PDF'inden (statik referans) --
        # ikisi de aynı kaynaktan (Takasbank) geldiği için kullanıcıya
        # hangisi kullanıldığı etiketle belli edilir.
        if takasbank_common:
            psr_auto, psr_source = takasbank_common.price_scan_range, "Takasbank XML"
            vsr_auto, vsr_source = takasbank_common.volatility_scan_range, "Takasbank XML"
            emm_auto, emm_source = takasbank_common.extreme_move_multiplier, "Takasbank XML"
            emcf_auto, emcf_source = (
                takasbank_common.extreme_move_covered_fraction,
                "Takasbank XML",
            )
            rate_auto, rate_source = takasbank_common.risk_free_rate, "Takasbank XML"
        else:
            psr_auto, psr_source = rp.price_scan_range, "Takasbank dökümanı (PDF)"
            vsr_auto, vsr_source = rp.volatility_scan_range, "Takasbank dökümanı (PDF)"
            emm_auto, emm_source = rp.extreme_move_multiplier, "Takasbank dökümanı (PDF)"
            emcf_auto, emcf_source = (
                rp.extreme_move_covered_fraction,
                "Takasbank dökümanı (PDF)",
            )
            rate_auto, rate_source = 0.45, "varsayılan (Takasbank XML bulunamadı)"

        risk_free_rate = _streamlit_override_row(
            st, "Risksiz Faiz Oranı", rate_auto, "rate", source=_src(rate_source)
        )
        psr = _streamlit_override_row(
            st, "Price Scan Range (PSR)", psr_auto, "psr", source=_src(psr_source), decimals=4
        )
        vsr = _streamlit_override_row(
            st, "Volatility Scan Range (VSR)", vsr_auto, "vsr", source=_src(vsr_source)
        )
        emm = _streamlit_override_row(
            st, "Extreme Move Multiplier", emm_auto, "emm", source=_src(emm_source)
        )
        emcf = _streamlit_override_row(
            st, "Extreme Move Covered Fraction", emcf_auto, "emcf", source=_src(emcf_source)
        )
        som = _streamlit_override_row(
            st,
            "Short Option Minimum (SOM)",
            rp.short_option_minimum,
            "som",
            source="Takasbank dökümanı (PDF)",
        )

        st.markdown("**Volatilite**")
        # NOT: call_missing/put_missing True ise (bu strike'ta o taraf Takasbank
        # verisinde yok -- yani o gün işlem görmemiş), auto/fallback değeri HİÇ
        # hesaplamıyoruz ve satırı HİÇ göstermiyoruz -- teorik/uydurulmuş bir
        # değer üretip kullanıcıyı yanıltmak yerine, o taraf tamamen dışarıda
        # bırakılır (bkz. yukarıdaki uyarı). yfinance historical/teorik BS
        # fallback'i SADECE Takasbank'ta bu hisse için hiç veri yoksa (takasbank_series
        # None) uygulanır -- o farklı bir durumdur (strike-özel eksiklik değil).
        if takasbank_call:
            call_vol_auto, call_vol_source = (
                takasbank_call.implied_volatility,
                "Takasbank XML",
            )
        elif not call_missing:
            call_vol_auto, call_vol_source = (
                fetched["volatility"],
                "yfinance historical (IV bulunamadı)",
            )
        else:
            call_vol_auto = call_vol_source = None
        if takasbank_put:
            put_vol_auto, put_vol_source = (
                takasbank_put.implied_volatility,
                "Takasbank XML",
            )
        elif not put_missing:
            put_vol_auto, put_vol_source = (
                fetched["volatility"],
                "yfinance historical (IV bulunamadı)",
            )
        else:
            put_vol_auto = put_vol_source = None

        call_volatility = (
            _streamlit_override_row(
                st, "Volatilite — Call", call_vol_auto, "call_vol", source=_src(call_vol_source)
            )
            if not call_missing
            else None
        )
        put_volatility = (
            _streamlit_override_row(
                st, "Volatilite — Put", put_vol_auto, "put_vol", source=_src(put_vol_source)
            )
            if not put_missing
            else None
        )

        # Taban fiyat: 16 senaryonun "Fark" hesabında Black-Scholes ile
        # hesaplanan YENİ (şoklu) fiyattan çıkarılan taban. Takasbank XML'de
        # <opt><p> olarak gelen GERÇEK piyasa fiyatı varsa o kullanılır (PC-SPAN
        # Risk Array ekranıyla birebir örtüşmesi için); yoksa (ve o taraf
        # gerçekten işlem görüyorsa, sadece piyasa fiyatı XML'de yoksa) kendi
        # teorik Black-Scholes fiyatımıza düşülür. call_missing/put_missing
        # True ise (o taraf bu tarihte hiç işlem görmemişse) bu satır da HİÇ
        # gösterilmez -- teorik bir fiyat uydurmuyoruz.
        effective_tte = (
            time_to_expiry_override if time_to_expiry_override is not None else auto_tte
        )
        theoretical_call_price = (
            black_scholes_price(spot, strike, effective_tte, risk_free_rate, call_volatility, "call")
            if not call_missing
            else None
        )
        theoretical_put_price = (
            black_scholes_price(spot, strike, effective_tte, risk_free_rate, put_volatility, "put")
            if not put_missing
            else None
        )
        if takasbank_call:
            call_base_auto, call_base_source = takasbank_call.market_price, "Takasbank XML"
        elif not call_missing:
            call_base_auto, call_base_source = (
                theoretical_call_price,
                "teorik Black-Scholes (Takasbank piyasa fiyatı bulunamadı)",
            )
        else:
            call_base_auto = call_base_source = None
        if takasbank_put:
            put_base_auto, put_base_source = takasbank_put.market_price, "Takasbank XML"
        elif not put_missing:
            put_base_auto, put_base_source = (
                theoretical_put_price,
                "teorik Black-Scholes (Takasbank piyasa fiyatı bulunamadı)",
            )
        else:
            put_base_auto = put_base_source = None

        st.markdown("**Call/Put Opsiyon Uzlaşma Fiyatı**")
        call_market_price = (
            _streamlit_override_row(
                st, "Taban Fiyat — Call", call_base_auto, "call_base", source=_src(call_base_source)
            )
            if not call_missing
            else None
        )
        put_market_price = (
            _streamlit_override_row(
                st, "Taban Fiyat — Put", put_base_auto, "put_base", source=_src(put_base_source)
            )
            if not put_missing
            else None
        )

        st.divider()
        icsc_help = (
            f"Takasbank'ın {fetched['ticker']} için yayınladığı referans değer: "
            f"{rp.intra_commodity_spread_charge:,.2f} TL / spread birimi. Bu "
            "ücret SADECE aynı dayanak varlıkta birden fazla vadeli gerçek "
            "bir spread pozisyonun varsa uygulanır. Aşağıdaki tek bacaklı/"
            "tek vadeli pozisyon için doğru değer 0'dır — spread "
            "pozisyonun olduğunu biliyorsan alanı değiştir."
        )
        icsc = st.number_input(
            "Vadeler Arası Spread Ücreti (Intra-Commodity Spread Charge, TL)",
            value=0.0,
            min_value=0.0,
            step=0.0001,
            format="%.4f",
            key="icsc_applied",
            help=icsc_help,
        )

    if st.button("Hesapla", type="primary"):
        if call_missing and put_missing:
            st.error(
                "Ne CALL ne de PUT bu strike/vade için Takasbank verisinde "
                "bulunuyor — bu pozisyon bu tarihte işlem görmemektedir, "
                "hesaplama yapılamaz."
            )
            st.session_state.pop("results", None)
        else:
            base_kwargs = dict(
                ticker=fetched["ticker"],
                strike=strike,
                contracts=int(contracts),
                expiry=expiry,
                risk_params_file=Path(risk_params_file),
                risk_free_rate=risk_free_rate,
                spot_override=spot,
                time_to_expiry_override=time_to_expiry_override,
                price_scan_range_override=psr,
                volatility_scan_range_override=vsr,
                extreme_move_multiplier_override=emm,
                extreme_move_covered_fraction_override=emcf,
                intra_commodity_spread_charge_override=icsc,
                short_option_minimum_override=som,
            )
            # NOT: Opsiyon Prim Değeri (Madde 37/3) Streamlit arayüzünde
            # KASITLI olarak sunulmuyor -- kullanıcıyla değerlendirildi
            # (bkz. proje sohbet geçmişi): bu hesaplayıcı "TOPLAM ne kadar
            # teminat gerekli" sorusuna cevap veriyor, "elimde zaten duran
            # prim nakdi düşülünce EK olarak ne kadar yatırmam gerekir"
            # sorusuna değil. Bu yüzden position_opened_today/
            # execution_price_override her zaman varsayılan (False/None)
            # kalır -- option_premium_value her zaman 0. CLI'da hâlâ
            # --position-opened-today/--execution-price ile isteyen
            # kullanabilir (span_engine formülü Madde 37/3'ü tam
            # destekliyor), sadece web arayüzünde gösterilmiyor.
            try:
                new_results = {}
                # Sadece o tarihte GERÇEKTEN işlem gören taraf(lar) hesaplanır
                # -- call_missing/put_missing True olan taraf için hiç
                # compute_span_result çağrılmıyor, teorik bir sonuç üretilmiyor.
                if not call_missing:
                    call_inputs = SpanCalculationInput(
                        **base_kwargs,
                        option_type="call",
                        volatility_override=call_volatility,
                        market_price_override=call_market_price,
                    )
                    new_results["call"] = compute_span_result(call_inputs)
                if not put_missing:
                    put_inputs = SpanCalculationInput(
                        **base_kwargs,
                        option_type="put",
                        volatility_override=put_volatility,
                        market_price_override=put_market_price,
                    )
                    new_results["put"] = compute_span_result(put_inputs)
                st.session_state["results"] = new_results
                st.session_state["results_inputs"] = SpanCalculationInput(
                    **base_kwargs, option_type="call"
                )
                st.session_state["call_missing"] = call_missing
                st.session_state["put_missing"] = put_missing
            except Exception as exc:
                st.error(str(exc))
                st.session_state.pop("results", None)

    results = st.session_state.get("results")
    if results is None:
        return
    result_inputs = st.session_state["results_inputs"]

    def _margin_breakdown(col, span: dict) -> None:
        """Min. Teminat kartının altına Takasbank'ın resmi formülünün
        (Merkezi Karşı Taraf Hizmeti ve Takas Esasları Prosedürü, Madde
        33-38 -- bkz. proje sohbet geçmişi) bileşenlerini küçük, bilgi
        ikonlu satırlarla döker: SPAN Risk, NOV (Net Opsiyon Değeri);
        en altta bunların toplamı.

        Gerçek PC-SPAN çıktısıyla doğrulanmıştır (THYAO 30.09.2026 K=300
        CALL: SPAN Risk 3.697, Available Net Option (2.102), Total
        Requirement 5.799 -- kuruşuna kadar örtüştü).

        NOT: Opsiyon Prim Değeri (Madde 37/3) BİLİNÇLİ olarak burada
        gösterilmiyor -- kullanıcıyla değerlendirildi (bkz. proje sohbet
        geçmişi): bu hesaplayıcı "TOPLAM ne kadar teminat gerekli"
        sorusuna cevap veriyor. Opsiyon satışından tahsil edilen prim
        zaten hesapta nakit olarak durur; onu ayrıca "düşmek", teminat
        SEVİYESİNİ değil, "elimdeki nakit üzerine EK olarak ne kadar
        yatırmam gerekir" sorusunu cevaplar -- bu araç o soruyu sormuyor.
        Bunun yerine altta sabit bir bilgilendirme notu gösteriliyor.
        """
        scan_component = (
            span["scan_risk"]
            + span["intra_commodity_spread_charge"]
            + span["delivery_risk"]
            - span["inter_commodity_spread_credit"]
        )
        bistech_margin_risk = max(span["short_option_minimum"], scan_component)
        span_risk_help = "16 SPAN senaryosundan en kötüsü (Scanning Risk)."
        if span["short_option_minimum"] > scan_component:
            span_risk_help += (
                f" Bu pozisyonda Scanning Risk ({span['scan_risk']:,.2f} TL), Short "
                f"Option Minimum'un ({span['short_option_minimum']:,.2f} TL) altında "
                "kaldığı için SOM tabanı uygulandı."
            )
        col.caption(f"SPAN Risk: {bistech_margin_risk:,.2f} TL", help=span_risk_help)

        nov_contribution = -span["net_option_value"]
        col.caption(
            f"NOV: {nov_contribution:+,.2f} TL",
            help=(
                "Net Opsiyon Değeri (Madde 37/2): bu kısa opsiyonu ŞU AN "
                "geri satın alma maliyeti (|kontrat| × piyasa fiyatı × "
                "kontrat çarpanı). Kısa pozisyon için her zaman teminata "
                "EKLENİR -- Takasbank'ın resmi ekranında 'Available Net "
                "Option' olarak geçer."
            ),
        )
        col.caption(f"**Toplam: {span['total_initial_margin']:,.2f} TL**")
        col.caption(
            "Gösterilen tutar, BISTECH/SPAN riski ve kısa opsiyonun güncel "
            "değeri dikkate alınarak hesaplanmıştır. Opsiyon satışından "
            "elde edilen prim bu hesaplamaya dahil edilmemiştir. İşlem "
            "gününde tahsil edilen opsiyon primi, Takasbank hesaplamasında "
            "başlangıç teminatı ihtiyacını azaltabilir."
        )

    st.divider()
    m1, m2 = st.columns(2)
    if "call" in results:
        m1.metric(
            "Call — Min. Teminat",
            f"{results['call']['span']['total_initial_margin']:,.2f} TL",
        )
        _margin_breakdown(m1, results["call"]["span"])
    else:
        m1.warning("CALL bu tarihte işlem görmemektedir.")
    if "put" in results:
        m2.metric(
            "Put — Min. Teminat",
            f"{results['put']['span']['total_initial_margin']:,.2f} TL",
        )
        _margin_breakdown(m2, results["put"]["span"])
    else:
        m2.warning("PUT bu tarihte işlem görmemektedir.")

    with st.expander("Aracı Kurumların Takasbank Minimum Teminatına Uyguladığı Çarpanlar"):
        st.caption(
            "Takasbank, VİOP'ta işlem gören her kontrat için SPAN bazlı asgari "
            "(minimum) teminat tutarlarını belirler ve yayınlar. Ancak aracı "
            "kurumlar, kendi risk yönetimi politikaları gereği bu asgari "
            "tutarın üzerine ek bir güvenlik marjı koyabilir. Tespit edilen "
            "bazı aracı kurumların uyguladığı çarpanlar:"
        )
        st.caption(
            "- [Garanti BBVA Yatırım](https://www.garantibbvayatirim.com.tr/urunlerimiz/viop): 2x Min Teminat\n"
            "- [Ziraat Yatırım](https://www.ziraatyatirim.com.tr/tr/turev-araclar-v%C4%B1op): 2,00x Min Teminat\n"
            "- [Fiba Yatırım](https://www.fibayatirim.com.tr/viop-teminat-tamamlama-span-carpani-ve-stop-out-uygulamasi-hakkinda-bilgilendirme): "
            "1,5x Min Teminat (Takasbank'ın güncel SPAN parametreleri üzerinden)\n"
            "- [Tacirler Yatırım](https://tacirler.com.tr/viop-teminat-rasyolarinin-guncellenmesi-hk-02-01-2025): "
            "1x — Takasbank'ın uyguladığı oranları doğrudan kullanıyor, ek çarpan yok "
            "(kaynak Ocak 2025 tarihli, teyide açık)\n"
            "- [Osmanlı Menkul](https://www.osmanlimenkul.com.tr/hisse-ve-viop/hisse-ve-viop-urunlerimiz/hisse-turev/viop-teminat-ve-limit-bilgileri): "
            "kullanılan teminat 7.500.000 TL eşiğini aştığında kademeli çarpan uygulanıyor "
            "(tam sayısal değer sayfada belirtilmiyor, dosyaya bağlı)\n"
            "- [IKON Menkul](http://www.ikonmenkul.com.tr/viop-baslangic-teminatlari): "
            "Takasbank oranlarına piyasa koşullarına göre değişken \"Ek Teminat\" uyguluyor "
            "(sabit bir çarpan belirtilmiyor)"
        )

    if st.button("Bileşenler"):
        st.session_state["show_components"] = not st.session_state.get(
            "show_components", False
        )
    if st.session_state.get("show_components", False):
        if "call" in results and "put" in results:
            table = _format_comparison_table(result_inputs, results["call"], results["put"])
        else:
            side, result = next(iter(results.items()))
            side_inputs = replace(result_inputs, option_type=side)
            table = _format_result_table(side_inputs, result)
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
        if "call" in results:
            st.markdown(
                f"**Call** — en kötü senaryo: "
                f"#{results['call']['scenarios'].attrs['worst_scenario_no']}"
            )
            st.table(
                _scenario_display_table(results["call"]["scenarios"], "Call Fiyatı")
            )
        else:
            st.caption("CALL bu tarihte işlem görmemektedir — senaryo tablosu yok.")
    with sc2:
        if "put" in results:
            st.markdown(
                f"**Put** — en kötü senaryo: "
                f"#{results['put']['scenarios'].attrs['worst_scenario_no']}"
            )
            st.table(
                _scenario_display_table(results["put"]["scenarios"], "Put Fiyatı")
            )
        else:
            st.caption("PUT bu tarihte işlem görmemektedir — senaryo tablosu yok.")


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
