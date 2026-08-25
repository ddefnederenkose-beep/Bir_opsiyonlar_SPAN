"""main.py için testler.

data_fetch (yfinance) çağrıları monkeypatch ile sahtelenir; risk
parametreleri için gerçek Takasbank fixture'ı kullanılır (bu hem gerçek
PDF parse akışını hem de tam SPAN hesabını uçtan uca test eder).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from bist_span import data_fetch, main
from bist_span.main import (
    SpanCalculationInput,
    _normalize_ticker,
    available_tickers,
    compute_span_result,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "takasbank_span_sample.pdf"


@pytest.fixture
def fake_price_data(monkeypatch):
    """data_fetch.get_price_data'yı ağa gitmeden sahte bir PriceData ile değiştirir."""

    def _fake_get_price_data(ticker: str, period: str = "1y"):
        return data_fetch.PriceData(
            ticker=ticker,
            current_price=62.0,
            history=pd.DataFrame({"Close": [60, 61, 62]}),
            historical_volatility=0.35,
            as_of=date.today(),
        )

    monkeypatch.setattr(data_fetch, "get_price_data", _fake_get_price_data)
    monkeypatch.setattr(main.data_fetch, "get_price_data", _fake_get_price_data)


@pytest.mark.parametrize(
    "raw, expected", [("AKBNK", "AKBNK.IS"), ("akbnk.is", "AKBNK.IS"), ("GARAN.IS", "GARAN.IS")]
)
def test_normalize_ticker(raw, expected):
    assert _normalize_ticker(raw) == expected


def test_compute_span_result_end_to_end(fake_price_data):
    """CLI/Streamlit'in ortak çekirdeği, tüm katmanları doğru sırayla çağırmalı."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-10,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
    )
    result = compute_span_result(inputs)

    assert result["spot"] == 62.0
    assert result["volatility"] == 0.35
    assert result["risk_params"].ticker == "AKBNK"
    assert result["span"]["total_initial_margin"] >= result["span"]["short_option_minimum"]


def test_compute_span_result_rejects_past_expiry(fake_price_data):
    """Geçmiş bir vade tarihi anlamlı bir hata fırlatmalı."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-10,
        expiry=date.today() - timedelta(days=1),
        risk_params_file=FIXTURE_PATH,
    )
    with pytest.raises(ValueError):
        compute_span_result(inputs)


def test_compute_span_result_unknown_ticker_raises(fake_price_data):
    """Risk parametre deposunda olmayan bir hisse KeyError fırlatmalı."""
    inputs = SpanCalculationInput(
        ticker="YOKBORSA",
        strike=10,
        option_type="put",
        contracts=5,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
    )
    with pytest.raises(KeyError):
        compute_span_result(inputs)


def test_run_cli_prints_table(fake_price_data, capsys):
    """run_cli, sonucu stdout'a bir tablo olarak bastırmalı."""
    expiry = (date.today() + timedelta(days=30)).isoformat()
    main.run_cli(
        [
            "--ticker",
            "AKBNK.IS",
            "--strike",
            "65",
            "--option-type",
            "call",
            "--contracts",
            "-10",
            "--expiry",
            expiry,
            "--risk-params-file",
            str(FIXTURE_PATH),
        ]
    )
    captured = capsys.readouterr()
    assert "TOPLAM BAŞLANGIÇ TEMİNATI" in captured.out
    assert "AKBNK" in captured.out


def test_run_cli_without_option_type_computes_both(fake_price_data, capsys):
    """--option-type verilmezse hem call hem put karşılaştırmalı basılmalı."""
    expiry = (date.today() + timedelta(days=30)).isoformat()
    main.run_cli(
        [
            "--ticker",
            "AKBNK.IS",
            "--strike",
            "65",
            "--expiry",
            expiry,
            "--risk-params-file",
            str(FIXTURE_PATH),
        ]
    )
    captured = capsys.readouterr()
    assert "Call" in captured.out
    assert "Put" in captured.out
    assert "TOPLAM BAŞLANGIÇ TEMİNATI" in captured.out
    assert "SPAN Senaryosu" in captured.out  # senaryo tablosu varsayılan olarak basılır


def test_run_cli_hide_scenarios_flag_suppresses_scenario_table(fake_price_data, capsys):
    """--hide-scenarios verilirse senaryo dökümü basılmamalı."""
    expiry = (date.today() + timedelta(days=30)).isoformat()
    main.run_cli(
        [
            "--ticker",
            "AKBNK.IS",
            "--strike",
            "65",
            "--option-type",
            "call",
            "--expiry",
            expiry,
            "--risk-params-file",
            str(FIXTURE_PATH),
            "--hide-scenarios",
        ]
    )
    captured = capsys.readouterr()
    assert "TOPLAM BAŞLANGIÇ TEMİNATI" in captured.out
    assert "SPAN Senaryosu" not in captured.out


