"""futures_xml.py için testler.

`tests/fixtures/takasbank_futures_sample.xml`, gerçek bir Takasbank
PC-SPAN dosyasının vadeli işlem (futPf/fut) yapısını birebir yansıtan
küçük, sentetik bir örnektir (bkz. modülün ve bu dosyanın keşif
notları -- gerçek AEFES verisiyle çapraz doğrulanmıştır).

Ağa gerçekten giden testler @pytest.mark.network ile işaretlidir.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from bist_span import futures_xml as fx
from bist_span import takasbank_xml as tbx

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "takasbank_futures_sample.xml"


def test_is_real_futures_ticker_excludes_c_family():
    assert fx._is_real_futures_ticker("AEFES") is True
    assert fx._is_real_futures_ticker("AEFES_C") is False
    assert fx._is_real_futures_ticker("A1CAPR_C") is False


def test_parse_futures_only_includes_fut_valuemeth():
    """Sadece valueMeth=FUT ürünler dahil edilmeli (opsiyon (EQTY) hariç tutulmalı)."""
    products = fx._parse_futures(FIXTURE_PATH)
    assert "AEFES" in products
    # oopPf (opsiyon) bloğu futPf değil, hiç görünmemeli
    assert set(products["AEFES"].keys()) == {"20260831", "20260930"}


def test_parse_futures_excludes_c_family():
    products = fx._parse_futures(FIXTURE_PATH)
    assert "AEFES_C" not in products


def test_parse_futures_excludes_inactive_zero_price():
    products = fx._parse_futures(FIXTURE_PATH)
    assert "INACTIVE" not in products


def test_parse_futures_includes_other_families_unfiltered():
    """_parse_futures ham listeyi döner -- 'sadece hisse evreni' filtresi çağıran tarafta."""
    products = fx._parse_futures(FIXTURE_PATH)
    assert "TESTFX" in products


def test_parse_futures_captures_fields_correctly():
    products = fx._parse_futures(FIXTURE_PATH)
    aug = products["AEFES"]["20260831"]
    assert aug["p"] == pytest.approx(18.62)
    assert aug["t"] == pytest.approx(0.008219)
    assert aug["cvf"] == pytest.approx(100.0)
    assert aug["r"] == "1"
    assert aug["psr"] == pytest.approx(0.141)
    assert aug["vsr"] == pytest.approx(0.0)


def test_parse_extreme_move_for_group_reads_correct_r():
    """r=1 ve r=2 FARKLI EMM/ECF taşıyor -- doğru grup okunmalı (hardcoded r=1 değil)."""
    emm1, ecf1 = fx._parse_extreme_move_for_group(FIXTURE_PATH, "1")
    assert emm1 == pytest.approx(3.0)
    assert ecf1 == pytest.approx(0.32)

    emm2, ecf2 = fx._parse_extreme_move_for_group(FIXTURE_PATH, "2")
    assert emm2 == pytest.approx(2.5)
    assert ecf2 == pytest.approx(0.5)


def test_build_futures_distilled_cache_combines_products_and_extreme_moves():
    distilled = fx.build_futures_distilled_cache(FIXTURE_PATH)
    assert "AEFES" in distilled["products"]
    assert distilled["extreme_move_by_group"]["1"]["emm"] == pytest.approx(3.0)
    assert distilled["extreme_move_by_group"]["2"]["emm"] == pytest.approx(2.5)


def _seed_futures_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trading_day: date) -> None:
    """ensure_futures_daily_cache'i gerçek ağa hiç dokunmadan test edebilmek
    için takasbank_xml.ensure_daily_cache'i (DEĞİŞTİRMEDEN) sahteler."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    xml_path = raw_dir / f"{trading_day.strftime('%y%m%d')}.xml"
    xml_path.write_text(FIXTURE_PATH.read_text())

    def _fake_ensure_daily_cache(today=None):
        return trading_day, tmp_path / "distilled" / f"{trading_day.strftime('%y%m%d')}.json"

    monkeypatch.setattr(tbx, "ensure_daily_cache", _fake_ensure_daily_cache)
    monkeypatch.setattr(tbx, "RAW_DIR", raw_dir)
    monkeypatch.setattr(fx, "FUTURES_DISTILLED_DIR", tmp_path / "futures_distilled")


