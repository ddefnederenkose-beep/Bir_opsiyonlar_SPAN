"""takasbank_xml.py için testler.

`tests/fixtures/takasbank_pcspan_sample.xml`, gerçek bir Takasbank
PC-SPAN EOD dosyasının yapısını birebir yansıtan KÜÇÜK, sentetik bir
örnektir (gerçek dosya ~65-70MB olduğu için repoya tam hâliyle
konulamaz). Yapı, gerçek dosya üzerinde ElementTree ile satır satır
keşfedilip doğrulanmıştır (bkz. proje sohbet geçmişi).

Ağa gerçekten giden testler @pytest.mark.network ile işaretlidir.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
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
    """Sadece valueMeth=EQTY ürünler dahil edilmeli (TESTFUT=FUT hariç tutulmalı)."""
    products, _spots = tbx._parse_products_and_spots(FIXTURE_PATH)
    assert "AKBNK" in products
    assert "TESTFUT" not in products


def test_parse_products_includes_index_and_fx_options_tagged_eqty():
    """XU030D (endeks) ve USDTRYKP (döviz) de valueMeth=EQTY ise dahil edilmeli.

    Gerçek Takasbank verisinde doğrulanmıştır: bu ürünler valueMeth=EQTY
    etiketiyle geliyor (isme rağmen "hisse" anlamında değil, "standart
    opsiyon değerleme metodolojisi" anlamında) -- filtre valueMeth'e
    bakar, ticker adına değil.
    """
    products, _spots = tbx._parse_products_and_spots(FIXTURE_PATH)
    assert "XU030D" in products
    assert "USDTRYKP" in products


def test_parse_products_captures_contract_size_from_cvf():
    """cvf (kontrat çarpanı) hisse dışı ürünlerde 100 DEĞİLDİR -- doğru okunmalı."""
    products, _spots = tbx._parse_products_and_spots(FIXTURE_PATH)
    akbnk_opt = products["AKBNK"]["20260831"]["options"][0]
    xu030d_opt = products["XU030D"]["20260831"]["options"][0]
    usdtrykp_opt = products["USDTRYKP"]["20260831"]["options"][0]
    assert akbnk_opt["cvf"] == pytest.approx(100.0)
    assert xu030d_opt["cvf"] == pytest.approx(10.0)
    assert usdtrykp_opt["cvf"] == pytest.approx(1.0)


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
    assert params.contract_size == pytest.approx(100.0)
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


def test_to_xml_pfcode_maps_known_aliases_and_passes_through_others():
    assert tbx.to_xml_pfcode("XU030") == "XU030D"
    assert tbx.to_xml_pfcode("USDTRY") == "USDTRYKP"
    assert tbx.to_xml_pfcode("AKBNK") == "AKBNK"  # eşleşme yoksa aynen döner


def test_get_spot_price_resolves_alias_to_real_option_pfcode(tmp_path, monkeypatch):
    """get_spot_price('USDTRY') PDF/temel isim -- ama gerçek opsiyon serisi
    (ve strike'larla AYNI ölçekteki spot) USDTRYKP'nin phyPf'inde. Fixture'da
    ikisi FARKLI değerlerde (48.1025 vs 48102.5) -- alias doğru
    uygulanmazsa yanlış (1000x küçük) spot dönerdi."""
    monkeypatch.setattr(tbx, "DISTILLED_DIR", tmp_path)
    trading_day = date(2026, 8, 25)
    distilled = tbx.build_distilled_cache(FIXTURE_PATH)
    (tmp_path / f"{trading_day.strftime('%y%m%d')}.json").write_text(
        __import__("json").dumps(distilled)
    )

    spot = tbx.get_spot_price("USDTRY", today=trading_day)
    assert spot.ticker == "USDTRY"  # kullanıcıya hep TEMEL isim döner
    assert spot.price == pytest.approx(48102.5)  # USDTRYKP'nin (strike'larla tutarlı) fiyatı

    spot_index = tbx.get_spot_price("XU030", today=trading_day)
    assert spot_index.price == pytest.approx(16971.54)


def test_get_option_params_resolves_alias_for_index_and_fx(tmp_path, monkeypatch):
    """get_option_params('XU030', ...) / ('USDTRY', ...) gerçek XML pfCode'una
    (XU030D/USDTRYKP) çevrilmeli ve doğru contract_size'ı taşımalı."""
    monkeypatch.setattr(tbx, "DISTILLED_DIR", tmp_path)
    trading_day = date(2026, 8, 25)
    distilled = tbx.build_distilled_cache(FIXTURE_PATH)
    (tmp_path / f"{trading_day.strftime('%y%m%d')}.json").write_text(
        __import__("json").dumps(distilled)
    )

    fx = tbx.get_option_params(
        "USDTRY", date(2026, 8, 31), 48500.0, "call", today=trading_day
    )
    assert fx.ticker == "USDTRY"
    assert fx.market_price == pytest.approx(36.6)
    assert fx.contract_size == pytest.approx(1.0)
    assert fx.price_scan_range == pytest.approx(0.11)

    index = tbx.get_option_params(
        "XU030", date(2026, 8, 31), 14250.0, "put", today=trading_day
    )
    assert index.ticker == "XU030"
    assert index.market_price == pytest.approx(7.14)
    assert index.contract_size == pytest.approx(10.0)
    assert index.price_scan_range == pytest.approx(0.114)


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


def _seed_distilled_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    trading_day: date,
    source_file: str,
    is_final: bool,
    age_seconds: float,
    published_at: str | None = None,
) -> Path:
    """Testler için diskte hazır bir damıtılmış cache dosyası kurar.

    ensure_daily_cache'in "cache zaten var" dalını, gerçek ağa hiç
    dokunmadan test edebilmek için kullanılır -- bkz. aşağıdaki
    test_ensure_daily_cache_* testleri.
    """
    monkeypatch.setattr(tbx, "DISTILLED_DIR", tmp_path / "distilled")
    monkeypatch.setattr(tbx, "RAW_DIR", tmp_path / "raw")
    tbx.DISTILLED_DIR.mkdir(parents=True)
    cache_path = tbx._distilled_cache_path(trading_day)
    cache_path.write_text(
        json.dumps(
            {
                "global": {
                    "extreme_move_multiplier": 3.0,
                    "extreme_move_covered_fraction": 0.32,
                },
                "products": {},
                "spot_prices": {},
                "source_file": source_file,
                "is_final": is_final,
                "published_at": published_at,
            }
        )
    )
    stale_time = time.time() - age_seconds
    os.utime(cache_path, (stale_time, stale_time))
    return cache_path


def test_ensure_daily_cache_eod_cache_never_rechecked(tmp_path, monkeypatch):
    """is_final=True (EOD'dan üretilmiş) bir cache, ne kadar eski olursa olsun tekrar ağa sorulmamalı."""
    trading_day = date(2026, 8, 26)
    monkeypatch.setattr(tbx, "find_latest_trading_day", lambda today=None: trading_day)
    cache_path = _seed_distilled_cache(
        tmp_path, monkeypatch, trading_day, "TAKASEOD_x-001.zip", True, age_seconds=999_999
    )

    def _boom(*args, **kwargs):
        raise AssertionError("EOD cache asla yeniden kontrol edilmemeli")

    monkeypatch.setattr(tbx, "_list_directory_files", _boom)

    d, path = tbx.ensure_daily_cache()
    assert d == trading_day
    assert path == cache_path


def test_ensure_daily_cache_intraday_not_rechecked_within_threshold(tmp_path, monkeypatch):
    """Eşik dolmadan (taze bir INTRADAY cache), ağa hiç sorulmadan doğrudan dönmeli."""
    trading_day = date(2026, 8, 26)
    monkeypatch.setattr(tbx, "find_latest_trading_day", lambda today=None: trading_day)
    _seed_distilled_cache(
        tmp_path, monkeypatch, trading_day, "TAKASINT_x-005.zip", False, age_seconds=60
    )

    def _boom(*args, **kwargs):
        raise AssertionError("eşik dolmadan tekrar ağa sorulmamalı")

    monkeypatch.setattr(tbx, "_list_directory_files", _boom)

    _, cache_path = tbx.ensure_daily_cache()
    cached = json.loads(cache_path.read_text())
    assert cached["source_file"] == "TAKASINT_x-005.zip"


def test_ensure_daily_cache_intraday_rechecks_and_keeps_same_file(tmp_path, monkeypatch):
    """Eşik dolmuş ama daha iyi bir dosya yoksa: XML tekrar indirilmemeli, sadece mtime tazelenmeli."""
    trading_day = date(2026, 8, 26)
    monkeypatch.setattr(tbx, "find_latest_trading_day", lambda today=None: trading_day)
    cache_path = _seed_distilled_cache(
        tmp_path,
        monkeypatch,
        trading_day,
        "TAKASINT_x-005.zip",
        False,
        age_seconds=tbx._RECHECK_AFTER_SECONDS + 10,
    )
    before_mtime = cache_path.stat().st_mtime

    monkeypatch.setattr(
        tbx, "_list_directory_files", lambda d: {"TAKASINT_x-005.zip": datetime(2026, 8, 26, 15, 10)}
    )

    def _boom(*args, **kwargs):
        raise AssertionError("aynı dosya varken XML tekrar indirilmemeli")

    monkeypatch.setattr(tbx, "_download_and_extract_xml", _boom)

    _, path = tbx.ensure_daily_cache()
    assert path.stat().st_mtime > before_mtime
    cached = json.loads(path.read_text())
    assert cached["source_file"] == "TAKASINT_x-005.zip"


def test_ensure_daily_cache_intraday_rebuilds_when_newer_file_appears(tmp_path, monkeypatch):
    """Eşik dolmuş VE daha güncel/nihai bir dosya çıkmışsa: cache o dosyadan yeniden kurulmalı."""
    trading_day = date(2026, 8, 26)
    monkeypatch.setattr(tbx, "find_latest_trading_day", lambda today=None: trading_day)
    _seed_distilled_cache(
        tmp_path,
        monkeypatch,
        trading_day,
        "TAKASINT_x-005.zip",
        False,
        age_seconds=tbx._RECHECK_AFTER_SECONDS + 10,
    )

    monkeypatch.setattr(
        tbx,
        "_list_directory_files",
        lambda d: {
            "TAKASINT_x-005.zip": datetime(2026, 8, 26, 15, 10),
            "TAKASEOD_x-001.zip": datetime(2026, 8, 26, 20, 35),
        },
    )
    fake_xml_path = tmp_path / "fake.xml"
    fake_xml_path.write_text("<spanFile/>")

    def _fake_download(d, filename, force=False):
        assert filename == "TAKASEOD_x-001.zip"
        assert force is True
        return fake_xml_path

    monkeypatch.setattr(tbx, "_download_and_extract_xml", _fake_download)
    monkeypatch.setattr(
        tbx,
        "build_distilled_cache",
        lambda xml_path: {
            "global": {
                "extreme_move_multiplier": 3.0,
                "extreme_move_covered_fraction": 0.32,
            },
            "products": {},
            "spot_prices": {"AKBNK": 74.3},
        },
    )

    _, path = tbx.ensure_daily_cache()
    cached = json.loads(path.read_text())
    assert cached["source_file"] == "TAKASEOD_x-001.zip"
    assert cached["is_final"] is True
    assert cached["spot_prices"]["AKBNK"] == pytest.approx(74.3)
    # Takasbank'ın KENDİ yayın zamanı (20:35) kaydedilmeli -- bizim
    # indirdiğimiz an DEĞİL (bkz. proje sohbet geçmişi).
    assert cached["published_at"] == "2026-08-26T20:35:00"


def test_last_update_info_reports_is_final(tmp_path, monkeypatch):
    trading_day = date(2026, 8, 26)
    _seed_distilled_cache(
        tmp_path, monkeypatch, trading_day, "TAKASINT_x-005.zip", False, age_seconds=5
    )
    info = tbx.last_update_info()
    assert info["source_date"] == trading_day
    assert info["is_final"] is False
    # published_at hiç yazılmamış (eski/basit cache) -- None'a düşmeli, patlamamalı.
    assert info["published_at"] is None


def test_last_update_info_reports_published_at_when_present(tmp_path, monkeypatch):
    """published_at, cached_at'ten (bizim fetch zamanımız) FARKLI ve doğru dönmeli."""
    trading_day = date(2026, 8, 26)
    _seed_distilled_cache(
        tmp_path,
        monkeypatch,
        trading_day,
        "TAKASINT_x-012.zip",
        False,
        age_seconds=5,
        published_at="2026-08-26T16:10:00",
    )
    info = tbx.last_update_info()
    assert info["published_at"] == datetime(2026, 8, 26, 16, 10, 0)
    assert info["source_file"] == "TAKASINT_x-012.zip"


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
