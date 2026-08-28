"""Takasbank Günlük PC-SPAN XML'inden VADELİ İŞLEM (futures) verisi çeker.

BAĞIMSIZ KATMAN: Bu modül, opsiyon katmanına (takasbank_xml.py,
span_engine.py, BIST_Opsiyon.py'nin opsiyon akışı) HİÇBİR ŞEKİLDE dokunmadan
yazılmıştır -- sadece takasbank_xml.py'nin PUBLIC, DEĞİŞTİRİLMEMİŞ
fonksiyonlarını (ensure_daily_cache, folder_url, CACHE_DIR/RAW_DIR)
salt-okunur olarak kullanır. Amaç: "BIST Vadeli İşlem" özelliği
istenirse -- bu dosya + futures_engine.py + pages/ altındaki sayfa --
opsiyon özelliğini hiç etkilemeden tek seferde silinebilsin.

Keşif notları (gerçek Takasbank dosyası üzerinde doğrulanmıştır --
bkz. proje sohbet geçmişi):

- spanFile/exchange/.../futPf: bir dayanak varlığın vadeli işlem ürünü
  (pfCode, valueMeth="FUT", setlMeth).
  - fut: HER VADE için bir kayıt -- opsiyonlardan FARKLI olarak
    <series> sarmalayıcısı YOK, <fut> doğrudan <futPf>'nin çocuğu,
    <pe> (vade) kendi içinde. Alanlar:
    <p>=fiyat, <d>=delta (HER ZAMAN 1.0 -- doğrusal enstrüman, opsiyon
    değil), <cvf>=kontrat çarpanı (hisse vadelilerinde 100, opsiyonlarla
    aynı desen), <t>=T (yıl, hazır), <scanRate><r>/<priceScanPct>/
    <volScan> (volScan HER ZAMAN 0.0 -- vadeli işlemde volatilite
    riski yok, doğrusal P&L), <ra>=16 senaryonun Takasbank'ın KENDİ
    önceden hesapladığı risk dizisi (opsiyonlardaki <opt><ra> ile
    birebir aynı yapı -- biz kendi motorumuzla (futures_engine.py)
    ayrıca hesaplıyoruz, bu alanı şimdilik kullanmıyoruz ama ileride
    çapraz doğrulama için değerli).

- XML'de İKİ farklı futPf ailesi var:
  1) Gerçek TEK HİSSE vadeli işlemleri (ör. "AEFES", "AKBNK") --
     genelde 3 vadeli, scanRate.r hisse opsiyonlarıyla AYNI grup
     (r=1) OLABİLİR ama HER ZAMAN değil (bazı normal ürünler r=2
     kullanıyor) -- bu yüzden EMM/ECF'i r'ye göre PARAMETRİK okuyoruz
     (bkz. _parse_extreme_move_for_group), takasbank_xml.py'nin r=1'e
     SABİTLENMİŞ private fonksiyonunu (_parse_global_extreme_move)
     KULLANMIYORUZ (o fonksiyona hiç dokunmuyoruz).
  2) "_C" son ekli (ör. "AEFES_C"), çok sayıda (91'e kadar) hep
     BUGÜNÜN tarihli "sanal marjin" ürün ailesi (muhtemelen repo/ödünç
     tarzı bir finansman ürünü, gerçek "vadeli işlem sözleşmesi"
     DEĞİL) -- bu KASITLI OLARAK dışarıda bırakılıyor.

Kapsam (bilinçli, ilk sürüm kararı): Şimdilik SADECE hisse senedi
vadeli işlemleri destekleniyor -- elektrik/değerli maden/döviz gibi
egzotik futPf ürünleri (ör. "ELCBAS01", "AUVMS_N", "CNHTRY") kapsam
dışı, ileride ayrı bir karar olarak eklenebilir. Bu modül ham ticker
listesini filtrelemeden döner (_C ailesi hariç); "sadece bilinen hisse
evreniyle kesiştir" filtresi ÇAĞIRAN TARAFTA (Vadeli İşlem sayfası)
yapılır -- böylece bu modül BIST_Opsiyon.py'ye bağımlı olmaz.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from bist_span import takasbank_xml as tbx

FUTURES_DISTILLED_DIR = tbx.CACHE_DIR / "futures_distilled"


def _is_real_futures_ticker(ticker: str) -> bool:
    """"_C" son ekli "sanal marjin" ailesini (gerçek vadeli işlem
    sözleşmesi değil) dışarıda bırakır -- bkz. modül docstring'i."""
    return not (ticker.endswith("_C") or ticker.endswith("R_C"))