def test_list_futures_tickers_end_to_end(tmp_path, monkeypatch):
    trading_day = date(2026, 8, 28)
    _seed_futures_cache(tmp_path, monkeypatch, trading_day)

    tickers = fx.list_futures_tickers(today=trading_day)
    assert "AEFES" in tickers
    assert "AEFES_C" not in tickers
    assert "INACTIVE" not in tickers


def test_list_futures_expiries_end_to_end(tmp_path, monkeypatch):
    trading_day = date(2026, 8, 28)
    _seed_futures_cache(tmp_path, monkeypatch, trading_day)

    expiries = fx.list_futures_expiries("AEFES.IS", today=trading_day)
    assert expiries == [date(2026, 8, 31), date(2026, 9, 30)]


def test_get_futures_params_end_to_end(tmp_path, monkeypatch):
    trading_day = date(2026, 8, 28)
    _seed_futures_cache(tmp_path, monkeypatch, trading_day)

    params = fx.get_futures_params("AEFES.IS", date(2026, 8, 31), today=trading_day)
    assert params.ticker == "AEFES"
    assert params.price == pytest.approx(18.62)
    assert params.time_to_expiry == pytest.approx(0.008219)
    assert params.contract_size == pytest.approx(100.0)
    assert params.price_scan_range == pytest.approx(0.141)
    assert params.volatility_scan_range == pytest.approx(0.0)
    assert params.extreme_move_multiplier == pytest.approx(3.0)
    assert params.extreme_move_covered_fraction == pytest.approx(0.32)
    assert params.source_date == trading_day


def test_get_futures_params_uses_correct_extreme_move_group(tmp_path, monkeypatch):
    """TESTFX r=2 kullanıyor -- r=1'in (3.0/0.32) DEĞİL, r=2'nin (2.5/0.5) EMM/ECF'i dönmeli."""
    trading_day = date(2026, 8, 28)
    _seed_futures_cache(tmp_path, monkeypatch, trading_day)

    params = fx.get_futures_params("TESTFX", date(2026, 9, 30), today=trading_day)
    assert params.extreme_move_multiplier == pytest.approx(2.5)
    assert params.extreme_move_covered_fraction == pytest.approx(0.5)


def test_get_futures_params_unknown_ticker_raises(tmp_path, monkeypatch):
    trading_day = date(2026, 8, 28)
    _seed_futures_cache(tmp_path, monkeypatch, trading_day)

    with pytest.raises(KeyError, match="YOKTICKER"):
        fx.get_futures_params("YOKTICKER", date(2026, 8, 31), today=trading_day)


def test_ensure_futures_daily_cache_rebuilds_when_raw_xml_is_newer(tmp_path, monkeypatch):
    """Opsiyon tarafı ham XML'i (ör. yeni bir INT/EOD dosyasıyla) tazelediğinde,
    vadeli işlem cache'i de otomatik senkron kalmalı."""
    import time

    trading_day = date(2026, 8, 28)
    _seed_futures_cache(tmp_path, monkeypatch, trading_day)

    _, cache_path1 = fx.ensure_futures_daily_cache(today=trading_day)
    first_mtime = cache_path1.stat().st_mtime

    # ham XML'i "daha yeni" yap (opsiyon tarafının tazelemesini simüle eder)
    raw_xml_path = tbx.RAW_DIR / f"{trading_day.strftime('%y%m%d')}.xml"
    time.sleep(0.01)
    raw_xml_path.write_text(FIXTURE_PATH.read_text())

    _, cache_path2 = fx.ensure_futures_daily_cache(today=trading_day)
    assert cache_path2.stat().st_mtime > first_mtime


@pytest.mark.network
def test_get_futures_params_real_aefes():
    """Gerçek ağ/indirme ile: AEFES'in gerçek bir vadesi/fiyatı olmalı."""
    expiries = fx.list_futures_expiries("AEFES")
    assert len(expiries) > 0
    params = fx.get_futures_params("AEFES", expiries[0])
    assert params.price > 0
    assert params.time_to_expiry > 0
    assert params.contract_size == pytest.approx(100.0)
    assert params.volatility_scan_range == pytest.approx(0.0)
