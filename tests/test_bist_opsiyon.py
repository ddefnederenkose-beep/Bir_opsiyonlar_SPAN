"""BIST_Opsiyon.py için testler.

data_fetch (yfinance) çağrıları monkeypatch ile sahtelenir; risk
parametreleri için gerçek Takasbank fixture'ı kullanılır (bu hem gerçek
PDF parse akışını hem de tam SPAN hesabını uçtan uca test eder).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from bist_span import BIST_Opsiyon as main
from bist_span import data_fetch, takasbank_xml
from bist_span.BIST_Opsiyon import (
    SpanCalculationInput,
    _normalize_ticker,
    available_tickers,
    compute_span_result,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "takasbank_span_sample.pdf"


@pytest.fixture
def fake_price_data(monkeypatch):
    """data_fetch.get_price_data'yı ağa gitmeden sahte bir PriceData ile değiştirir.

    Ayrıca takasbank_xml.get_spot_price VE get_option_params'ı da
    (bulunamadı hatası fırlatacak şekilde) sahteler -- spot ve T artık
    ÖNCELİKLE Takasbank'tan geldiği için, bu olmadan testler gerçek ağa
    gidip (ya da diskteki güncel cache'e bağımlı kalıp) günden güne
    değişen gerçek fiyat/T'lerle deterministik olmaktan çıkardı. Bu
    fixture'ı kullanan testler böylece bilinçli olarak "Takasbank'ta
    bulunamadı, yfinance/takvim gününe düşüldü" senaryosunu test eder;
    Takasbank-öncelikli senaryolar ayrı testlerde (bkz.
    test_compute_span_result_prefers_takasbank_spot,
    test_compute_span_result_prefers_takasbank_time_to_expiry) açıkça
    mock'lanır.
    """

    def _fake_get_price_data(ticker: str, period: str = "1y"):
        return data_fetch.PriceData(
            ticker=ticker,
            current_price=62.0,
            history=pd.DataFrame({"Close": [60, 61, 62]}),
            historical_volatility=0.35,
            as_of=date.today(),
        )

    def _fake_get_spot_price_missing(ticker: str, today=None):
        raise KeyError(f"{ticker} test ortamında Takasbank'ta yok")

    def _fake_get_option_params_missing(ticker, expiry, strike, option_type):
        raise KeyError(f"{ticker} test ortamında Takasbank'ta yok")

    monkeypatch.setattr(data_fetch, "get_price_data", _fake_get_price_data)
    monkeypatch.setattr(main.data_fetch, "get_price_data", _fake_get_price_data)
    monkeypatch.setattr(takasbank_xml, "get_spot_price", _fake_get_spot_price_missing)
    monkeypatch.setattr(
        main.takasbank_xml, "get_spot_price", _fake_get_spot_price_missing
    )
    monkeypatch.setattr(
        takasbank_xml, "get_option_params", _fake_get_option_params_missing
    )
    monkeypatch.setattr(
        main.takasbank_xml, "get_option_params", _fake_get_option_params_missing
    )


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


def test_available_tickers_includes_supported_index_and_fx_symbols():
    """XU030 (XU030D opsiyonu) PDF'teki TEMEL ismiyle; USD/TRY ürünleri
    (USDTRYK, USDTRYKP) ise KENDİ GERÇEK adlarıyla listelenmeli --
    PDF'teki kısaltılmış "USDTRY" ismi artık DOĞRUDAN gösterilmiyor
    (XML'de USDTRY için iki ayrı ürün olduğundan tek bir isim yanıltıcı
    olurdu, bkz. takasbank_xml._XML_PFCODE_ALIASES).
    """
    tickers = available_tickers(FIXTURE_PATH)

    assert "AKBNK" in tickers
    assert "GARAN" in tickers
    assert "XU030" in tickers
    assert "USDTRYK" in tickers
    assert "USDTRYKP" in tickers
    assert "USDTRY" not in tickers
    assert tickers == sorted(tickers)


def test_get_risk_params_borrows_som_for_ticker_without_own_pdf_row():
    """USDTRYK/USDTRYKP'nin PDF'te kendi satırı yok -- USDTRY'nin SOM/ICS'ini ödünç almalı."""
    store = main._load_risk_params_store(FIXTURE_PATH)
    usdtry = store.get("USDTRY")

    for ticker in ("USDTRYK", "USDTRYKP"):
        borrowed = main._get_risk_params(store, ticker)
        assert borrowed.ticker == ticker  # ticker alanı isteneni yansıtmalı
        assert borrowed.short_option_minimum == usdtry.short_option_minimum
        assert borrowed.intra_commodity_spread_charge == usdtry.intra_commodity_spread_charge
        assert borrowed.price_scan_range == usdtry.price_scan_range

    # ".IS" sonekiyle çağrılsa da (compute_span_result'ın normalize ettiği gibi) çalışmalı
    borrowed_is = main._get_risk_params(store, "USDTRYKP.IS")
    assert borrowed_is.ticker == "USDTRYKP"


def test_non_equity_tickers_still_excludes_remaining_unsupported_symbols():
    """Henüz opsiyon serisi desteklenmeyen döviz/endeks sembolleri hâlâ hariç."""
    assert "EURTRY" in main._NON_EQUITY_TICKERS
    # "USDTRY" PDF'in KISALTILMIŞ ismi -- kendi opsiyon serisi yok, artık
    # doğrudan seçilebilir DEĞİL (gerçek ürünler USDTRYK/USDTRYKP).
    assert "USDTRY" in main._NON_EQUITY_TICKERS
    assert "XU030" not in main._NON_EQUITY_TICKERS  # artık destekleniyor


def test_compute_span_result_prefers_takasbank_spot_over_yfinance(
    fake_price_data, monkeypatch
):
    """Spot fiyat artık ÖNCELİKLE Takasbank'tan gelmeli, yfinance'e değil.

    fake_price_data fixture'ı Takasbank'ı "bulunamadı" yapıyor; burada
    açıkça Takasbank'ın GERÇEK bir değer döndürdüğü senaryoyu mock'layıp,
    yfinance'in (62.0) DEĞİL, Takasbank'ın (74.3) kullanıldığını doğruluyoruz.
    """

    def _fake_takasbank_spot(ticker, today=None):
        return takasbank_xml.TakasbankSpotPrice(
            ticker=ticker, price=74.3, source_date=date.today()
        )

    monkeypatch.setattr(main.takasbank_xml, "get_spot_price", _fake_takasbank_spot)

    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
    )
    result = compute_span_result(inputs)
    assert result["spot"] == pytest.approx(74.3)  # Takasbank, yfinance'in (62.0) önünde


def test_compute_span_result_falls_back_to_yfinance_when_takasbank_missing(
    fake_price_data,
):
    """Takasbank'ta bu hisse/gün bulunamazsa spot yfinance'e (fallback) düşmeli."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
    )
    result = compute_span_result(inputs)
    assert result["spot"] == 62.0  # fake_price_data fixture Takasbank'ı bulunamadı yapıyor