def _parse_extreme_move_for_group(xml_path: Path, r_group: str) -> tuple[float, float]:
    """Verilen risk grubu (r) için global Extreme Move Multiplier/Covered
    Fraction'ı bulur -- takasbank_xml._parse_global_extreme_move'un r'ye
    göre PARAMETRİK hali (o fonksiyon r=1'e sabit, buraya dokunmuyoruz;
    vadeli işlemler r=1 VEYA r=2 kullanabiliyor, bkz. modül docstring'i).
    """
    context = ET.iterparse(str(xml_path), events=("end",))
    for _event, elem in context:
        if elem.tag != "pointDef":
            continue
        if elem.findtext("r") == r_group:
            for spd in elem.findall("scanPointDef"):
                if spd.findtext("point") == "15":
                    multiplier = float(spd.findtext("priceScanDef/mult"))
                    covered_fraction = float(spd.findtext("weight"))
                    return multiplier, covered_fraction
        elem.clear()
    raise ValueError(f"{xml_path} içinde pointDef[r={r_group}]/scanPointDef[point=15] bulunamadı")


def _parse_futures(xml_path: Path) -> dict:
    """Tüm vadeli işlem ürünlerini (futPf, valueMeth=FUT) damıtır.

    Returns:
        {ticker: {expiry_yyyymmdd: {"p", "t", "cvf", "r", "psr", "vsr"}}}
    """
    products: dict[str, dict] = {}
    context = ET.iterparse(str(xml_path), events=("end",))
    for _event, elem in context:
        if elem.tag != "futPf":
            continue
        try:
            if elem.findtext("valueMeth") != "FUT":
                continue
            ticker = elem.findtext("pfCode")
            if not ticker or not _is_real_futures_ticker(ticker):
                continue

            by_expiry: dict[str, dict] = {}
            for fut in elem.findall("fut"):
                expiry_str = fut.findtext("pe")
                p_val = fut.findtext("p")
                t_val = fut.findtext("t")
                cvf_val = fut.findtext("cvf")
                r_val = fut.findtext("scanRate/r")
                psr_val = fut.findtext("scanRate/priceScanPct")
                vsr_val = fut.findtext("scanRate/volScan")
                if not (expiry_str and p_val and t_val and r_val and psr_val):
                    continue
                if float(p_val) <= 0:  # aktif olmayan/kotasyonsuz sözleşme
                    continue
                by_expiry[expiry_str] = {
                    "p": float(p_val),
                    "t": float(t_val),
                    "cvf": float(cvf_val) if cvf_val else 100.0,
                    "r": r_val,
                    "psr": float(psr_val) / 100.0,
                    "vsr": float(vsr_val) / 100.0 if vsr_val else 0.0,
                }

            if by_expiry:
                products[ticker] = by_expiry
        finally:
            elem.clear()

    return products


def build_futures_distilled_cache(xml_path: Path) -> dict:
    """XML'i tarayıp vadeli işlemler için damıtılmış bir sözlük üretir.

    Returns:
        {"products": {...}, "extreme_move_by_group": {"1": {"emm":, "ecf":}, "2": {...}, ...}}
    """
    products = _parse_futures(xml_path)
    r_groups = {opt["r"] for by_expiry in products.values() for opt in by_expiry.values()}
    extreme_move_by_group = {}
    for r in r_groups:
        emm, ecf = _parse_extreme_move_for_group(xml_path, r)
        extreme_move_by_group[r] = {"emm": emm, "ecf": ecf}
    return {"products": products, "extreme_move_by_group": extreme_move_by_group}


def _futures_distilled_cache_path(d: date) -> Path:
    return FUTURES_DISTILLED_DIR / f"{d.strftime('%y%m%d')}.json"


def ensure_futures_daily_cache(today: date | None = None) -> tuple[date, Path]:
    """Vadeli işlemler için damıtılmış günlük cache'in var olduğundan emin olur.

    Önce takasbank_xml.ensure_daily_cache() (DEĞİŞTİRİLMEDEN, olduğu gibi
    çağrılır) ile o günün ham XML'inin diskte VE GÜNCEL olduğunu
    garanti eder (EOD/INT tazelik kontrolü zaten orada var, burada
    TEKRARLANMIYOR). Sonra: bizim kendi vadeli işlem cache'imiz o ham
    XML'den daha eskiyse (ya da hiç yoksa), yeniden kurulur -- yani
    opsiyon tarafı bir INT dosyasını EOD ile değiştirdiğinde, vadeli
    işlem tarafı da otomatik senkron kalır.
    """
    trading_day, raw_xml_cache_path = tbx.ensure_daily_cache(today)
    raw_xml_path = tbx.RAW_DIR / f"{trading_day.strftime('%y%m%d')}.xml"
    cache_path = _futures_distilled_cache_path(trading_day)

    needs_rebuild = True
    if cache_path.exists() and raw_xml_path.exists():
        needs_rebuild = cache_path.stat().st_mtime < raw_xml_path.stat().st_mtime

    if needs_rebuild:
        distilled = build_futures_distilled_cache(raw_xml_path)
        FUTURES_DISTILLED_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(distilled, ensure_ascii=False))

    return trading_day, cache_path