def test_compute_call_and_put_returns_both_sides(fake_price_data):
    """compute_call_and_put, option_type'tan bağımsız olarak ikisini de döner."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="put",  # yok sayılmalı
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
    )
    results = main.compute_call_and_put(inputs)

    assert set(results) == {"call", "put"}
    assert results["call"]["span"]["total_initial_margin"] > 0
    assert results["put"]["span"]["total_initial_margin"] > 0


def test_compute_span_result_applies_risk_param_overrides(fake_price_data):
    """Verilen override'lar, PDF'ten gelen otomatik değerlerin yerine geçmeli."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
        spot_override=100.0,
        volatility_override=0.50,
        price_scan_range_override=0.20,
        volatility_scan_range_override=0.40,
        extreme_move_multiplier_override=5.0,
        extreme_move_covered_fraction_override=0.50,
        intra_commodity_spread_charge_override=999.0,
        short_option_minimum_override=1234.0,
    )
    result = compute_span_result(inputs)

    assert result["spot"] == 100.0
    assert result["volatility"] == 0.50
    rp = result["risk_params"]
    assert rp.price_scan_range == 0.20
    assert rp.volatility_scan_range == 0.40
    assert rp.extreme_move_multiplier == 5.0
    assert rp.extreme_move_covered_fraction == 0.50
    assert rp.short_option_minimum == 1234.0
    # Kontrat sayısı -1 (1 kısa kontrat) olduğu için SOM = short_option_minimum override'ı.
    assert result["span"]["short_option_minimum"] == 1234.0
    # intra_commodity_spread_charge_override, risk_params'ı DEĞİL, formüle
    # fiilen uygulanan ücreti kontrol eder (bkz. test_intra_commodity_spread_charge_*).
    assert rp.intra_commodity_spread_charge != 999.0  # PDF'teki değer korunur
    assert result["span"]["intra_commodity_spread_charge"] == 999.0


def test_intra_commodity_spread_charge_defaults_to_zero(fake_price_data):
    """Override verilmezse spread ücreti asla otomatik uygulanmamalı (bug fix).

    Tek bacaklı/tek vadeli bir pozisyonda spread olamaz; Takasbank'ın o
    hisse için yayınladığı referans değer (risk_params'ta hâlâ mevcut ve
    bilgi amaçlı) SPAN formülüne otomatik eklenmemeli.
    """
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
    )
    result = compute_span_result(inputs)

    assert result["risk_params"].intra_commodity_spread_charge > 0  # PDF'ten geldi
    assert result["span"]["intra_commodity_spread_charge"] == 0.0  # ama uygulanmadı


def test_intra_commodity_spread_charge_applies_when_explicitly_given(fake_price_data):
    """Kullanıcı açıkça bir spread ücreti girerse, o zaman formüle eklenmeli."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
        intra_commodity_spread_charge_override=250.0,
    )
    result = compute_span_result(inputs)

    assert result["span"]["intra_commodity_spread_charge"] == 250.0


def test_compute_span_result_without_overrides_uses_parsed_values(fake_price_data):
    """Hiç override verilmezse PDF'ten parse edilen değerler aynen kullanılmalı."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
    )
    result = compute_span_result(inputs)
    rp = result["risk_params"]
    assert rp.price_scan_range == pytest.approx(0.157)
    assert rp.short_option_minimum == pytest.approx(405.0)


def test_compute_span_result_includes_scenario_table(fake_price_data):
    """Dönen dict, kullanıcının Excel'iyle (THYAO_SPAN_Hesaplama) birebir aynı
    sütun yapısında 16 senaryonun P&L dökümünü de içermeli."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
    )
    result = compute_span_result(inputs)
    scenarios = result["scenarios"]

    assert len(scenarios) == 16
    assert {
        "Sen.",
        "Açıklama",
        "Fiyat Çarpanı",
        "Vol Yönü",
        "S_yeni",
        "IV_yeni",
        "Call Fiyatı",
        "Fark",
        "Kısa K/Z (TL)",
    }.issubset(scenarios.columns)
    # En kötü K/Z, span sonucundaki scan_risk ile eşleşmeli (işaret ters).
    worst_pnl = scenarios["Kısa K/Z (TL)"].min()
    assert -worst_pnl == pytest.approx(result["span"]["scan_risk"], abs=0.01)
    # Excel'deki "Aktif Senaryo #" ile aynı: en kötü satırın Sen. numarası.
    worst_row = scenarios.loc[scenarios["Kısa K/Z (TL)"].idxmin()]
    assert scenarios.attrs["worst_scenario_no"] == worst_row["Sen."]


def test_available_tickers_excludes_non_equity_symbols():
    """available_tickers, USDTRY/XU030 gibi hisse olmayan sembolleri hariç tutmalı."""
    tickers = available_tickers(FIXTURE_PATH)

    assert "AKBNK" in tickers
    assert "GARAN" in tickers
    assert "USDTRY" not in tickers
    assert "XU030" not in tickers
    assert tickers == sorted(tickers)
