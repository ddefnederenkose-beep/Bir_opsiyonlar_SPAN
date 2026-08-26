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
T/faiz oranı/PSR/VSR/implied volatility/piyasa fiyatı değerlerini, TÜM
hisselerin spot/underlying fiyatlarını ve global Extreme Move
Multiplier/Covered Fraction değerlerini damıtılmış (distilled) küçük bir
JSON'a çıkarır, diske cache'ler. Aynı gün için tekrar çağrılırsa
diskteki JSON'dan okur, yeniden indirip parse etmez.

XML yapısı hakkında keşif notları (gerçek bir Takasbank dosyası üzerinde
doğrulanmıştır -- bkz. proje sohbet geçmişi):

- ``spanFile/definitions/pointDef[r]/scanPointDef``: 16 SPAN senaryosunun
  GLOBAL tanımı (ürün bağımsız, sadece scanRate/r'ye göre gruplanır).
  Hisse opsiyonlarının hepsi r=1 kullanır (ASELS/GARAN/THYAO/AKBNK ile
  doğrulanmıştır). point=15 (extreme up): priceScanDef.mult = Extreme
  Move Multiplier, weight = Extreme Move Covered Fraction,
  volScanDef.mult = 0.0 (extreme senaryolarda volatilite şoklanmaz --
  bkz. span_engine.generate_risk_scenarios docstring'i).
- ``spanFile/exchange/.../phyPf``: hisse başına BİR kayıt, dayanak
  varlığın kendisi için (``pfCode``, ``phy/p``=güncel spot fiyat,
  örn. AKBNK için 74.3). Bu artık spot fiyatın ANA kaynağıdır
  (yfinance yalnızca opsiyonel karşılaştırma/fallback amaçlı).
- ``spanFile/exchange/oofPf/oopPf``: hisse başına bir kayıt
  (``pfCode``, ``valueMeth=EQTY`` hisse opsiyonlarını işaretler).
  - ``series``: vade başına bir kayıt (``pe``=vade tarihi YYYYMMDD,
    ``t``=T yıl cinsinden HAZIR, ``intrRate/val``=faiz oranı ondalık,
    ``scanRate/priceScanPct``=PSR YÜZDE olarak (15.7 -> /100),
    ``scanRate/volScanPct``=VSR ZATEN ondalık (0.31, /100 GEREKMEZ --
    isim yanıltıcı, PDF'teki VSR="31" değerinin /100'lenmiş hâliyle
    birebir eşleşir)).
    - ``opt``: strike başına bir kayıt (``k``=strike, ``o``=C/P,
      ``v``=implied volatility ondalık, ``p``=piyasa/uzlaşma fiyatı,
      örn. AKBNK K=74 call için 1.72). Bu artık SPAN'in 16 senaryosundaki
      "Fark" hesabının taban fiyatıdır (bkz. span_engine.
      calculate_scenario_pnl'in base_price parametresi) -- kendi
      Black-Scholes'umuzla hesaplanan TEORİK fiyatın yerini alır; teorik
      fiyat artık sadece referans/karşılaştırma amaçlı ayrıca tutulur.

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
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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

# Bazı endeks/döviz ürünlerinde, Takasbank'ın PDF risk parametre
# dosyasındaki (SOM/Intra-Commodity Spread Charge kaynağı) TEMEL sembol
# (ör. "XU030", "USDTRY") ile günlük XML'deki GERÇEK opsiyon ürün kodu
# (pfCode, ör. "XU030D", "USDTRYKP") FARKLIDIR -- ikisi aynı dayanak
# varlığın opsiyonlarını temsil eder, sadece iki farklı Takasbank
# yayınında iki farklı isimle geçer (gerçek XML'de doğrulanmıştır: PSR/
# VSR değerleri PDF'teki "temel" isimle birebir eşleşiyor). Kullanıcıya
# ve PDF'e (risk_params) HER ZAMAN temel ismi gösteriyoruz/kullanıyoruz;
# günlük XML'e sorarken sessizce gerçek pfCode'a çeviriyoruz. Düz
# "USDTRY" ve "XU030" pfCode'ları günlük XML'de SADECE dayanak varlık
# (phyPf, referans fiyat) olarak var -- kendi opsiyon serileri yok.
_XML_PFCODE_ALIASES = {
    "XU030": "XU030D",
    "USDTRY": "USDTRYKP",
}


def to_xml_pfcode(ticker: str) -> str:
    """PDF/görüntü ismini (varsa bir eşleşme varsa) gerçek XML pfCode'una çevirir."""
    return _XML_PFCODE_ALIASES.get(ticker, ticker)


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
        market_price: Bu kontratın Takasbank'ın yayınladığı gerçek
            piyasa/uzlaşma fiyatı (<opt><p>). SPAN'in 16 senaryo
            hesabında "taban" olarak kullanılır (bkz. span_engine.
            calculate_scenario_pnl'in base_price parametresi).
        contract_size: Kontrat başına dayanak varlık miktarı (XML'in
            <opt><cvf> alanından, hazır). Hisse opsiyonlarında 100,
            ama SABİT DEĞİL -- ör. XU030D (BIST30 endeks opsiyonu) 10,
            USDTRYKP (USD/TRY opsiyonu) 1 kullanır. calculate_scenario_pnl
            içinde P&L, (şoklu fiyat - taban) * contracts * contract_size
            formülüyle hesaplanır -- bu alan yanlışsa teminat da yanlış
            çıkar (bkz. proje sohbet geçmişi: hisse opsiyonlarında ×100
            eksikliği daha önce tam olarak bu sınıf bir buga yol açmıştı).
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
    market_price: float
    contract_size: float
    source_date: date


def folder_url(d: date) -> str:
    """Bir günün Takasbank klasör URL'ini döner (UI'da kaynak linki göstermek için de kullanılır)."""
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
            resp = requests.head(folder_url(d), timeout=_HTTP_TIMEOUT)
        except requests.RequestException:
            continue
        if resp.status_code == 200:
            return d
    raise RuntimeError(
        f"Son {max_lookback} gün içinde Takasbank klasörü bulunamadı "
        f"(bugün: {today})"
    )


_IIS_DIR_ENTRY_RE = re.compile(
    r'([A-Za-z]+, [A-Za-z]+ \d{1,2}, \d{4}\s+\d{1,2}:\d{2} [AP]M)'
    r'\s+\d+\s*<A HREF="[^"]*">(TAKAS[^<]+\.zip)</A>'
)
_IIS_TIMESTAMP_FORMAT = "%A, %B %d, %Y %I:%M %p"


def _list_directory_files(d: date) -> dict[str, datetime]:
    """Bir gün klasöründeki .zip dosyalarını, Takasbank'ın kendi sunucu
    dizin listesinde gösterdiği YAYIN zaman damgasıyla birlikte döner.

    Bu zaman damgası, dosyanın Takasbank tarafından o klasöre ne zaman
    yüklendiğini gösterir -- BİZİM onu ne zaman indirdiğimizi değil
    (o ayrı bir kavram, bkz. ensure_daily_cache/last_update_info'daki
    "cached_at"). UI'da "son güncelleme" olarak asıl gösterilmesi gereken
    budur.

    Returns:
        {dosya_adı: yayın_zamanı} -- IIS'in dizin listesindeki HTML'den
        ayrıştırılır (ör. "Wednesday, August 26, 2026  4:10 PM").
    """
    resp = requests.get(folder_url(d), timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    files: dict[str, datetime] = {}
    for ts_text, filename in _IIS_DIR_ENTRY_RE.findall(resp.text):
        try:
            files[filename] = datetime.strptime(ts_text.strip(), _IIS_TIMESTAMP_FORMAT)
        except ValueError:
            continue
    return files


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


def _download_and_extract_xml(d: date, filename: str, force: bool = False) -> Path:
    """ZIP'i indirir (daha önce indirilmediyse), içindeki tek XML'i çıkarır.

    Args:
        force: True ise, disk'te aynı günün XML'i zaten olsa bile yeniden
            indirir (ör. gün içinde daha güncel/nihai bir dosya çıktığında
            -- bkz. ensure_daily_cache).

    Returns:
        Çıkarılan XML dosyasının yolu.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    xml_path = RAW_DIR / f"{d.strftime('%y%m%d')}.xml"
    if xml_path.exists() and not force:
        return xml_path

    url = f"{folder_url(d)}{filename}"
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


def _parse_products_and_spots(xml_path: Path) -> tuple[dict, dict]:
    """Tüm hisse opsiyon ürünlerini (oopPf) VE spot fiyatlarını (phyPf)
    TEK geçişte damıtır (iki ayrı tam-dosya taraması yapmamak için).

    Returns:
        (products, spot_prices)
        products: {ticker: {expiry_yyyymmdd: {"t", "intrRate", "psr",
            "vsr", "options": [{"k", "o", "v", "p"}, ...]}}}
        spot_prices: {ticker: spot_fiyatı}
    """
    products: dict[str, dict] = {}
    spot_prices: dict[str, float] = {}
    context = ET.iterparse(str(xml_path), events=("end",))
    for _event, elem in context:
        if elem.tag == "phyPf":
            try:
                ticker = elem.findtext("pfCode")
                p_val = elem.findtext("phy/p")
                if ticker and p_val:
                    price = float(p_val)
                    if price > 0:  # bazı pasif/kotasyonsuz enstrümanlarda 0.0 geliyor
                        spot_prices[ticker] = price
            finally:
                elem.clear()
            continue

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
                    p = opt.findtext("p")
                    # cvf: kontrat başına dayanak varlık miktarı (hisse
                    # opsiyonlarında 100, XU030D'de 10, USDTRYKP'de 1 --
                    # SABİT VARSAYILAMAZ, her opt kendi cvf'ini taşır).
                    # Alan yoksa (beklenmeyen durum) 100'e düşülür --
                    # bugüne kadarki tüm hisse-opsiyonu davranışıyla aynı.
                    cvf = opt.findtext("cvf")
                    if k and o and v and p is not None:
                        options.append(
                            {
                                "k": float(k),
                                "o": o,
                                "v": float(v),
                                "p": float(p),
                                "cvf": float(cvf) if cvf else 100.0,
                            }
                        )

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

    return products, spot_prices


def build_distilled_cache(xml_path: Path) -> dict:
    """XML'i tarayıp damıtılmış (küçük, hızlı yüklenen) bir sözlük üretir."""
    emm, ecf = _parse_global_extreme_move(xml_path)
    products, spot_prices = _parse_products_and_spots(xml_path)
    return {
        "global": {
            "extreme_move_multiplier": emm,
            "extreme_move_covered_fraction": ecf,
        },
        "products": products,
        "spot_prices": spot_prices,
    }


def _distilled_cache_path(d: date) -> Path:
    return DISTILLED_DIR / f"{d.strftime('%y%m%d')}.json"


_RECHECK_AFTER_SECONDS = 15 * 60  # gün içi (INT) cache'ler bu süre sonra tekrar kontrol edilir


def _write_distilled_cache(
    cache_path: Path,
    distilled: dict,
    source_file: str,
    published_at: datetime | None = None,
) -> None:
    payload = {
        **distilled,
        "source_file": source_file,
        "is_final": source_file.startswith("TAKASEOD"),
        # Takasbank'ın bu dosyayı KENDİ sunucusunda yayınladığı zaman (IIS
        # dizin listesinden) -- BİZİM onu indirdiğimiz an değil. Bilinmiyorsa
        # (ör. testlerde) None kalır, last_update_info bu durumda sadece
        # cached_at'e (bizim fetch zamanımız) düşer.
        "published_at": published_at.isoformat() if published_at else None,
    }
    DISTILLED_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False))


def ensure_daily_cache(today: date | None = None) -> tuple[date, Path]:
    """En son iş gününün damıtılmış cache'inin var olduğundan emin olur.

    Cache YOKSA: en son mevcut Takasbank klasörünü bulur, en güncel
    dosyasını (EOD varsa o, yoksa en yüksek numaralı INT) indirir, parse
    eder ve JSON olarak cache'ler.

    Cache VARSA ve zaten EOD'dan (o günün NİHAİ dosyası) üretildiyse,
    hiçbir şey değişmeyeceği için doğrudan döner.

    Cache VARSA ama bir INTRADAY dosyadan üretildiyse (henüz EOD
    yayınlanmamışken çekilmiş): Takasbank INTRADAY dosyaları gün içinde
    saatlik güncellediği ve günün sonunda (~20:30 civarı) NİHAİ EOD
    dosyasını yayınladığı için, bu cache süresiz geçerli SAYILMAZ --
    bayatlamış (aynı gün içinde daha güncel bir dosya çıkmış) olabilir.
    Bu yüzden cache _RECHECK_AFTER_SECONDS'tan eskiyse, ağır XML'i değil
    sadece klasör listesini (küçük bir HTML) tekrar çekip daha iyi bir
    dosya (EOD ya da daha yüksek numaralı INT) çıkmış mı diye bakılır;
    çıkmışsa cache o dosyadan yeniden kurulur. Bu kontrol her çağrıda
    DEĞİL, sadece süre dolduğunda yapılır -- aksi halde Streamlit'in her
    widget etkileşiminde tüm scripti yeniden çalıştırması Takasbank'ın
    sunucusuna gereksiz istek yağdırırdı.

    Returns:
        (o günün tarihi, damıtılmış JSON dosyasının yolu)
    """
    trading_day = find_latest_trading_day(today)
    cache_path = _distilled_cache_path(trading_day)

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            cached = {}
        if cached.get("is_final"):
            return trading_day, cache_path

        age_seconds = time.time() - cache_path.stat().st_mtime
        if age_seconds < _RECHECK_AFTER_SECONDS:
            return trading_day, cache_path

        try:
            files_map = _list_directory_files(trading_day)
            best_file = _pick_best_file(list(files_map.keys()))
        except Exception:
            # Klasör listesi şu an çekilemedi (geçici ağ sorunu vb.) --
            # elimizdeki (bayat olabilecek) cache'i kullanmaya devam et,
            # bir sonraki çağrıda tekrar denenir.
            return trading_day, cache_path

        if best_file == cached.get("source_file"):
            # Daha iyi bir dosya yok -- tekrar tekrar kontrol etmemek için
            # mtime'ı güncelleyip _RECHECK_AFTER_SECONDS'ı sıfırlıyoruz.
            cache_path.touch()
            return trading_day, cache_path

        # Gün içinde daha güncel/nihai bir dosya yayınlanmış -- yeniden kur.
        xml_path = _download_and_extract_xml(trading_day, best_file, force=True)
        distilled = build_distilled_cache(xml_path)
        _write_distilled_cache(cache_path, distilled, best_file, files_map.get(best_file))
        return trading_day, cache_path

    files_map = _list_directory_files(trading_day)
    best_file = _pick_best_file(list(files_map.keys()))
    xml_path = _download_and_extract_xml(trading_day, best_file)
    distilled = build_distilled_cache(xml_path)
    _write_distilled_cache(cache_path, distilled, best_file, files_map.get(best_file))
    return trading_day, cache_path


def last_update_info() -> dict | None:
    """Diskteki en güncel damıtılmış cache'in tarihini döner (UI'da göstermek için).

    Returns:
        {"source_date": date, "cached_at": datetime, "published_at": datetime | None,
        "source_file": str | None, "is_final": bool} ya da hiç cache yoksa None.

        "cached_at": BİZİM bu veriyi yerel diske ne zaman yazdığımız (fetch
            zamanımız) -- Takasbank'ın dosyayı ne zaman yayınladığıyla
            KARIŞTIRILMAMALI (bkz. proje sohbet geçmişi: önceki UI metni
            bunu "son güncelleme" diye gösterip yanıltıyordu).
        "published_at": Takasbank'ın bu dosyayı KENDİ sunucusuna yüklediği
            an (IIS dizin listesinden okunur). Eski cache'lerde (bu alan
            eklenmeden önce yazılmış) None olabilir -- UI bu durumda
            cached_at'e düşer.
        "is_final" False ise, cache bir INTRADAY (gün içi ara) dosyadan
            üretilmiştir -- Takasbank'ın o günün NİHAİ (EOD) dosyası henüz
            yayınlanmamış olabilir (bkz. ensure_daily_cache).
    """
    if not DISTILLED_DIR.exists():
        return None
    cache_files = sorted(DISTILLED_DIR.glob("*.json"))
    if not cache_files:
        return None
    latest = cache_files[-1]
    d = date(2000 + int(latest.stem[:2]), int(latest.stem[2:4]), int(latest.stem[4:6]))
    try:
        cached_json = json.loads(latest.read_text())
    except (json.JSONDecodeError, OSError):
        cached_json = {}
    is_final = cached_json.get("is_final", False)
    source_file = cached_json.get("source_file")
    published_at_raw = cached_json.get("published_at")
    published_at = None
    if published_at_raw:
        try:
            published_at = datetime.fromisoformat(published_at_raw)
        except ValueError:
            published_at = None
    return {
        "source_date": d,
        "is_final": is_final,
        "source_file": source_file,
        "published_at": published_at,
        "cached_at": datetime.fromtimestamp(latest.stat().st_mtime),
    }


def get_option_params(
    ticker: str,
    expiry: date,
    strike: float,
    option_type: str,
    today: date | None = None,
) -> TakasbankOptionParams:
    """Verilen hisse/vade/strike/tip için Takasbank'ın günlük PC-SPAN
    dosyasından T, faiz oranı, implied volatility, piyasa fiyatı, PSR,
    VSR, Extreme Move Multiplier/Covered Fraction değerlerini çeker.

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
    xml_pfcode = to_xml_pfcode(normalized_ticker)
    trading_day, cache_path = ensure_daily_cache(today)
    distilled = json.loads(cache_path.read_text())

    products = distilled["products"]
    if xml_pfcode not in products:
        raise KeyError(
            f"{normalized_ticker}, {trading_day} tarihli Takasbank dosyasında "
            f"bulunamadı (opsiyonu olmayabilir)"
        )

    expiry_str = expiry.strftime("%Y%m%d")
    by_expiry = products[xml_pfcode]
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
                market_price=opt["p"],
                contract_size=opt.get("cvf", 100.0),
                source_date=trading_day,
            )

    available_strikes = sorted(
        {opt["k"] for opt in series_data["options"] if opt["o"] == target_code}
    )
    raise ValueError(
        f"{normalized_ticker} {expiry_str} {option_type} için strike={strike} "
        f"bulunamadı. Mevcut strike'lar: {available_strikes}"
    )


@dataclass
class TakasbankSpotPrice:
    """Bir hissenin Takasbank'ın günlük PC-SPAN dosyasından çekilen
    güncel/spot fiyatı (``phyPf/phy/p``)."""

    ticker: str
    price: float
    source_date: date


def get_spot_price(ticker: str, today: date | None = None) -> TakasbankSpotPrice:
    """Bir hissenin Takasbank XML'inden spot/underlying fiyatını çeker.

    Args:
        ticker: BIST sembolü (".IS" soneki otomatik temizlenir).
        today: Test/override amaçlı "bugün" tarihi (varsayılan: gerçek bugün).

    Returns:
        TakasbankSpotPrice.

    Raises:
        KeyError: Hisse dosyada bulunamazsa.
    """
    normalized_ticker = ticker.upper().strip().removesuffix(".IS")
    # ÖNEMLİ: opsiyonların strike'ları hangi pfCode'un ölçeğindeyse (ör.
    # USDTRYKP: ~48000'ler, 1000×USDTRY), spot da AYNI ölçekten gelmeli --
    # yoksa Black-Scholes'a strike ile tutarsız bir spot girer (spot/strike
    # oranı 1000× kayar, tüm fiyatlama anlamsızlaşır). Bu yüzden burada da
    # aynı pfCode alias'ı uygulanıyor (bkz. to_xml_pfcode).
    xml_pfcode = to_xml_pfcode(normalized_ticker)
    trading_day, cache_path = ensure_daily_cache(today)
    distilled = json.loads(cache_path.read_text())

    spot_prices = distilled.get("spot_prices", {})
    if xml_pfcode not in spot_prices:
        raise KeyError(
            f"{normalized_ticker}, {trading_day} tarihli Takasbank dosyasında "
            f"spot fiyatı bulunamadı"
        )
    return TakasbankSpotPrice(
        ticker=normalized_ticker,
        price=spot_prices[xml_pfcode],
        source_date=trading_day,
    )