@dataclass
class FuturesParams:
    """Bir hisse vadeli işlem sözleşmesi için Takasbank'ın günlük PC-SPAN
    dosyasından otomatik çekilen parametreler.

    Attributes:
        ticker: BIST sembolü (ör. "AKBNK").
        expiry: Vade tarihi.
        price: Sözleşmenin GÜNCEL fiyatı (<fut><p>) -- doğrusal
            enstrüman olduğu için hem "spot" hem "taban fiyat" rolünü
            aynı anda görür, opsiyonlardaki gibi ayrı bir spot/taban
            fiyat ayrımı YOKTUR.
        time_to_expiry: T, yıl cinsinden (XML'in <t> alanından, hazır).
        contract_size: Kontrat başına dayanak varlık miktarı (<cvf>,
            hisse vadelilerinde genelde 100).
        price_scan_range: PSR, ondalık.
        volatility_scan_range: VSR -- vadeli işlemlerde HER ZAMAN 0.0
            (volatilite riski yok, doğrusal enstrüman).
        extreme_move_multiplier, extreme_move_covered_fraction: Bu
            sözleşmenin risk grubuna (r) ait Extreme Move parametreleri.
        source_date: Bu verinin ait olduğu Takasbank iş günü.
    """

    ticker: str
    expiry: date
    price: float
    time_to_expiry: float
    contract_size: float
    price_scan_range: float
    volatility_scan_range: float
    extreme_move_multiplier: float
    extreme_move_covered_fraction: float
    source_date: date


def list_futures_tickers(today: date | None = None) -> list[str]:
    """XML'de bulunan TÜM gerçek (_C ailesi hariç) vadeli işlem
    ticker'larını döner -- hisse dışı egzotik ürünler DAHİL (elektrik,
    değerli maden vb.); "sadece bilinen hisse evreniyle kesiştir"
    filtresi çağıran tarafta (Vadeli İşlem sayfası) yapılmalı."""
    _trading_day, cache_path = ensure_futures_daily_cache(today)
    distilled = json.loads(cache_path.read_text())
    return sorted(distilled["products"].keys())


def list_futures_expiries(ticker: str, today: date | None = None) -> list[date]:
    normalized = ticker.upper().strip().removesuffix(".IS")
    _trading_day, cache_path = ensure_futures_daily_cache(today)
    distilled = json.loads(cache_path.read_text())
    by_expiry = distilled["products"].get(normalized, {})
    dates = []
    for expiry_str in by_expiry:
        try:
            dates.append(date(int(expiry_str[:4]), int(expiry_str[4:6]), int(expiry_str[6:8])))
        except ValueError:
            continue
    return sorted(dates)


def get_futures_params(ticker: str, expiry: date, today: date | None = None) -> FuturesParams:
    """Verilen hisse vadeli işlem sözleşmesi için Takasbank'ın günlük
    PC-SPAN dosyasından fiyat, T, PSR, kontrat çarpanı ve Extreme Move
    parametrelerini çeker.

    Raises:
        KeyError: Hisse ya da vade dosyada bulunamazsa.
    """
    normalized_ticker = ticker.upper().strip().removesuffix(".IS")
    trading_day, cache_path = ensure_futures_daily_cache(today)
    distilled = json.loads(cache_path.read_text())

    products = distilled["products"]
    if normalized_ticker not in products:
        raise KeyError(
            f"{normalized_ticker}, {trading_day} tarihli Takasbank dosyasında "
            f"vadeli işlem sözleşmesi olarak bulunamadı"
        )

    expiry_str = expiry.strftime("%Y%m%d")
    by_expiry = products[normalized_ticker]
    if expiry_str not in by_expiry:
        available = ", ".join(sorted(by_expiry))
        raise KeyError(
            f"{normalized_ticker} için {expiry_str} vadesi bulunamadı. "
            f"Mevcut vadeler: {available}"
        )

    fut = by_expiry[expiry_str]
    extreme = distilled["extreme_move_by_group"].get(
        fut["r"], {"emm": 3.0, "ecf": 0.32}  # bulunamazsa hisse opsiyonlarının bilinen varsayılanı
    )

    return FuturesParams(
        ticker=normalized_ticker,
        expiry=expiry,
        price=fut["p"],
        time_to_expiry=fut["t"],
        contract_size=fut["cvf"],
        price_scan_range=fut["psr"],
        volatility_scan_range=fut["vsr"],
        extreme_move_multiplier=extreme["emm"],
        extreme_move_covered_fraction=extreme["ecf"],
        source_date=trading_day,
    )
