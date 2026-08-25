"""Risk Parametre Katmanı.

Takasbank'ın "Risk Parametrelerinin Güncellenmesi" genel mektubu olarak
yayınladığı PDF'leri parse ederek Vadeli İşlem ve Opsiyon Piyasası
(VİOP) tablosundan hisse bazında SPAN risk parametrelerini (PSR, VSR,
Extreme Move Multiplier, Extreme Move Covered Fraction,
Intra-Commodity Spread Charge, Short Option Minimum) çıkarır.

Belge yapısı hakkında notlar
-----------------------------
Takasbank'ın güncelleme mektupları tipik olarak iki blok içerir:

1. "DEĞİŞEN RİSK PARAMETRELERİ" — sadece değişen bölümlerin YENİ
   değerlerini listeler (örn. sadece Intra-Commodity Spread değiştiyse
   SADECE o bölüm burada görünür, diğerleri hiç yer almaz).
2. "MEVCUTTA GEÇERLİ OLAN RİSK PARAMETRELERİ" — değişmeyen (dolayısıyla
   hâlâ geçerli olan) bölümleri listeler.

VİOP tablosu içinde her biri "N) <Başlık>" formatında numaralanmış alt
bölümler bulunur (PSR, Extreme Move, Intra-Commodity Spread, Fiziki
Teslimat, VSR, Inter-Commodity Spread Credit, SOM). ÖNEMLİ: bu N
numarası SABİT DEĞİL — Takasbank, hangi bölümlerin "DEĞİŞEN" bloğunda
yer aldığına göre numaralandırmayı HER MEKTUPTA yeniden sıralıyor
(örn. bir mektupta PSR "1)" iken, sadece Intra-Commodity Spread'in
değiştiği bir başka mektupta PSR "2)" olabiliyor -- iki gerçek Takasbank
mektubu karşılaştırılarak doğrulanmıştır). Bu yüzden bölümleri numaraya
göre DEĞİL, başlığın Türkçe metnine göre (numaradan bağımsız,
_TITLE_FIELD_MAP) eşleştiriyoruz.

Aynı sebeple, "DEĞİŞEN" bloğu her zaman VİOP piyasa başlık satırını
("VADELİ İŞLEM VE OPSİYON PİYASASI ...") içermeyebilir -- bazı
mektuplarda değişen bölüm(ler) doğrudan, herhangi bir piyasa başlığı
olmadan listelenir. Bu yüzden bloklara piyasa başlığından değil,
belgenin tamamını tarayıp numaralı bölüm başlıklarını ve bilinen diğer
piyasa tablolarının başlıklarını (bir VİOP bölümünün bittiğinin işareti
olarak) takip ederek ayrıştırıyoruz.

Aynı hisse/alan kombinasyonu birden fazla yerde görünürse, belgede daha
ÖNCE geçen değer kazanır -- "DEĞİŞEN" bloğu her zaman "MEVCUTTA GEÇERLİ
OLAN" bloğundan önce geldiği için bu, her zaman en güncel değeri
kullanmamızı sağlar.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
import pdfplumber

# "1) Fiyat Değişim Aralığı" gibi numaralı alt bölüm başlıkları.
# Numara kasıtlı olarak yakalanmıyor/kullanılmıyor -- bkz. modül docstring'i.
_SECTION_HEADER_RE = re.compile(r"^\s*\d+\)\s*(.+?)\s*$")

# Bölüm başlığının Türkçe metninde geçen anahtar kelime -> RiskParams alan
# adı. Sırayla kontrol edilir; ilk eşleşen kazanır (aralarında çakışan alt
# dize yok). (Fiziki Teslimat ve Inter-Commodity Spread Credit kapsam dışı.)
_TITLE_FIELD_MAP = (
    ("Vadeler Arası Yayılma Pozisyonu Riski", "intra_commodity_spread_charge"),
    ("Fiyat Değişim Aralığı", "price_scan_range"),
    ("Volatilite Değişim Aralığı", "volatility_scan_range"),
    ("Kısa Opsiyon Pozisyonu Minimum Riski", "short_option_minimum"),
)
_EXTREME_MOVE_TITLE = "Aşırı Hareket Senaryosu"

# Aktif VİOP alt bölümünü sonlandıran, başka bir piyasa tablosunun
# başladığını gösteren satır başlangıçları.
_BLOCK_BOUNDARY_MARKERS = (
    "PAY PİYASASI BISTECH MARJİN",
    "BAP-BISTECH",
    "OTC- BISTECH",
    "BİAŞ SWAP PİYASASI",
    "ÖDÜNÇ PAY PİYASASI",
    "KIYMETLİ MADENLER",
    "PARA PİYASASI RİSK PARAMETRE",
    "TAKASBANK PARA PİYASASI",
    "TAKASBANK ÇEK TAKASI",
)

# "AKBNK 15.7", "ASELS 4,190", "TLREF (Vade Dışı) 8.2" gibi veri satırları.
_DATA_LINE_RE = re.compile(
    r"^(?P<ticker>[A-ZÇĞİÖŞÜ0-9]+(?:\s\([^)]*\))?(?:-[A-ZÇĞİÖŞÜ0-9]+)?)"
    r"\s+(?P<value>[\d,]+\.?\d*)\s*$"
)

# Yüzde olarak verilen ve 0-1 aralığına normalize edilecek alanlar.
_PERCENT_FIELDS = {"price_scan_range", "volatility_scan_range"}


@dataclass
class RiskParams:
    """Bir hisse için SPAN risk parametre seti.

    Attributes:
        ticker: BIST/VİOP sembolü (örn. "AKBNK").
        price_scan_range: Price Scan Range (PSR), ondalık oran (0.157 = %15.7).
        volatility_scan_range: Volatility Scan Range (VSR), ondalık oran.
        extreme_move_multiplier: Extreme Move Multiplier (ör. 3.0).
        extreme_move_covered_fraction: Extreme Move Covered Fraction, ondalık oran.
        intra_commodity_spread_charge: Intra-Commodity Spread Charge, TL tutar.
        short_option_minimum: Short Option Minimum (SOM), TL tutar.
    """

    ticker: str
    price_scan_range: float
    volatility_scan_range: float
    extreme_move_multiplier: float
    extreme_move_covered_fraction: float
    intra_commodity_spread_charge: float
    short_option_minimum: float


def _extract_full_text(file_path: str | Path) -> list[str]:
    """PDF'in tüm sayfalarını satır satır, belge sırasıyla döner."""
    lines: list[str] = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(text.splitlines())
    return lines


