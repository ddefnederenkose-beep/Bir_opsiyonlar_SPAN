"""Sistemli backtest: bizim SPAN motorumuzu, Takasbank'ın XML'e KENDİ
gömdüğü 16-senaryo risk dizisiyle (<ra>) karşılaştırır.

YÖNTEM (bkz. proje sohbet geçmişi -- futures_engine.py'de AEFES için
zaten kuruşuna kadar doğrulanmış aynı teknik, burada YÜZLERCE pozisyona
genişletiliyor): Takasbank'ın günlük PC-SPAN XML'i, her opsiyon/vadeli
işlem sözleşmesi için KENDİ önceden hesapladığı 16 senaryo P&L'ini
<ra><a>...</a>x16</ra> olarak gömer (kısa 1 kontrat, kontrat büyüklüğüyle
zaten ölçeklenmiş). Bu script:
  1) elimizdeki HER cache'lenmiş ham XML günü için,
  2) o gündeki HER hisse opsiyonu/vadeli işlemi için,
  3) bizim motorumuzun (span_engine/futures_engine, HİÇ DEĞİŞTİRİLMEDEN)
     hesapladığı Scanning Risk'i, Takasbank'ın <ra>'sından AYNI
     span_engine.scanning_risk() fonksiyonuyla türetilen değerle
     karşılaştırır (16 P&L de scenario-scenario birebir karşılaştırılır,
     bkz. compare() içindeki max_scenario_diff).

BAĞIMSIZ, SALT-OKUNUR SCRIPT: hiçbir mevcut modülü DEĞİŞTİRMEZ --
takasbank_xml.py/futures_xml.py/span_engine.py/futures_engine.py/
BIST_Opsiyon.py'nin PUBLIC (ya da bu projede zaten yerleşik "iç kullanım
için paylaşılan" kabul edilen) fonksiyonlarını salt-okunur kullanır.
<ra> dizisini ayrıştıran kod (hiçbir mevcut modülde yok, futures_xml.py
kendi docstring'inde "ileride çapraz doğrulama için değerli" diye
bıraktığı tam da bu) SADECE burada, yeni ve izole olarak yazıldı.

Kullanım:
    python scripts/backtest_span.py
    python scripts/backtest_span.py --out cache/backtest_results.csv
    python scripts/backtest_span.py --limit-files 2   # hızlı deneme

Not: cache/takasbank/raw/*.xml dosyaları (~65-75MB/gün) bu repoda
git'e girmez (.gitignore) -- script SADECE lokalde elinde ne varsa
kullanır, ağa hiç gitmez.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from statistics import mean, median
from xml.etree import ElementTree as ET

_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from bist_span import futures_engine as fe
from bist_span import futures_xml as fx
from bist_span import takasbank_xml as tbx
from bist_span.BIST_Opsiyon import DEFAULT_RISK_PARAMS_FILE, available_tickers
from bist_span.span_engine import (
    OptionPosition,
    calculate_scenario_pnl,
    generate_risk_scenarios,
    scanning_risk,
)

RAW_DIR = tbx.RAW_DIR

# futures sayfasındaki (pages/1_📈_BIST_Vadeli_İşlem.py) hisse-olmayan
# ürün filtresiyle AYNI liste -- o dosyadan programatik import fragile
# olacağı için (Streamlit "pages/" dosyaları normal paket üyesi değil)
# burada kasıtlı olarak KÜÇÜK bir kopyası tutuluyor (bkz. o dosyanın
# docstring'i -- gerçek Takasbank XML'i taranarak elle tespit edilmişti).
_NON_EQUITY_FUTURES_PREFIXES = ("ELCBAS",)
_NON_EQUITY_FUTURES_SUFFIXES = ("_N",)
_NON_EQUITY_FUTURES_EXACT = {
    "CNHTRY", "EURTRY", "EURUSD", "GBPUSD", "RUBTRY", "USDTRY", "USDTRYP",
    "XAGUSD", "XAUTRY", "XAUUSD", "XCUUSD", "XPDUSD", "XPTUSD",
    "X10XBD", "XLBNKD", "XSD25D", "XU030D",
    "TLREF1M", "TRALT", "TRMET", "TRT131130T14", "SASX10",
}


def _is_stock_futures_ticker(ticker: str) -> bool:
    if ticker in _NON_EQUITY_FUTURES_EXACT:
        return False
    if ticker.startswith(_NON_EQUITY_FUTURES_PREFIXES):
        return False
    if ticker.endswith(_NON_EQUITY_FUTURES_SUFFIXES):
        return False
    return True


def _extract_option_ra(xml_path: Path) -> dict[tuple[str, str, float, str], list[float]]:
    """{(pfcode, expiry_str, strike, 'C'|'P'): [16 float]} -- <opt><ra> olan HER opsiyon."""
    result: dict[tuple[str, str, float, str], list[float]] = {}
    context = ET.iterparse(str(xml_path), events=("end",))
    for _event, elem in context:
        if elem.tag != "oopPf":
            continue
        try:
            pfcode = elem.findtext("pfCode")
            if not pfcode:
                continue
            for series in elem.findall("series"):
                expiry_str = series.findtext("pe")
                if not expiry_str:
                    continue
                for opt in series.findall("opt"):
                    ra_elem = opt.find("ra")
                    if ra_elem is None:
                        continue
                    k_val = opt.findtext("k")
                    o_val = opt.findtext("o")
                    if not k_val or not o_val:
                        continue
                    a_values = [float(a.text) for a in ra_elem.findall("a")]
                    if len(a_values) != 16:
                        continue
                    result[(pfcode, expiry_str, round(float(k_val), 6), o_val)] = a_values
        finally:
            elem.clear()
    return result


def _extract_futures_ra(xml_path: Path) -> dict[tuple[str, str], list[float]]:
    """{(pfcode, expiry_str): [16 float]} -- <fut><ra> olan HER vadeli işlem."""
    result: dict[tuple[str, str], list[float]] = {}
    context = ET.iterparse(str(xml_path), events=("end",))
    for _event, elem in context:
        if elem.tag != "futPf":
            continue
        try:
            if elem.findtext("valueMeth") != "FUT":
                continue
            pfcode = elem.findtext("pfCode")
            if not pfcode:
                continue
            for fut in elem.findall("fut"):
                ra_elem = fut.find("ra")
                if ra_elem is None:
                    continue
                expiry_str = fut.findtext("pe")
                if not expiry_str:
                    continue
                a_values = [float(a.text) for a in ra_elem.findall("a")]
                if len(a_values) != 16:
                    continue
                result[(pfcode, expiry_str)] = a_values
        finally:
            elem.clear()
    return result


def _compare(our_pnls: list[float], ra: list[float]) -> tuple[float, float, float]:
    """(bizim_scan_risk, takasbank_scan_risk, en_büyük_senaryo_farkı) döner."""
    our_scan = scanning_risk(our_pnls)
    tb_scan = scanning_risk(ra)
    max_scenario_diff = max(abs(a - b) for a, b in zip(our_pnls, ra))
    return our_scan, tb_scan, max_scenario_diff


def backtest_options_for_day(
    xml_path: Path, trading_day: str, equity_universe: set[str]
) -> list[dict]:
    distilled = tbx.build_distilled_cache(xml_path)
    ra_map = _extract_option_ra(xml_path)
    emm, ecf = tbx._parse_global_extreme_move(xml_path)
    spot_prices = distilled["spot_prices"]

    rows = []
    for pfcode, by_expiry in distilled["products"].items():
        if pfcode not in equity_universe:
            continue
        spot = spot_prices.get(pfcode)
        if not spot or spot <= 0:
            continue
        for expiry_str, series_data in by_expiry.items():
            t = series_data.get("t")
            rate = series_data.get("intrRate")
            psr = series_data.get("psr")
            vsr = series_data.get("vsr")
            if not t or t <= 0 or psr is None:
                continue
            for opt in series_data.get("options", []):
                key = (pfcode, expiry_str, round(opt["k"], 6), opt["o"])
                ra = ra_map.get(key)
                if not ra:
                    continue
                if opt.get("v", 0) <= 0 or opt.get("p", 0) <= 0:
                    continue
                option_type = "call" if opt["o"] == "C" else "put"
                position = OptionPosition(
                    ticker=pfcode,
                    strike=opt["k"],
                    option_type=option_type,
                    contracts=-1,
                    time_to_expiry=t,
                    risk_free_rate=rate,
                    contract_size=int(opt.get("cvf", 100.0)),
                )
                scenarios = generate_risk_scenarios(
                    spot=spot,
                    volatility=opt["v"],
                    price_scan_range=psr,
                    volatility_scan_range=vsr,
                    extreme_move_multiplier=emm,
                    extreme_move_covered_fraction=ecf,
                )
                our_pnls = [
                    calculate_scenario_pnl(position, spot, opt["v"], s, base_price=opt["p"])
                    for s in scenarios
                ]
                our_scan, tb_scan, max_scen_diff = _compare(our_pnls, ra)
                rows.append(
                    {
                        "date": trading_day,
                        "product": "option",
                        "ticker": pfcode,
                        "expiry": expiry_str,
                        "strike": opt["k"],
                        "type": option_type,
                        "our_scan_risk": round(our_scan, 4),
                        "takasbank_scan_risk": round(tb_scan, 4),
                        "abs_diff": round(abs(our_scan - tb_scan), 4),
                        "max_scenario_diff": round(max_scen_diff, 4),
                    }
                )
    return rows


def backtest_futures_for_day(
    xml_path: Path, trading_day: str, equity_universe_hint: set[str]
) -> list[dict]:
    distilled = fx.build_futures_distilled_cache(xml_path)
    ra_map = _extract_futures_ra(xml_path)
    r_groups = distilled["extreme_move_by_group"]

    rows = []
    for pfcode, by_expiry in distilled["products"].items():
        if not _is_stock_futures_ticker(pfcode):
            continue
        for expiry_str, fut_data in by_expiry.items():
            ra = ra_map.get((pfcode, expiry_str))
            if not ra:
                continue
            price = fut_data["p"]
            psr = fut_data["psr"]
            extreme = r_groups.get(fut_data["r"], {"emm": 3.0, "ecf": 0.32})
            position = fe.FuturesPosition(
                ticker=pfcode, contracts=-1, contract_size=fut_data["cvf"]
            )
            scenarios = generate_risk_scenarios(
                spot=price,
                volatility=0.0,
                price_scan_range=psr,
                volatility_scan_range=0.0,
                extreme_move_multiplier=extreme["emm"],
                extreme_move_covered_fraction=extreme["ecf"],
            )
            our_pnls = [fe.calculate_futures_scenario_pnl(position, price, s) for s in scenarios]
            our_scan, tb_scan, max_scen_diff = _compare(our_pnls, ra)
            rows.append(
                {
                    "date": trading_day,
                    "product": "future",
                    "ticker": pfcode,
                    "expiry": expiry_str,
                    "strike": "",
                    "type": "",
                    "our_scan_risk": round(our_scan, 4),
                    "takasbank_scan_risk": round(tb_scan, 4),
                    "abs_diff": round(abs(our_scan - tb_scan), 4),
                    "max_scenario_diff": round(max_scen_diff, 4),
                }
            )
    return rows


def summarize(rows: list[dict]) -> None:
    if not rows:
        print("Hiç karşılaştırma satırı üretilmedi.")
        return

    n = len(rows)
    diffs = [r["abs_diff"] for r in rows]
    scen_diffs = [r["max_scenario_diff"] for r in rows]
    exact = sum(1 for d in diffs if d <= 0.01)
    within_1tl = sum(1 for d in diffs if d <= 1.0)
    within_1pct = sum(
        1
        for r in rows
        if r["takasbank_scan_risk"] > 0
        and r["abs_diff"] / r["takasbank_scan_risk"] <= 0.01
    )

    print(f"\n=== BACKTEST ÖZETİ ({n} pozisyon, {len({r['date'] for r in rows})} gün) ===")
    print(f"  Scanning Risk ortalama mutlak fark : {mean(diffs):.4f} TL")
    print(f"  Scanning Risk medyan mutlak fark    : {median(diffs):.4f} TL")
    print(f"  Scanning Risk maks. mutlak fark     : {max(diffs):.4f} TL")
    print(f"  Tam eşleşme (<=0.01 TL)             : {exact}/{n}  (%{100*exact/n:.2f})")
    print(f"  1 TL içinde                         : {within_1tl}/{n}  (%{100*within_1tl/n:.2f})")
    print(f"  %1 bağıl hata içinde                : {within_1pct}/{n}  (%{100*within_1pct/n:.2f})")
    print(f"  Senaryo-bazlı maks. fark (16'nın en kötüsü): {max(scen_diffs):.4f} TL")

    by_product: dict[str, list[dict]] = {}
    for r in rows:
        by_product.setdefault(r["product"], []).append(r)
    for product, prows in by_product.items():
        pdiffs = [r["abs_diff"] for r in prows]
        pexact = sum(1 for d in pdiffs if d <= 0.01)
        print(
            f"  -- {product}: {len(prows)} pozisyon, "
            f"tam eşleşme %{100*pexact/len(prows):.2f}, "
            f"ortalama fark {mean(pdiffs):.4f} TL"
        )

    worst = sorted(rows, key=lambda r: -r["abs_diff"])[:15]
    print("\n=== EN BÜYÜK 15 SAPMA (manuel incelemeye değer) ===")
    print(
        f"{'date':>8} {'product':>7} {'ticker':>8} {'expiry':>9} {'strike':>8} "
        f"{'type':>5} {'bizim':>12} {'takasbank':>12} {'fark':>10}"
    )
    for r in worst:
        print(
            f"{r['date']:>8} {r['product']:>7} {r['ticker']:>8} {r['expiry']:>9} "
            f"{str(r['strike']):>8} {r['type']:>5} {r['our_scan_risk']:>12,.2f} "
            f"{r['takasbank_scan_risk']:>12,.2f} {r['abs_diff']:>10,.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=Path("cache/backtest_results.csv"),
        help="Sonuçların yazılacağı CSV yolu (varsayılan: cache/backtest_results.csv)",
    )
    parser.add_argument(
        "--limit-files", type=int, default=None, help="Sadece ilk N XML dosyasını işle (hızlı deneme için)"
    )
    args = parser.parse_args()

    xml_files = sorted(RAW_DIR.glob("*.xml"))
    if args.limit_files:
        xml_files = xml_files[: args.limit_files]
    if not xml_files:
        print(f"'{RAW_DIR}' altında hiç ham XML bulunamadı -- önce siteyi kullanıp cache oluştur.")
        return

    equity_universe = set(available_tickers(DEFAULT_RISK_PARAMS_FILE))
    print(f"Bilinen hisse evreni: {len(equity_universe)} ticker")
    print(f"İşlenecek gün sayısı: {len(xml_files)} -> {[f.stem for f in xml_files]}")

    all_rows: list[dict] = []
    for xml_path in xml_files:
        trading_day = xml_path.stem
        print(f"\n--- {trading_day} işleniyor ({xml_path.stat().st_size / 1e6:.1f} MB) ---")
        try:
            option_rows = backtest_options_for_day(xml_path, trading_day, equity_universe)
            print(f"  opsiyon: {len(option_rows)} karşılaştırma")
        except Exception as exc:
            print(f"  opsiyon HATASI: {exc}")
            option_rows = []
        try:
            futures_rows = backtest_futures_for_day(xml_path, trading_day, equity_universe)
            print(f"  vadeli işlem: {len(futures_rows)} karşılaştırma")
        except Exception as exc:
            print(f"  vadeli işlem HATASI: {exc}")
            futures_rows = []
        all_rows.extend(option_rows)
        all_rows.extend(futures_rows)

    summarize(all_rows)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        if all_rows:
            writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            writer.writeheader()
            writer.writerows(all_rows)
    print(f"\nDetaylı sonuçlar yazıldı: {args.out}")


if __name__ == "__main__":
    main()
