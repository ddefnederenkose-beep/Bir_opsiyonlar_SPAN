"""Takasbank Günlük PC-SPAN Risk Parametre XML Katmanı.

Takasbank, her iş günü şu klasörde PC-SPAN 4.0 formatında risk parametre
dosyaları yayınlar:

    https://wwwdata.takasbank.com.tr/pardosya/Prod/YYMMDD/

Klasör içinde iki tür dosya bulunur:
- ``TAKASINT_...-NNN.zip``: gün içi (intraday), günde birçok kez, saatlik
  yayınlanan ara güncellemeler.
- ``TAKASEOD_...-001.zip``: gün sonu (End Of Day), o gün için TEK ve
  NİHAİ dosya (piyasa kapandıktan sonra, ~20:30 civarı yayınlanır).

Hafta sonu ve resmi tatillerde klasör hiç oluşmadığı için "son mevcut
klasörü bul" mantığı otomatik olarak "son iş günü" sorununu çözer --
ayrı bir tatil takvimi tutmaya gerek yoktur.

Bu modül şunu yapar: en son mevcut günü bulur, o günün en güncel/nihai
dosyasını (EOD varsa o, yoksa en yüksek numaralı INT) indirir, tek
seferlik bir tarama ile TÜM hisse opsiyon ürünlerinin (``valueMeth=EQTY``)
T/faiz oranı/PSR/VSR/implied volatility değerlerini ve global Extreme
Move Multiplier/Covered Fraction değerlerini damıtılmış (distilled) küçük
bir JSON'a çıkarır, diske cache'ler. Aynı gün için tekrar çağrılırsa
diskteki JSON'dan okur, yeniden indirip parse etmez.

XML yapısı hakkında keşif notları (gerçek bir Takasbank EOD dosyası
üzerinde doğrulanmıştır -- bkz. proje sohbet geçmişi):

- ``spanFile/definitions/pointDef[r]/scanPointDef``: 16 SPAN senaryosunun
  GLOBAL tanımı (ürün bağımsız, sadece scanRate/r'ye göre gruplanır).
  Hisse opsiyonlarının hepsi r=1 kullanır (ASELS/GARAN/THYAO/AKBNK ile
  doğrulanmıştır). point=15 (extreme up): priceScanDef.mult = Extreme
  Move Multiplier, weight = Extreme Move Covered Fraction,
  volScanDef.mult = 0.0 (extreme senaryolarda volatilite şoklanmaz --
  bkz. span_engine.generate_risk_scenarios docstring'i).
- ``spanFile/exchange/oofPf/oopPf``: hisse başına bir kayıt
  (``pfCode``, ``valueMeth=EQTY`` hisse opsiyonlarını işaretler).
  - ``series``: vade başına bir kayıt (``pe``=vade tarihi YYYYMMDD,
    ``t``=T yıl cinsinden HAZIR, ``intrRate/val``=faiz oranı ondalık,
    ``scanRate/priceScanPct``=PSR YÜZDE olarak (15.7 -> /100),
    ``scanRate/volScanPct``=VSR ZATEN ondalık (0.31, /100 GEREKMEZ --
    isim yanıltıcı, PDF'teki VSR="31" değerinin /100'lenmiş hâliyle
    birebir eşleşir)).
    - ``opt``: strike başına bir kayıt (``k``=strike, ``o``=C/P,
      ``v``=implied volatility ondalık, ``p``=piyasa/uzlaşma fiyatı --
      BU PROJEDE KULLANILMIYOR, opsiyon fiyatı hâlâ kendi Black-Scholes
      hesabımızdan geliyor).

Kesinlikle KULLANILMAYAN alanlar (kasıtlı, kullanıcı talimatıyla):
- Spot/underlying fiyatı: yfinance'ten gelmeye devam eder.
- ``opt/p`` (piyasa/uzlaşma fiyatı): Black-Scholes taban fiyatının
  yerini almaz.

SOM (Short Option Minimum) ve Intra-Commodity Spread Charge bu
katmandan GELMEZ -- keşif sırasında bunların ürün başına düz bir
değer olmadığı, ayrı bir "Combined Commodity" (``ccDef``) bloğunda
tier tabanlı bir oran tablosu + hesaplama metodolojisi (``somMeth``)
olarak tanımlandığı görüldü. Bunu yanlış çözüp sessizce hatalı teminat
üretme riskini almamak için SOM/Intra-Commodity için hâlâ
``risk_params.py``'nin Takasbank PDF parser'ı kullanılıyor.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

BASE_URL = "https://wwwdata.takasbank.com.tr/pardosya/Prod"

# Ham ZIP/XML ve damıtılmış JSON cache'lerinin tutulacağı dizinler.
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache" / "takasbank"
RAW_DIR = CACHE_DIR / "raw"
DISTILLED_DIR = CACHE_DIR / "distilled"

# Hisse opsiyonlarının PC-SPAN scanPointDef gruplaması (bkz. modül
# docstring'i -- ASELS/GARAN/THYAO/AKBNK ile doğrulanmıştır).
_EQUITY_SCAN_GROUP_R = "1"
_EXTREME_MOVE_POINT = "15"

_HTTP_TIMEOUT = 30


@dataclass
class TakasbankOptionParams:
    """Bir hisse/vade/strike/tip için Takasbank'ın günlük PC-SPAN
    dosyasından otomatik çekilen parametreler.

    Attributes:
        ticker: BIST sembolü (ör. "AKBNK").
        expiry: Vade tarihi.
        strike: Kullanım fiyatı.
        option_type: "call" veya "put".
        time_to_expiry: T, yıl cinsinden (XML'in <t> alanından, hazır).
        risk_free_rate: Faiz oranı, ondalık (ör. 0.38).
        implied_volatility: Bu strike/tip için implied volatility, ondalık.
        price_scan_range: PSR, ondalık (ör. 0.157).
        volatility_scan_range: VSR, ondalık (ör. 0.31).
        extreme_move_multiplier: Extreme Move Multiplier (global, ör. 3.0).
        extreme_move_covered_fraction: Extreme Move Covered Fraction
            (global, ör. 0.32).
        source_date: Bu verinin ait olduğu Takasbank iş günü.
    """

    ticker: str
    expiry: date
    strike: float
    option_type: str
    time_to_expiry: float
    risk_free_rate: float
    implied_volatility: float
    price_scan_range: float
    volatility_scan_range: float
    extreme_move_multiplier: float
    extreme_move_covered_fraction: float
    source_date: date


def _folder_url(d: date) -> str:
    return f"{BASE_URL}/{d.strftime('%y%m%d')}/"


def find_latest_trading_day(
    today: date | None = None, max_lookback: int = 10
) -> date:
    """En son klasörü mevcut olan günü bulur (hafta sonu/tatil otomatik atlanır).

    Args:
        today: Aramanın başlayacağı gün (varsayılan: bugün).
        max_lookback: En fazla kaç gün geriye gidileceği (güvenlik sınırı).

    Returns:
        Klasörü mevcut olan en güncel gün.

    Raises:
        RuntimeError: max_lookback gün içinde hiçbir klasör bulunamazsa.
    """
    today = today or date.today()
    for offset in range(max_lookback):
        d = today - timedelta(days=offset)
        try:
            resp = requests.head(_folder_url(d), timeout=_HTTP_TIMEOUT)
        except requests.RequestException:
            continue
        if resp.status_code == 200:
            return d
    raise RuntimeError(
        f"Son {max_lookback} gün içinde Takasbank klasörü bulunamadı "
        f"(bugün: {today})"
    )


def _list_directory_files(d: date) -> list[str]:
    """Bir gün klasöründeki .zip dosya adlarını (belgede geçtiği sırayla) döner."""
    resp = requests.get(_folder_url(d), timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    return re.findall(r'HREF="[^"]*/(TAKAS[^"]+\.zip)"', resp.text)


def _pick_best_file(filenames: list[str]) -> str:
    """EOD dosyası varsa onu, yoksa en yüksek numaralı INT dosyasını seçer.

    Raises:
        ValueError: Hiç dosya verilmemişse.
    """
    if not filenames:
        raise ValueError("Seçilecek dosya yok")

    eod_files = [f for f in filenames if f.startswith("TAKASEOD")]
    if eod_files:
        return sorted(eod_files)[-1]

    int_files = [f for f in filenames if f.startswith("TAKASINT")]
    if int_files:

        def _seq_no(name: str) -> int:
            match = re.search(r"-(\d+)\.zip$", name)
            return int(match.group(1)) if match else -1

        return max(int_files, key=_seq_no)

    # Beklenmeyen bir isimlendirme varsa alfabetik son dosyaya düş.
    return sorted(filenames)[-1]


def _download_and_extract_xml(d: date, filename: str) -> Path:
    """ZIP'i indirir (daha önce indirilmediyse), içindeki tek XML'i çıkarır.

    Returns:
        Çıkarılan XML dosyasının yolu.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    xml_path = RAW_DIR / f"{d.strftime('%y%m%d')}.xml"
    if xml_path.exists():
        return xml_path

    url = f"{_folder_url(d)}{filename}"
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        if len(names) != 1:
            raise ValueError(
                f"{filename} içinde tam olarak 1 dosya bekleniyordu, {len(names)} bulundu"
            )
        with zf.open(names[0]) as src, open(xml_path, "wb") as dst:
            dst.write(src.read())

    return xml_path


def _parse_global_extreme_move(xml_path: Path) -> tuple[float, float]:
    """Global Extreme Move Multiplier ve Covered Fraction'ı bulur.

    spanFile/definitions/pointDef[r=1]/scanPointDef[point=15]'ten:
    priceScanDef/mult = multiplier, weight = covered_fraction.
    """
    # NOT: pointDef DIŞINDAKI elemanları burada temizlemiyoruz -- henüz
    # kapanmamış bir üst elemanın (ör. scanPointDef içindeki <point>)
    # verisini erken silmek yanlış sonuca yol açar (bkz. modülün keşif
    # sürecinde yaşanan gerçek bug). Sadece işimiz bitmiş pointDef'leri
    # temizliyoruz; doğru r'yi dosyadaki sırasına güvenmeden buluyoruz.
    context = ET.iterparse(str(xml_path), events=("end",))
    for _event, elem in context:
        if elem.tag != "pointDef":
            continue
        if elem.findtext("r") == _EQUITY_SCAN_GROUP_R:
            for spd in elem.findall("scanPointDef"):
                if spd.findtext("point") == _EXTREME_MOVE_POINT:
                    multiplier = float(spd.findtext("priceScanDef/mult"))
                    covered_fraction = float(spd.findtext("weight"))
                    return multiplier, covered_fraction
        elem.clear()
    raise ValueError(
        f"{xml_path} içinde pointDef[r={_EQUITY_SCAN_GROUP_R}]/"
        f"scanPointDef[point={_EXTREME_MOVE_POINT}] bulunamadı"
    )


def _parse_products(xml_path: Path) -> dict:
    """Tüm hisse opsiyon ürünlerini (valueMeth=EQTY) tek geçişte damıtır.

    Returns:
        {ticker: {expiry_yyyymmdd: {"t", "intrRate", "psr", "vsr",
        "options": [{"k", "o", "v"}, ...]}}}
    """
    products: dict[str, dict] = {}
    context = ET.iterparse(str(xml_path), events=("end",))
    for _event, elem in context:
        if elem.tag != "oopPf":
            continue
        try:
            if elem.findtext("valueMeth") != "EQTY":
                continue
            ticker = elem.findtext("pfCode")
            if not ticker:
                continue

            by_expiry: dict[str, dict] = {}
            for series in elem.findall("series"):
                expiry_str = series.findtext("pe")
                t_val = series.findtext("t")
                r_val = series.findtext("intrRate/val")
                psr_val = series.findtext("scanRate/priceScanPct")
                vsr_val = series.findtext("scanRate/volScanPct")
                if not (expiry_str and t_val and r_val and psr_val and vsr_val):
                    continue

                options = []
                for opt in series.findall("opt"):
                    k = opt.findtext("k")
                    o = opt.findtext("o")
                    v = opt.findtext("v")
                    if k and o and v:
                        options.append({"k": float(k), "o": o, "v": float(v)})

                by_expiry[expiry_str] = {
                    "t": float(t_val),
                    "intrRate": float(r_val),
                    "psr": float(psr_val) / 100.0,
                    "vsr": float(vsr_val),  # ZATEN ondalık, /100 GEREKMEZ
                    "options": options,
                }

            if by_expiry:
                products[ticker] = by_expiry
        finally:
            elem.clear()

    return products


def build_distilled_cache(xml_path: Path) -> dict:
    """XML'i tarayıp damıtılmış (küçük, hızlı yüklenen) bir sözlük üretir."""
    emm, ecf = _parse_global_extreme_move(xml_path)
    products = _parse_products(xml_path)
    return {
        "global": {
            "extreme_move_multiplier": emm,
            "extreme_move_covered_fraction": ecf,
        },
        "products": products,
    }


def _distilled_cache_path(d: date) -> Path:
    return DISTILLED_DIR / f"{d.strftime('%y%m%d')}.json"


def ensure_daily_cache(today: date | None = None) -> tuple[date, Path]:
    """En son iş gününün damıtılmış cache'inin var olduğundan emin olur.

    Zaten cache'lenmişse yeniden indirmez/parse etmez. Değilse: en son
    mevcut Takasbank klasörünü bulur, en güncel dosyasını indirir, parse
    eder ve JSON olarak cache'ler.

    Returns:
        (o günün tarihi, damıtılmış JSON dosyasının yolu)
    """
    trading_day = find_latest_trading_day(today)
    cache_path = _distilled_cache_path(trading_day)
    if cache_path.exists():
        return trading_day, cache_path

    files = _list_directory_files(trading_day)
    best_file = _pick_best_file(files)
    xml_path = _download_and_extract_xml(trading_day, best_file)
    distilled = build_distilled_cache(xml_path)

    DISTILLED_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(distilled, ensure_ascii=False))
    return trading_day, cache_path


