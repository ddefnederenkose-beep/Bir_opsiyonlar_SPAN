"""takasbank_xml.py için testler.

`tests/fixtures/takasbank_pcspan_sample.xml`, gerçek bir Takasbank
PC-SPAN EOD dosyasının yapısını birebir yansıtan KÜÇÜK, sentetik bir
örnektir (gerçek dosya ~65-70MB olduğu için repoya tam hâliyle
konulamaz). Yapı, gerçek dosya üzerinde ElementTree ile satır satır
keşfedilip doğrulanmıştır (bkz. proje sohbet geçmişi).

Ağa gerçekten giden testler @pytest.mark.network ile işaretlidir.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from bist_span import takasbank_xml as tbx

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "takasbank_pcspan_sample.xml"


def test_parse_global_extreme_move_reads_point_15_from_equity_group():
    """r=1 (hisse) grubunun point=15'inden EMM/Covered Fraction okunmalı.

    Dosyada r=2 (fx) grubu da var (mult=5.0, weight=0.5) -- yanlışlıkla
    o okunmadığından emin oluyoruz.
    """
    multiplier, covered_fraction = tbx._parse_global_extreme_move(FIXTURE_PATH)
    assert multiplier == pytest.approx(3.0)
    assert covered_fraction == pytest.approx(0.32)


def test_parse_products_only_includes_equity_products():
    """Sadece valueMeth=EQTY ürünler dahil edilmeli (USDTRY=FX hariç tutulmalı)."""
    products, _spots = tbx._parse_products_and_spots(FIXTURE_PATH)
    assert "AKBNK" in products
    assert "USDTRY" not in products


def test_parse_products_psr_divided_by_100_but_vsr_not():
    """PSR yüzde olarak gelir (/100 gerekir), VSR zaten ondalıktır (/100 gerekmez)."""
    products, _spots = tbx._parse_products_and_spots(FIXTURE_PATH)
    series = products["AKBNK"]["20260831"]
    assert series["psr"] == pytest.approx(0.157)  # 15.7 / 100
    assert series["vsr"] == pytest.approx(0.31)  # zaten ondalık
    assert series["t"] == pytest.approx(0.012438)
    assert series["intrRate"] == pytest.approx(0.38)


def test_parse_products_captures_all_expiries_and_options():
    """Birden fazla vade ve strike doğru şekilde ayrıştırılmalı."""
    products, _spots = tbx._parse_products_and_spots(FIXTURE_PATH)
    akbnk = products["AKBNK"]
    assert set(akbnk) == {"20260831", "20260928"}

    near_options = {(o["k"], o["o"]) for o in akbnk["20260831"]["options"]}
    assert near_options == {(52.0, "P"), (56.0, "C")}

    far_options = {(o["k"], o["o"]) for o in akbnk["20260928"]["options"]}
    assert far_options == {(65.0, "C")}


def test_parse_products_captures_market_price():
    """opt/p (piyasa fiyatı) her opsiyon için doğru okunmalı."""
    products, _spots = tbx._parse_products_and_spots(FIXTURE_PATH)
    options = products["AKBNK"]["20260831"]["options"]
    prices = {(o["k"], o["o"]): o["p"] for o in options}
    assert prices[(52.0, "P")] == pytest.approx(0.01)
    assert prices[(56.0, "C")] == pytest.approx(1.20)


def test_parse_spots_reads_phypf_price():
    """phyPf/phy/p'den spot fiyatı doğru okunmalı."""
    _products, spots = tbx._parse_products_and_spots(FIXTURE_PATH)
    assert spots["AKBNK"] == pytest.approx(74.3)


def test_parse_spots_excludes_zero_price_instruments():
    """p=0.0 olan (pasif/kotasyonsuz) enstrümanlar spot_prices'a dahil edilmemeli."""
    _products, spots = tbx._parse_products_and_spots(FIXTURE_PATH)
    assert "AAPBNP" not in spots


def test_build_distilled_cache_combines_global_products_and_spots():
    distilled = tbx.build_distilled_cache(FIXTURE_PATH)
    assert distilled["global"]["extreme_move_multiplier"] == pytest.approx(3.0)
    assert distilled["global"]["extreme_move_covered_fraction"] == pytest.approx(0.32)
    assert "AKBNK" in distilled["products"]
    assert distilled["spot_prices"]["AKBNK"] == pytest.approx(74.3)


def test_get_option_params_end_to_end(tmp_path, monkeypatch):
    """get_option_params, gerçek ağ/indirme olmadan uçtan uca çalışmalı."""
    monkeypatch.setattr(tbx, "DISTILLED_DIR", tmp_path)

    trading_day = date(2026, 8, 25)
    distilled = tbx.build_distilled_cache(FIXTURE_PATH)
    (tmp_path / f"{trading_day.strftime('%y%m%d')}.json").write_text(
        __import__("json").dumps(distilled)
    )

    params = tbx.get_option_params(
        ticker="AKBNK.IS",  # ".IS" soneki temizlenmeli
        expiry=date(2026, 8, 31),
        strike=56.0,
        option_type="call",
        today=trading_day,
    )

    assert params.ticker == "AKBNK"
    assert params.time_to_expiry == pytest.approx(0.012438)
    assert params.risk_free_rate == pytest.approx(0.38)
    assert params.implied_volatility == pytest.approx(0.439181)
    assert params.price_scan_range == pytest.approx(0.157)
    assert params.volatility_scan_range == pytest.approx(0.31)
    assert params.extreme_move_multiplier == pytest.approx(3.0)
    assert params.extreme_move_covered_fraction == pytest.approx(0.32)
    assert params.market_price == pytest.approx(1.20)
    assert params.source_date == trading_day


