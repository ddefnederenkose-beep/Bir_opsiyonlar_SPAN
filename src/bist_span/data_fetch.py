"""Veri Çekme Katmanı.

yfinance üzerinden BIST hisseleri (örn. AKBNK.IS, GARAN.IS) için güncel
fiyat ve geçmiş fiyat serisi çeker; bu seriden historical volatility
hesaplar. Sonuçlar günlük bazda basit bir dosya cache'inde tutulur ki
her çağrıda API'ye gidilmesin.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# Cache dosyalarının tutulacağı dizin (proje kökündeki cache/).
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache"


@dataclass
class PriceData:
    """Bir hisse için güncel fiyat + geçmiş seri + hesaplanan volatilite.

    Attributes:
        ticker: yfinance sembolü (örn. "AKBNK.IS").
        current_price: Güncel (son) fiyat.
        history: Geçmiş fiyat serisi (DataFrame, en azından 'Close' kolonu).
        historical_volatility: Yıllıklaştırılmış historical volatility.
        as_of: Verinin hangi tarih için cache'lendiği.
    """

    ticker: str
    current_price: float
    history: pd.DataFrame
    historical_volatility: float
    as_of: date


def get_current_price(ticker: str) -> float:
    """yfinance üzerinden güncel fiyatı döner.

    Args:
        ticker: BIST sembolü, örn. "AKBNK.IS".

    Returns:
        Güncel fiyat (son kapanış / son işlem fiyatı).

    Raises:
        ValueError: Ticker için fiyat verisi alınamazsa.
    """
    t = yf.Ticker(ticker)

    try:
        last_price = t.fast_info.get("lastPrice")
        if last_price is not None and not np.isnan(last_price):
            return float(last_price)
    except Exception:
        pass

    hist = t.history(period="1d")
    if hist.empty:
        raise ValueError(f"{ticker} için fiyat verisi alınamadı")
    return float(hist["Close"].iloc[-1])


def get_historical_prices(ticker: str, period: str = "1y") -> pd.DataFrame:
    """yfinance üzerinden geçmiş fiyat serisini döner.

    Args:
        ticker: BIST sembolü, örn. "GARAN.IS".
        period: yfinance period parametresi (örn. "1y", "6mo").

    Returns:
        En azından 'Close' kolonunu içeren DataFrame (index: tarih).

    Raises:
        ValueError: Ticker için geçmiş veri alınamazsa.
    """
    hist = yf.Ticker(ticker).history(period=period)
    if hist.empty:
        raise ValueError(f"{ticker} için geçmiş fiyat verisi alınamadı")
    return hist


def calculate_historical_volatility(
    prices: pd.Series, trading_days: int = 252
) -> float:
    """Log return standart sapmasından yıllıklaştırılmış volatilite hesaplar.

    Formül: std(log(P_t / P_t-1)) * sqrt(trading_days)

    Args:
        prices: Kapanış fiyatları serisi (kronolojik sırada).
        trading_days: Yıllıklaştırma için işlem günü sayısı (varsayılan 252).

    Returns:
        Yıllıklaştırılmış historical volatility (örn. 0.35 -> %35).

    Raises:
        ValueError: En az 2 fiyat noktası yoksa.
    """
    prices = prices.dropna()
    if len(prices) < 2:
        raise ValueError("Volatilite hesabı için en az 2 fiyat noktası gerekli")

    log_returns = np.log(prices / prices.shift(1)).dropna()
    return float(log_returns.std() * np.sqrt(trading_days))


def _cache_path(ticker: str) -> Path:
    """Bir ticker için günlük cache dosyasının yolunu döner."""
    safe_ticker = ticker.replace("/", "_")
    return CACHE_DIR / f"{safe_ticker}_{date.today().isoformat()}.json"


def _read_cache(ticker: str) -> PriceData | None:
    """Bugüne ait cache varsa okur, yoksa None döner."""
    path = _cache_path(ticker)
    if not path.exists():
        return None

    raw = json.loads(path.read_text())
    history = pd.read_csv(io.StringIO(raw["history_csv"]), index_col=0, parse_dates=True)
    return PriceData(
        ticker=raw["ticker"],
        current_price=raw["current_price"],
        history=history,
        historical_volatility=raw["historical_volatility"],
        as_of=date.fromisoformat(raw["as_of"]),
    )


def _write_cache(ticker: str, data: PriceData) -> None:
    """Hesaplanan PriceData'yı günlük cache dosyasına yazar."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticker": data.ticker,
        "current_price": data.current_price,
        "history_csv": data.history.to_csv(),
        "historical_volatility": data.historical_volatility,
        "as_of": data.as_of.isoformat(),
    }
    _cache_path(ticker).write_text(json.dumps(payload))


def get_price_data(ticker: str, period: str = "1y") -> PriceData:
    """Cache varsa cache'den, yoksa API'den fiyat + volatilite verisi döner.

    Bu fonksiyon dış dünyaya (risk_params / span_engine / main) açılan
    ana giriş noktasıdır: önce _read_cache, cache miss ise
    get_current_price + get_historical_prices + calculate_historical_volatility
    ile veriyi üretip _write_cache ile cache'ler.

    Args:
        ticker: BIST sembolü.
        period: Geçmiş veri periyodu.

    Returns:
        PriceData nesnesi.
    """
    cached = _read_cache(ticker)
    if cached is not None:
        return cached

    current_price = get_current_price(ticker)
    history = get_historical_prices(ticker, period=period)
    volatility = calculate_historical_volatility(history["Close"])

    data = PriceData(
        ticker=ticker,
        current_price=current_price,
        history=history,
        historical_volatility=volatility,
        as_of=date.today(),
    )
    _write_cache(ticker, data)
    return data