def last_update_info() -> dict | None:
    """Diskteki en güncel damıtılmış cache'in tarihini döner (UI'da göstermek için).

    Returns:
        {"source_date": date, "cached_at": datetime} ya da hiç cache yoksa None.
    """
    if not DISTILLED_DIR.exists():
        return None
    cache_files = sorted(DISTILLED_DIR.glob("*.json"))
    if not cache_files:
        return None
    latest = cache_files[-1]
    d = date(2000 + int(latest.stem[:2]), int(latest.stem[2:4]), int(latest.stem[4:6]))
    return {
        "source_date": d,
        "cached_at": __import__("datetime").datetime.fromtimestamp(
            latest.stat().st_mtime
        ),
    }


def get_option_params(
    ticker: str,
    expiry: date,
    strike: float,
    option_type: str,
    today: date | None = None,
) -> TakasbankOptionParams:
    """Verilen hisse/vade/strike/tip için Takasbank'ın günlük PC-SPAN
    dosyasından T, faiz oranı, implied volatility, PSR, VSR, Extreme Move
    Multiplier/Covered Fraction değerlerini çeker.

    Args:
        ticker: BIST sembolü (".IS" soneki otomatik temizlenir).
        expiry: Vade tarihi.
        strike: Kullanım fiyatı.
        option_type: "call" veya "put".
        today: Test/override amaçlı "bugün" tarihi (varsayılan: gerçek bugün).

    Returns:
        TakasbankOptionParams.

    Raises:
        KeyError: Hisse ya da vade dosyada bulunamazsa (mesaj, mevcut
            vadeleri/strike'ları listeler).
        ValueError: Strike/tip kombinasyonu bulunamazsa.
    """
    normalized_ticker = ticker.upper().strip().removesuffix(".IS")
    trading_day, cache_path = ensure_daily_cache(today)
    distilled = json.loads(cache_path.read_text())

    products = distilled["products"]
    if normalized_ticker not in products:
        raise KeyError(
            f"{normalized_ticker}, {trading_day} tarihli Takasbank dosyasında "
            f"bulunamadı (opsiyonu olmayabilir)"
        )

    expiry_str = expiry.strftime("%Y%m%d")
    by_expiry = products[normalized_ticker]
    if expiry_str not in by_expiry:
        available = ", ".join(sorted(by_expiry))
        raise KeyError(
            f"{normalized_ticker} için {expiry_str} vadesi bulunamadı. "
            f"Mevcut vadeler: {available}"
        )

    series_data = by_expiry[expiry_str]
    target_code = "C" if option_type == "call" else "P"
    for opt in series_data["options"]:
        if opt["o"] == target_code and abs(opt["k"] - strike) < 1e-9:
            return TakasbankOptionParams(
                ticker=normalized_ticker,
                expiry=expiry,
                strike=strike,
                option_type=option_type,
                time_to_expiry=series_data["t"],
                risk_free_rate=series_data["intrRate"],
                implied_volatility=opt["v"],
                price_scan_range=series_data["psr"],
                volatility_scan_range=series_data["vsr"],
                extreme_move_multiplier=distilled["global"]["extreme_move_multiplier"],
                extreme_move_covered_fraction=distilled["global"][
                    "extreme_move_covered_fraction"
                ],
                source_date=trading_day,
            )

    available_strikes = sorted(
        {opt["k"] for opt in series_data["options"] if opt["o"] == target_code}
    )
    raise ValueError(
        f"{normalized_ticker} {expiry_str} {option_type} için strike={strike} "
        f"bulunamadı. Mevcut strike'lar: {available_strikes}"
    )
