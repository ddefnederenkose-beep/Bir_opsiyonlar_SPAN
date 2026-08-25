"""risk_params.py için testler.

`tests/fixtures/takasbank_span_sample.pdf`, Takasbank'ın gerçek bir
"Risk Parametrelerinin Güncellenmesi" genel mektubudur (2161, 30/07/2026).
Bu fixture'daki AKBNK/GARAN/ASELS değerleri PDF'ten elle doğrulanmış
referans değerlerdir (bkz. modül docstring'i için sf. 4-9 "DEĞİŞEN" ve
sf. 13-20 "MEVCUTTA GEÇERLİ OLAN" blokları).

`tests/fixtures/takasbank_span_sample_2.pdf` ise daha sonraki bir gerçek
mektuptur (2163, 13/08/2026) ve KASITLI OLARAK farklı bir bölüm
numaralandırmasına sahiptir: bu mektupta sadece Intra-Commodity Spread
Charge değiştiği için "DEĞİŞEN" bloğunda tek başına "1)" numarasıyla
yer alır (ilk fixture'da PSR "1)" idi); ayrıca "DEĞİŞEN" bloğu VİOP
piyasa başlık satırını hiç içermez. Bu ikinci fixture, parser'ın
numaraya değil başlık metnine göre çalıştığını doğrulayan bir regresyon
testidir.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from bist_span.risk_params import RiskParams, RiskParamsStore, parse_takasbank_span_file

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "takasbank_span_sample.pdf"
FIXTURE_PATH_2 = Path(__file__).parent / "fixtures" / "takasbank_span_sample_2.pdf"


def test_parse_takasbank_span_file_returns_expected_columns():
    """Parse edilen DataFrame beklenen kolonları içermeli."""
    df = parse_takasbank_span_file(FIXTURE_PATH)
    expected_columns = {
        "ticker",
        "price_scan_range",
        "volatility_scan_range",
        "extreme_move_multiplier",
        "extreme_move_covered_fraction",
        "intra_commodity_spread_charge",
        "short_option_minimum",
    }
    assert expected_columns.issubset(df.columns)
    assert len(df) > 0


def test_parse_takasbank_span_file_akbnk_values():
    """AKBNK satırı, PDF'ten elle doğrulanmış değerlerle eşleşmeli."""
    df = parse_takasbank_span_file(FIXTURE_PATH)
    row = df[df["ticker"] == "AKBNK"].iloc[0]

    assert row["price_scan_range"] == pytest.approx(0.157)
    assert row["volatility_scan_range"] == pytest.approx(0.31)
    assert row["extreme_move_multiplier"] == pytest.approx(3.0)
    assert row["extreme_move_covered_fraction"] == pytest.approx(0.32)
    assert row["intra_commodity_spread_charge"] == pytest.approx(740.0)
    assert row["short_option_minimum"] == pytest.approx(405.0)


def test_parse_takasbank_span_file_handles_renumbered_sections():
    """Bölüm numaraları mektuptan mektuba değişse bile doğru alanlara okunmalı.

    İkinci fixture'da Intra-Commodity Spread Charge "1)" olarak numaralı
    (ilk fixture'da PSR "1)" idi) ve DEĞİŞEN bloğu VİOP başlık satırını
    içermiyor. AKBNK'nin intra_commodity_spread_charge'ı bu mektupta
    740'tan 810'a güncellenmiş; diğer alanlar (PSR/VSR/Extreme
    Move/SOM) değişmediği için "MEVCUTTA GEÇERLİ OLAN" bloğundan aynı
    kalmalı.
    """
    df = parse_takasbank_span_file(FIXTURE_PATH_2)
    row = df[df["ticker"] == "AKBNK"].iloc[0]

    assert row["intra_commodity_spread_charge"] == pytest.approx(810.0)
    assert row["price_scan_range"] == pytest.approx(0.157)
    assert row["volatility_scan_range"] == pytest.approx(0.31)
    assert row["extreme_move_multiplier"] == pytest.approx(3.0)
    assert row["extreme_move_covered_fraction"] == pytest.approx(0.32)
    assert row["short_option_minimum"] == pytest.approx(405.0)