def _collect_section_lines(
    lines: list[str],
) -> tuple[dict[str, list[str]], list[str]]:
    """Belgeyi tek geçişte tarayıp VİOP alt bölümlerini başlık metnine göre toplar.

    Numaralı bir bölüm başlığı ("N) <Başlık>") görüldüğünde, başlık metni
    _TITLE_FIELD_MAP'teki anahtar kelimelerle karşılaştırılır; eşleşirse
    o andan itibaren gelen veri satırları ilgili alan için toplanır.
    Extreme Move başlığı ayrı bir listede toplanır. Bilinen başka bir
    piyasa tablosunun başlığı görüldüğünde toplama durdurulur (bir
    sonraki numaralı başlığa kadar hiçbir şey toplanmaz).

    Belgede aynı alan için birden fazla bölüm geçebilir (ör. "DEĞİŞEN"
    ve "MEVCUTTA GEÇERLİ OLAN" blokları) -- bu durumda satırlar, belgede
    geçtikleri sırayla aynı listeye eklenir; bu da _parse_data_lines +
    "ilk değer kazanır" mantığıyla birleştiğinde en güncel (önce gelen)
    değerin kullanılmasını sağlar.

    Returns:
        (field_lines, extreme_move_lines)
    """
    field_lines: dict[str, list[str]] = {field: [] for _, field in _TITLE_FIELD_MAP}
    extreme_move_lines: list[str] = []
    current_field: str | None = None
    current_is_extreme = False

    for line in lines:
        if any(marker in line for marker in _BLOCK_BOUNDARY_MARKERS):
            current_field = None
            current_is_extreme = False
            continue

        header_match = _SECTION_HEADER_RE.match(line)
        if header_match:
            title = header_match.group(1)
            current_field = next(
                (field for keyword, field in _TITLE_FIELD_MAP if keyword in title),
                None,
            )
            current_is_extreme = _EXTREME_MOVE_TITLE in title
            continue

        if current_field is not None:
            field_lines[current_field].append(line)
        elif current_is_extreme:
            extreme_move_lines.append(line)

    return field_lines, extreme_move_lines


def _parse_data_lines(lines: list[str]) -> list[tuple[str, float]]:
    """"TICKER value" formatındaki satırları (ticker, değer) çiftlerine çevirir."""
    results = []
    for line in lines:
        match = _DATA_LINE_RE.match(line.strip())
        if not match:
            continue
        ticker = match.group("ticker")
        value = float(match.group("value").replace(",", ""))
        results.append((ticker, value))
    return results


def _parse_extreme_move(lines: list[str]) -> tuple[float | None, float | None]:
    """Extreme Move Multiplier ve Covered Fraction global değerlerini bulur."""
    multiplier: float | None = None
    covered_fraction: float | None = None
    number_re = re.compile(r"(\d+(?:\.\d+)?)\s*$")

    for line in lines:
        number_match = number_re.search(line)
        if not number_match:
            continue
        value = float(number_match.group(1))
        if "Multiplier" in line or "Çarpanı" in line:
            multiplier = value
        elif "Covered Fraction" in line or "Kapsama Oranı" in line:
            covered_fraction = value / 100.0

    return multiplier, covered_fraction