def test_compute_span_result_spot_override_beats_both_sources(
    fake_price_data, monkeypatch
):
    """spot_override verilmişse, Takasbank da yfinance de değil, o kullanılmalı."""

    def _fake_takasbank_spot(ticker, today=None):
        return takasbank_xml.TakasbankSpotPrice(
            ticker=ticker, price=74.3, source_date=date.today()
        )

    monkeypatch.setattr(main.takasbank_xml, "get_spot_price", _fake_takasbank_spot)

    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
        spot_override=100.0,
    )
    result = compute_span_result(inputs)
    assert result["spot"] == 100.0


def test_compute_span_result_uses_theoretical_base_price_by_default(fake_price_data):
    """market_price_override verilmezse, taban fiyat teorik Black-Scholes olmalı."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
    )
    result = compute_span_result(inputs)
    assert result["market_price"] is None
    assert result["scenarios"].attrs["current_price"] == pytest.approx(
        result["scenarios"].attrs["theoretical_price"]
    )


def test_compute_span_result_uses_market_price_when_given(fake_price_data):
    """market_price_override verilirse, 16 senaryonun "Fark" tabanı o olmalı
    (teorik fiyattan farklı olsa bile)."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
        market_price_override=1.72,
    )
    result = compute_span_result(inputs)
    assert result["market_price"] == 1.72
    assert result["scenarios"].attrs["current_price"] == pytest.approx(1.72)
    # Teorik fiyat referans olarak hâlâ ayrıca duruyor.
    assert result["scenarios"].attrs["theoretical_price"] != pytest.approx(1.72)


def test_compute_call_and_put_routes_market_price_per_side(fake_price_data):
    """compute_call_and_put, call/put market fiyatlarını doğru tarafa yönlendirmeli."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",  # yok sayılır
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
        call_market_price_override=1.72,
        put_market_price_override=1.03,
    )
    results = main.compute_call_and_put(inputs)
    assert results["call"]["market_price"] == 1.72
    assert results["put"]["market_price"] == 1.03


def test_compute_span_result_prefers_takasbank_time_to_expiry(
    fake_price_data, monkeypatch
):
    """T artık ÖNCELİKLE Takasbank XML'in kendi <t> alanından gelmeli.

    Takasbank'ın T'si takvim günü/365 İLE AYNI OLMAK ZORUNDA DEĞİLDİR --
    gerçek PC-SPAN Risk Array'iyle karşılaştırıldığında (bkz. proje sohbet
    geçmişi) bu ikisinin karıştırılması Scanning Risk'i belirgin ölçüde
    kaydırıyordu. fake_price_data fixture'ı get_option_params'ı "bulunamadı"
    yapıyor; burada açıkça Takasbank'ın GERÇEK bir T döndürdüğü senaryoyu
    mock'layıp, takvim hesabının (30/365) DEĞİL, Takasbank'ın (0.09189)
    kullanıldığını doğruluyoruz.
    """
    expiry = date.today() + timedelta(days=30)

    def _fake_takasbank_option_params(ticker, exp, strike, option_type):
        return takasbank_xml.TakasbankOptionParams(
            ticker=ticker,
            expiry=exp,
            strike=strike,
            option_type=option_type,
            time_to_expiry=0.09189,  # takvimden hesaplanan 30/365 = 0.08219'dan FARKLI
            risk_free_rate=0.38,
            implied_volatility=0.39,
            price_scan_range=0.157,
            volatility_scan_range=0.31,
            extreme_move_multiplier=3.0,
            extreme_move_covered_fraction=0.32,
            market_price=4.85,
            contract_size=100.0,
            source_date=date.today(),
        )

    monkeypatch.setattr(
        main.takasbank_xml, "get_option_params", _fake_takasbank_option_params
    )

    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=expiry,
        risk_params_file=FIXTURE_PATH,
    )
    result = compute_span_result(inputs)
    assert result["time_to_expiry"] == pytest.approx(0.09189)
    assert result["time_to_expiry"] != pytest.approx(30 / 365)


def test_compute_span_result_falls_back_to_calendar_time_to_expiry_when_takasbank_missing(
    fake_price_data,
):
    """Takasbank'ta bu strike/vade/tip bulunamazsa T takvim hesabına (fallback) düşmeli."""
    expiry = date.today() + timedelta(days=30)
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=expiry,
        risk_params_file=FIXTURE_PATH,
    )
    result = compute_span_result(inputs)
    # fake_price_data fixture'ı Takasbank'ı bulunamadı yapıyor
    assert result["time_to_expiry"] == pytest.approx(30 / 365)