def test_parse_takasbank_span_file_extreme_move_is_global():
    """Extreme Move Multiplier/Covered Fraction tüm hisseler için aynı olmalı.

    (Takasbank bu iki değeri hisse bazında değil, VİOP geneli için tek
    bir sabit olarak yayınlar.)
    """
    df = parse_takasbank_span_file(FIXTURE_PATH)
    assert df["extreme_move_multiplier"].nunique() == 1
    assert df["extreme_move_covered_fraction"].nunique() == 1


def test_risk_params_store_get_returns_loaded_params():
    """Depoya yüklenen bir RiskParams, get ile geri alınabilmeli."""
    store = RiskParamsStore()
    df = pd.DataFrame(
        [
            {
                "ticker": "AKBNK",
                "price_scan_range": 0.157,
                "volatility_scan_range": 0.31,
                "extreme_move_multiplier": 3.0,
                "extreme_move_covered_fraction": 0.32,
                "intra_commodity_spread_charge": 740.0,
                "short_option_minimum": 405.0,
            }
        ]
    )
    store.load_from_dataframe(df)
    params = store.get("AKBNK")
    assert isinstance(params, RiskParams)
    assert params.price_scan_range == 0.157


def test_risk_params_store_get_strips_is_suffix():
    """get('AKBNK.IS'), yfinance sembolleriyle doğrudan uyumlu olmalı."""
    store = RiskParamsStore()
    df = pd.DataFrame(
        [
            {
                "ticker": "AKBNK",
                "price_scan_range": 0.157,
                "volatility_scan_range": 0.31,
                "extreme_move_multiplier": 3.0,
                "extreme_move_covered_fraction": 0.32,
                "intra_commodity_spread_charge": 740.0,
                "short_option_minimum": 405.0,
            }
        ]
    )
    store.load_from_dataframe(df)
    assert store.get("AKBNK.IS").ticker == "AKBNK"


def test_risk_params_store_skips_incomplete_rows():
    """Zorunlu alanlardan biri eksikse (NaN) o satır depoya alınmamalı."""
    store = RiskParamsStore()
    df = pd.DataFrame(
        [
            {
                "ticker": "EKSIK",
                "price_scan_range": 0.1,
                "volatility_scan_range": None,  # opsiyonu yok, VSR bulunmuyor
                "extreme_move_multiplier": 3.0,
                "extreme_move_covered_fraction": 0.32,
                "intra_commodity_spread_charge": 100.0,
                "short_option_minimum": None,
            }
        ]
    )
    store.load_from_dataframe(df)
    assert len(store) == 0


def test_risk_params_store_get_missing_ticker_raises():
    """Depoda olmayan bir ticker için get anlamlı bir hata fırlatmalı."""
    store = RiskParamsStore()
    with pytest.raises(KeyError):
        store.get("YOK")


def test_risk_params_store_save_and_load_roundtrip(tmp_path):
    """save/load, depodaki içeriği kayıpsız geri getirmeli."""
    store = RiskParamsStore()
    df = pd.DataFrame(
        [
            {
                "ticker": "AKBNK",
                "price_scan_range": 0.157,
                "volatility_scan_range": 0.31,
                "extreme_move_multiplier": 3.0,
                "extreme_move_covered_fraction": 0.32,
                "intra_commodity_spread_charge": 740.0,
                "short_option_minimum": 405.0,
            }
        ]
    )
    store.load_from_dataframe(df)

    path = tmp_path / "risk_params.json"
    store.save(path)

    reloaded = RiskParamsStore()
    reloaded.load(path)
    assert reloaded.get("AKBNK") == store.get("AKBNK")