def test_get_spot_price_end_to_end(tmp_path, monkeypatch):
    """get_spot_price, gerçek ağ/indirme olmadan uçtan uca çalışmalı."""
    monkeypatch.setattr(tbx, "DISTILLED_DIR", tmp_path)
    trading_day = date(2026, 8, 25)
    distilled = tbx.build_distilled_cache(FIXTURE_PATH)
    (tmp_path / f"{trading_day.strftime('%y%m%d')}.json").write_text(
        __import__("json").dumps(distilled)
    )

    spot = tbx.get_spot_price("AKBNK.IS", today=trading_day)
    assert spot.ticker == "AKBNK"
    assert spot.price == pytest.approx(74.3)
    assert spot.source_date == trading_day


def test_get_spot_price_unknown_ticker_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tbx, "DISTILLED_DIR", tmp_path)
    trading_day = date(2026, 8, 25)
    distilled = tbx.build_distilled_cache(FIXTURE_PATH)
    (tmp_path / f"{trading_day.strftime('%y%m%d')}.json").write_text(
        __import__("json").dumps(distilled)
    )

    with pytest.raises(KeyError, match="YOKHISSE"):
        tbx.get_spot_price("YOKHISSE", today=trading_day)


def test_get_option_params_unknown_ticker_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tbx, "DISTILLED_DIR", tmp_path)
    trading_day = date(2026, 8, 25)
    distilled = tbx.build_distilled_cache(FIXTURE_PATH)
    (tmp_path / f"{trading_day.strftime('%y%m%d')}.json").write_text(
        __import__("json").dumps(distilled)
    )

    with pytest.raises(KeyError, match="YOKHISSE"):
        tbx.get_option_params(
            ticker="YOKHISSE",
            expiry=date(2026, 8, 31),
            strike=56.0,
            option_type="call",
            today=trading_day,
        )


def test_get_option_params_unknown_strike_raises_with_available_list(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(tbx, "DISTILLED_DIR", tmp_path)
    trading_day = date(2026, 8, 25)
    distilled = tbx.build_distilled_cache(FIXTURE_PATH)
    (tmp_path / f"{trading_day.strftime('%y%m%d')}.json").write_text(
        __import__("json").dumps(distilled)
    )

    with pytest.raises(ValueError, match="56.0"):
        tbx.get_option_params(
            ticker="AKBNK",
            expiry=date(2026, 8, 31),
            strike=999.0,
            option_type="call",
            today=trading_day,
        )


def test_pick_best_file_prefers_eod_over_int():
    files = [
        "TAKASINT_-CCP__-BI-_____-260826-001.zip",
        "TAKASINT_-CCP__-BI-_____-260826-009.zip",
        "TAKASEOD_-CCP__-BI-_____-260826-001.zip",
    ]
    assert tbx._pick_best_file(files) == "TAKASEOD_-CCP__-BI-_____-260826-001.zip"


def test_pick_best_file_picks_highest_numbered_int_when_no_eod():
    files = [
        "TAKASINT_-CCP__-BI-_____-260826-001.zip",
        "TAKASINT_-CCP__-BI-_____-260826-009.zip",
        "TAKASINT_-CCP__-BI-_____-260826-002.zip",
    ]
    assert tbx._pick_best_file(files) == "TAKASINT_-CCP__-BI-_____-260826-009.zip"


def test_pick_best_file_raises_on_empty_list():
    with pytest.raises(ValueError):
        tbx._pick_best_file([])


@pytest.mark.network
def test_find_latest_trading_day_real_server():
    """Gerçek Takasbank sunucusuna karşı: bugün ya da geriye doğru bir gün bulunmalı."""
    d = tbx.find_latest_trading_day()
    assert d <= date.today()
    assert (date.today() - d).days < 7


@pytest.mark.network
def test_get_option_params_real_akbnk():
    """Gerçek Takasbank verisiyle uçtan uca: AKBNK için bugünkü/son parametreler.

    Strike'ı bilmediğimiz için önce mevcut strike'ları öğrenmek üzere
    bilerek var olmayan bir strike deneyip hata mesajından gerçek bir
    strike/vade çekiyoruz, sonra onunla asıl çağrıyı yapıyoruz.
    """
    trading_day, cache_path = tbx.ensure_daily_cache()
    import json as _json

    distilled = _json.loads(cache_path.read_text())
    assert "AKBNK" in distilled["products"]
    first_expiry = next(iter(distilled["products"]["AKBNK"]))
    first_opt = distilled["products"]["AKBNK"][first_expiry]["options"][0]

    params = tbx.get_option_params(
        ticker="AKBNK",
        expiry=date(
            int(first_expiry[:4]), int(first_expiry[4:6]), int(first_expiry[6:8])
        ),
        strike=first_opt["k"],
        option_type="call" if first_opt["o"] == "C" else "put",
    )
    assert params.time_to_expiry > 0
    assert 0 < params.implied_volatility < 5
    assert params.market_price >= 0
    assert params.source_date == trading_day

    spot = tbx.get_spot_price("AKBNK")
    assert spot.price > 0
    assert spot.source_date == trading_day