def test_compute_span_result_time_to_expiry_override_beats_takasbank(
    fake_price_data, monkeypatch
):
    """time_to_expiry_override verilmişse, Takasbank'ın T'si de takvim de değil, o kullanılmalı."""

    def _fake_takasbank_option_params(ticker, exp, strike, option_type):
        return takasbank_xml.TakasbankOptionParams(
            ticker=ticker,
            expiry=exp,
            strike=strike,
            option_type=option_type,
            time_to_expiry=0.09189,
            risk_free_rate=0.38,
            implied_volatility=0.39,
            price_scan_range=0.157,
            volatility_scan_range=0.31,
            extreme_move_multiplier=3.0,
            extreme_move_covered_fraction=0.32,
            market_price=4.85,
            contract_size=100.0,
            source_date=date.today(),
        )

    monkeypatch.setattr(
        main.takasbank_xml, "get_option_params", _fake_takasbank_option_params
    )

    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
        time_to_expiry_override=0.1234,
    )
    result = compute_span_result(inputs)
    assert result["time_to_expiry"] == pytest.approx(0.1234)


def test_compute_span_result_includes_net_option_value_by_default(fake_price_data):
    """Varsayılan olarak (taşınan/bugün açılmamış pozisyon), Opsiyon Prim
    Değeri 0 olmalı ama Net Opsiyon Değeri her zaman hesaba katılmalı
    (Madde 33-38 -- bkz. proje sohbet geçmişi, PC-SPAN'la doğrulandı)."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
        market_price_override=1.72,
    )
    result = compute_span_result(inputs)
    span = result["span"]
    assert span["option_premium_value"] == 0.0
    # kısa 1 kontrat, piyasa fiyatı 1.72, kontrat çarpanı 100 -> -172.0
    assert span["net_option_value"] == pytest.approx(-172.0)
    bistech_margin_risk = max(
        span["short_option_minimum"],
        span["scan_risk"] + span["intra_commodity_spread_charge"] + span["delivery_risk"]
        - span["inter_commodity_spread_credit"],
    )
    assert span["total_initial_margin"] == pytest.approx(bistech_margin_risk + 172.0)


def test_compute_span_result_applies_option_premium_value_when_opened_today(fake_price_data):
    """position_opened_today=True + execution_price_override verilmişse,
    Opsiyon Prim Değeri (Madde 37/3) hesaba katılıp teminatı AZALTMALI."""
    base_inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-2,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
        market_price_override=1.72,
    )
    opened_today_inputs = replace(
        base_inputs, position_opened_today=True, execution_price_override=1.50
    )

    without_premium = compute_span_result(base_inputs)
    with_premium = compute_span_result(opened_today_inputs)

    # kısa 2 kontrat, işlem fiyatı 1.50, kontrat çarpanı 100 -> 300.0
    assert with_premium["span"]["option_premium_value"] == pytest.approx(300.0)
    assert without_premium["span"]["option_premium_value"] == 0.0
    assert with_premium["span"]["total_initial_margin"] == pytest.approx(
        without_premium["span"]["total_initial_margin"] - 300.0
    )


def test_compute_span_result_ignores_position_opened_today_without_execution_price(
    fake_price_data,
):
    """position_opened_today=True ama execution_price_override verilmemişse,
    Opsiyon Prim Değeri sessizce 0 kalmalı (uydurma bir fiyat kullanılmamalı)."""
    inputs = SpanCalculationInput(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-1,
        expiry=date.today() + timedelta(days=30),
        risk_params_file=FIXTURE_PATH,
        market_price_override=1.72,
        position_opened_today=True,
    )
    result = compute_span_result(inputs)
    assert result["span"]["option_premium_value"] == 0.0