def parse_takasbank_span_file(file_path: str | Path) -> pd.DataFrame:
    """Takasbank SPAN risk parametre PDF'ini parse edip tablo döner.

    VİOP (Vadeli İşlem ve Opsiyon Piyasası) tablosundaki PSR, Extreme
    Move, Intra-Commodity Spread, VSR ve SOM bölümleri, belgedeki bölüm
    NUMARASINDAN bağımsız olarak başlık metnine göre okunur (bkz. modül
    docstring'i). Belgede aynı alan birden fazla yerde geçerse (ör.
    "DEĞİŞEN" / "MEVCUTTA GEÇERLİ OLAN" blokları), her hisse+alan
    kombinasyonu için belgede İLK geçen değer kullanılır -- bu, güncelleme
    mektuplarında her zaman en güncel değerdir.

    Args:
        file_path: Takasbank'tan indirilen PDF dosyasının yolu.

    Returns:
        Kolonları RiskParams alanlarıyla birebir eşleşen DataFrame:
        ticker, price_scan_range, volatility_scan_range,
        extreme_move_multiplier, extreme_move_covered_fraction,
        intra_commodity_spread_charge, short_option_minimum.
        Bir hisse için bir alan hiçbir blokta bulunamazsa NaN olur
        (örn. opsiyonu olmayan bir vadeli işlem sözleşmesinde VSR/SOM
        bulunmayabilir).

    Raises:
        ValueError: Belgede aradığımız alanlardan hiçbiri bulunamazsa.
    """
    lines = _extract_full_text(file_path)
    field_lines, extreme_move_lines = _collect_section_lines(lines)

    if not any(field_lines.values()) and not extreme_move_lines:
        raise ValueError(
            f"{file_path} içinde VİOP risk parametre bölümleri bulunamadı"
        )

    extreme_multiplier, extreme_covered_fraction = _parse_extreme_move(
        extreme_move_lines
    )

    fields_by_ticker: dict[str, dict[str, float]] = {}
    for field_name, lines_for_field in field_lines.items():
        for ticker, value in _parse_data_lines(lines_for_field):
            if field_name in _PERCENT_FIELDS:
                value = value / 100.0
            fields_by_ticker.setdefault(ticker, {})
            fields_by_ticker[ticker].setdefault(field_name, value)

    rows = []
    for ticker in sorted(fields_by_ticker):
        fields = fields_by_ticker[ticker]
        rows.append(
            {
                "ticker": ticker,
                "price_scan_range": fields.get("price_scan_range"),
                "volatility_scan_range": fields.get("volatility_scan_range"),
                "extreme_move_multiplier": extreme_multiplier,
                "extreme_move_covered_fraction": extreme_covered_fraction,
                "intra_commodity_spread_charge": fields.get(
                    "intra_commodity_spread_charge"
                ),
                "short_option_minimum": fields.get("short_option_minimum"),
            }
        )

    return pd.DataFrame(rows)


class RiskParamsStore:
    """Hisse bazında risk parametrelerini bellekte/diskte tutan basit depo."""

    def __init__(self) -> None:
        self._params: dict[str, RiskParams] = {}

    def load_from_dataframe(self, df: pd.DataFrame) -> None:
        """parse_takasbank_span_file çıktısından depoyu doldurur.

        Zorunlu alanlardan biri eksik (NaN) olan satırlar atlanır --
        SPAN hesabı yapılabilmesi için tüm parametrelerin dolu olması
        gerekir.
        """
        required = [
            "price_scan_range",
            "volatility_scan_range",
            "extreme_move_multiplier",
            "extreme_move_covered_fraction",
            "intra_commodity_spread_charge",
            "short_option_minimum",
        ]
        for _, row in df.iterrows():
            if row[required].isna().any():
                continue
            self._params[row["ticker"]] = RiskParams(
                ticker=row["ticker"],
                price_scan_range=float(row["price_scan_range"]),
                volatility_scan_range=float(row["volatility_scan_range"]),
                extreme_move_multiplier=float(row["extreme_move_multiplier"]),
                extreme_move_covered_fraction=float(
                    row["extreme_move_covered_fraction"]
                ),
                intra_commodity_spread_charge=float(
                    row["intra_commodity_spread_charge"]
                ),
                short_option_minimum=float(row["short_option_minimum"]),
            )

    def get(self, ticker: str) -> RiskParams:
        """Bir hisse için risk parametrelerini döner.

        Args:
            ticker: BIST/VİOP sembolü (örn. "AKBNK"). ".IS" soneki
                otomatik olarak temizlenir (yfinance sembolleriyle
                doğrudan uyumluluk için).

        Raises:
            KeyError: Depoda bu hisse için parametre yoksa.
        """
        normalized = ticker.removesuffix(".IS")
        if normalized not in self._params:
            raise KeyError(f"{ticker} için risk parametresi bulunamadı")
        return self._params[normalized]

    def __len__(self) -> int:
        return len(self._params)

    def tickers(self) -> list[str]:
        """Depoda tam risk parametresi bulunan tüm sembolleri sıralı döner."""
        return sorted(self._params)

    def save(self, file_path: str | Path) -> None:
        """Depoyu diske JSON olarak kaydeder."""
        payload = {ticker: asdict(params) for ticker, params in self._params.items()}
        Path(file_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def load(self, file_path: str | Path) -> None:
        """Depoyu diskten (save ile yazılmış JSON'dan) yükler."""
        payload = json.loads(Path(file_path).read_text())
        self._params = {
            ticker: RiskParams(**fields) for ticker, fields in payload.items()
        }
