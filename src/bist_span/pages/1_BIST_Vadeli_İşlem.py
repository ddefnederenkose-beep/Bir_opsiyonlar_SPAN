"""BIST Vadeli İşlem — Minimum SPAN Teminatı (Streamlit sayfası).

BAĞIMSIZ KATMAN: Bu dosya, "BIST Opsiyonları — Minimum SPAN Teminatı"
sayfasının (BIST_Opsiyon.py) SPAN HESAP MANTIĞINA HİÇBİR ŞEKİLDE dokunmadan
yazılmıştır -- BIST_Opsiyon.py'den sadece PUBLIC, DEĞİŞTİRİLMEMİŞ fonksiyonları
(_streamlit_override_row, _top_nav) salt-okunur olarak import edip yeniden
kullanır; hesap mantığı tamamen futures_xml.py/futures_engine.py'den gelir
(onlar da BIST_Opsiyon.py'ye bağımlı değildir). BIST_Opsiyon.py'de TEK istisna: sayfanın en
üstünde görünen küçük, izole "üst navigasyon" bloğu (BIST_Opsiyon.py'deki
"BAŞLANGIÇ/SON" yorum satırlarıyla işaretli) -- bu, kullanıcıyla açıkça
konuşulup onaylanmış, SPAN hesabına dokunmayan, tek parça hâlinde geri
alınabilir bir eklemedir. Amaç: bu üç dosya (bu sayfa + futures_xml.py +
futures_engine.py) + BIST_Opsiyon.py'deki o tek izole blok istenirse opsiyon
özelliğinin hesap mantığını hiç etkilemeden silinebilsin.

NOT: BIST_Opsiyon.py'nin available_tickers()'ını (opsiyon risk parametre PDF'indeki
~29 hisse) hisse FİLTRESİ olarak KULLANMIYORUZ -- ilk sürümde öyle yapılmıştı
ve AEFES gibi (opsiyonu PDF'te olmayan ama gerçek vadeli işlemi Takasbank
XML'inde bulunan) hisseleri yanlışlıkla eliyordu. Bunun yerine, hisse OLMAYAN
(döviz/değerli maden/elektrik/endeks/faiz) ürünleri elle tespit edilmiş bir
listeyle (bkz. _NON_EQUITY_FUTURES_*) eleyip geri kalan HER ŞEYİ hisse
vadelisi sayıyoruz.

Streamlit'in "pages/" klasör kuralı gereği bu dosya, ana script (BIST_Opsiyon.py)
çalıştırıldığında kenar çubuğunda otomatik ikinci bir sayfa olarak belirir
-- BIST_Opsiyon.py'ye "başka bir sayfa var" diye tek satır bile eklemeye gerek yok.

Vadeli işlem, opsiyonlardan yapısal olarak BASİTTİR: delta her zaman 1'dir
(doğrusal enstrüman), Black-Scholes/volatilite/strike YOKTUR, ve Madde
33-38'in "Kısa OPSİYON Pozisyonu" ifadesi gereği Short Option Minimum/Net
Opsiyon Değeri/Opsiyon Prim Değeri bu sayfada UYGULANMAZ (bkz.
futures_engine.py docstring'i). Bu yüzden Takasbank'ın günlük PC-SPAN
XML'i TEK BAŞINA yeterli -- opsiyon sayfasındaki gibi ayrı bir risk
parametre PDF'i ya da yfinance'ten historical volatility çekmeye hiç
gerek yok.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

# `streamlit run src/bist_span/BIST_Opsiyon.py` altındaki pages/ klasöründe
# çalışırken bu dosya bağımsız bir script olarak yürütülür -- BIST_Opsiyon.py'deki
# AYNI sebeple (bkz. BIST_Opsiyon.py'nin başındaki yorum) src/ dizinini sys.path'e
# ekleyip mutlak import kullanıyoruz. Tek fark: bu dosya BIST_Opsiyon.py'den bir
# kat daha derinde (pages/ altında) olduğu için bir .parent daha var.
_SRC_DIR = str(Path(__file__).resolve().parent.parent.parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from bist_span import futures_engine as fe
from bist_span import futures_xml as fx
from bist_span import i18n
from bist_span import takasbank_xml as tbx
from bist_span.BIST_Opsiyon import _streamlit_override_row, _top_nav
from bist_span.span_engine import apply_price_shock, generate_risk_scenarios

_FRACTION_LABELS = ((0.0, "sabit"), (1 / 3, "1/3 PSR"), (2 / 3, "2/3 PSR"), (1.0, "tam PSR"))

# futures_xml.list_futures_tickers() ham listesi, hisse senedi vadelilerinin
# yanında döviz/değerli maden/elektrik/endeks/faiz gibi egzotik futPf
# ürünlerini de içerir (bkz. futures_xml.py docstring'i -- o modül BİLİNÇLİ
# olarak filtrelemeden döner; "hisse evreniyle kesiştir" filtresi burada,
# sayfa seviyesinde yapılmalı). Gerçek Takasbank XML'i (28.08.2026) elle
# taranarak tespit edilen, hisse OLMAYAN 38 ürün:
_NON_EQUITY_FUTURES_PREFIXES = ("ELCBAS",)  # elektrik baz yük vadelileri (ELCBAS01, ELCBASQ1, ELCBASY, ...)
_NON_EQUITY_FUTURES_SUFFIXES = ("_N",)  # değerli maden mini vadelileri (AGVMS_N, AUVMS_N, PDVMS_N, PTVMS_N)
_NON_EQUITY_FUTURES_EXACT = {
    # döviz paritesi vadelileri
    "CNHTRY", "EURTRY", "EURUSD", "GBPUSD", "RUBTRY", "USDTRY", "USDTRYP",
    # değerli maden/emtia vadelileri
    "XAGUSD", "XAUTRY", "XAUUSD", "XCUUSD", "XPDUSD", "XPTUSD",
    # endeks vadelileri (XU030D: BIST30, X10XBD: BIST Bankacılık 10,
    # XLBNKD: BIST Bankacılık, XSD25D: BIST Sürdürülebilirlik 25)
    "X10XBD", "XLBNKD", "XSD25D", "XU030D",
    # faiz/tahvil/diğer türü vadeliler
    "TLREF1M", "TRALT", "TRMET", "TRT131130T14", "SASX10",
}


def _is_stock_futures_ticker(ticker: str) -> bool:
    """Hisse senedi DIŞINDAKİ vadeli işlem ürünlerini eler -- bu sayfanın
    ilk sürüm kapsamı SADECE hisse senedi vadelileridir (bkz. modül
    docstring'i ve futures_xml.py docstring'i)."""
    if ticker in _NON_EQUITY_FUTURES_EXACT:
        return False
    if ticker.startswith(_NON_EQUITY_FUTURES_PREFIXES):
        return False
    if ticker.endswith(_NON_EQUITY_FUTURES_SUFFIXES):
        return False
    return True


def _futures_scenario_description(
    price_multiplier: float, is_extreme: bool, emm: float, emcf: float
) -> str:
    """BIST_Opsiyon._scenario_description'ın vadeli-işlem-özel hali.

    BIST_Opsiyon.py'deki sürüm her senaryoya bir "Vol yukarı/aşağı" etiketi de
    ekler -- opsiyonlarda anlamlı (volatilite gerçekten fiyatı etkiler),
    ama vadeli işlemde volatilite riski hiç YOK (VSR her zaman 0, bkz.
    futures_engine.py docstring'i) -- o etiketi burada kullanmak yanıltıcı
    olurdu ("Vol aşağı" gibi anlamsız bir metin), bu yüzden ayrı, daha
    sade bir sürüm yazıldı.
    """
    if is_extreme:
        direction_word = "yukarı" if price_multiplier > 0 else "aşağı"
        emm_str = f"{emm:g}"
        emcf_str = f"{emcf * 100:g}"
        return f"Aşırı hareket {direction_word} ({emm_str}×PSR, %{emcf_str})"

    for fraction, label in _FRACTION_LABELS:
        if math.isclose(abs(price_multiplier), fraction, abs_tol=1e-9):
            if fraction == 0.0:
                return "Fiyat sabit"
            sign = "+" if price_multiplier > 0 else "-"
            return f"Fiyat {sign}{label}"
    return f"Fiyat {price_multiplier:+.4f}×PSR"


def _futures_scenario_table(
    position: fe.FuturesPosition,
    price: float,
    price_scan_range: float,
    extreme_move_multiplier: float,
    extreme_move_covered_fraction: float,
) -> pd.DataFrame:
    """Vadeli işlemin 16 SPAN senaryosunu bir tabloya döker.

    BIST_Opsiyon._build_scenario_table'ın vadeli-işlem-özel hali: Black-Scholes/IV/
    strike sütunları YOK (bkz. modül docstring'i) -- sadece şoklu fiyat ve
    doğrusal P&L. VSR her zaman 0 verildiği için (generate_risk_scenarios,
    futures_engine.calculate_futures_margin'in yaptığı gibi) 16 senaryo
    fiili olarak 8 farklı fiyat seviyesini İKİŞER KEZ üretir -- bu,
    Takasbank'ın kendi <fut><ra> risk dizisinde de gözlenen (ve PC-SPAN'ın
    Risk Array ekranında kullanıcı tarafından doğrulanmış) davranıştır,
    hata değildir.
    """
    scenarios = generate_risk_scenarios(
        spot=price,
        volatility=0.0,
        price_scan_range=price_scan_range,
        volatility_scan_range=0.0,
        extreme_move_multiplier=extreme_move_multiplier,
        extreme_move_covered_fraction=extreme_move_covered_fraction,
    )
    rows = []
    for i, scenario in enumerate(scenarios, start=1):
        pnl = fe.calculate_futures_scenario_pnl(position, price, scenario)
        shocked_price = apply_price_shock(price, scenario["price_shock"])
        price_multiplier = scenario["price_shock"] / price_scan_range if price_scan_range else 0.0
        rows.append(
            {
                "Sen.": i,
                "Açıklama": _futures_scenario_description(
                    price_multiplier,
                    scenario["is_extreme"],
                    extreme_move_multiplier,
                    extreme_move_covered_fraction,
                ),
                "Fiyat Çarpanı": round(price_multiplier, 6),
                "Şoklu Fiyat": round(shocked_price, 4),
                "Fark": round(shocked_price - price, 4),
                "Kısa K/Z (TL)": round(pnl, 4),
            }
        )
    df = pd.DataFrame(rows)
    worst_idx = df["Kısa K/Z (TL)"].idxmin()
    df.attrs["worst_scenario_no"] = int(df.loc[worst_idx, "Sen."])
    return df


def _display_table(df: pd.DataFrame) -> pd.DataFrame:
    """16-senaryo tablosunu st.table ile göstermeye hazır sabit-ondalıklı
    string sütunlara çevirir (bkz. BIST_Opsiyon._scenario_display_table -- aynı
    render nedeniyle, burada da düz HTML tablo tercih edildi)."""
    display = df.copy()
    display["Sen."] = display["Sen."].map(lambda v: f"{int(v)}")
    display["Fiyat Çarpanı"] = display["Fiyat Çarpanı"].map(lambda v: f"{v:.4f}")
    display["Şoklu Fiyat"] = display["Şoklu Fiyat"].map(lambda v: f"{v:.4f}")
    display["Fark"] = display["Fark"].map(lambda v: f"{v:+.4f}")
    display["Kısa K/Z (TL)"] = display["Kısa K/Z (TL)"].map(lambda v: f"{v:+.2f}")
    display.index = [""] * len(display)
    return display


def run_futures_page() -> None:
    import streamlit as st

    lang = i18n.get_lang(st)

    st.set_page_config(
        page_title=i18n.t("page_title_futures", lang),
        layout="wide",
    )

    lang = _top_nav()  # BIST_Opsiyon.py'deki izole blok -- opsiyon/vadeli işlem üst seçici + dil

    st.title(i18n.t("title_futures", lang))
    st.markdown(i18n.t("intro_futures", lang))
    st.caption(i18n.t("caption_intro_futures", lang))
    with st.expander(i18n.t("advanced_settings", lang)):
        contracts = st.number_input(
            i18n.t("contracts_label", lang), value=-1, step=1, key="fut_contracts"
        )

    try:
        with st.spinner(i18n.t("fetch_futures_spinner", lang)):
            fx.ensure_futures_daily_cache()
            all_futures_tickers = fx.list_futures_tickers()
    except Exception as exc:
        st.error(i18n.t("fetch_futures_error", lang, error=exc))
        return

    # Kapsam (bilinçli, ilk sürüm kararı -- bkz. futures_xml.py docstring'i):
    # şimdilik SADECE hisse senedi vadelileri listeleniyor -- elektrik/döviz/
    # değerli maden/endeks/faiz gibi egzotik futPf ürünleri (bkz.
    # _is_stock_futures_ticker) kapsam dışı.
    tickers = sorted(t for t in all_futures_tickers if _is_stock_futures_ticker(t))
    if not tickers:
        st.warning(i18n.t("no_stock_futures_warning", lang))
        return

    ticker = st.selectbox(
        i18n.t("ticker_label", lang),
        options=tickers,
        index=tickers.index("AEFES") if "AEFES" in tickers else 0,
        help=i18n.t("ticker_help_futures", lang),
    )

    expiries = fx.list_futures_expiries(ticker)
    if not expiries:
        st.warning(i18n.t("no_expiry_warning_futures", lang, ticker=ticker))
        return
    expiry = st.selectbox(
        i18n.t("expiry_label", lang),
        options=expiries,
        format_func=lambda d: d.strftime("%d.%m.%Y"),
        help=i18n.t("expiry_help", lang),
    )

    try:
        params = fx.get_futures_params(ticker, expiry)
    except KeyError as exc:
        st.error(str(exc))
        return

    source_link = tbx.folder_url(params.source_date)
    # last_update_info() aynı ham Takasbank XML'ini okur (futures_xml.py'nin
    # kendi cache'i de o dosyadan türetiliyor -- bkz. ensure_futures_daily_cache),
    # bu yüzden opsiyon sayfasındaki "en güncel dosya" garantisi (gün içi
    # INT dosyası çıktıkça otomatik yenilenme) burada da AYNEN geçerlidir --
    # bkz. BIST_Opsiyon.py'deki denk kullanım.
    takasbank_info = tbx.last_update_info()
    xml_source = i18n.t("source_takasbank_xml", lang)
    if takasbank_info:
        durum = i18n.t(
            "status_eod" if takasbank_info["is_final"] else "status_intraday", lang
        )
        published_at = takasbank_info.get("published_at")
        if published_at:
            update_line = i18n.t(
                "published_line", lang, timestamp=published_at.strftime("%d.%m.%Y %H:%M")
            )
        else:
            update_line = i18n.t(
                "cached_line",
                lang,
                timestamp=takasbank_info["cached_at"].strftime("%d.%m.%Y %H:%M"),
            )
        st.caption(
            i18n.t(
                "data_banner_futures",
                lang,
                link=source_link,
                date=params.source_date.strftime("%d.%m.%Y"),
                update_line=update_line,
                status=durum,
            )
        )
    else:
        st.caption(
            i18n.t(
                "data_banner_futures_no_info",
                lang,
                link=source_link,
                date=params.source_date.strftime("%d.%m.%Y"),
            )
        )

    with st.expander(i18n.t("auto_fetched_expander", lang)):
        price = _streamlit_override_row(
            st, i18n.t("field_current_price_futures", lang), params.price, "fut_price", source=xml_source, live=True
        )
        contract_size = _streamlit_override_row(
            st,
            i18n.t("field_contract_multiplier", lang),
            params.contract_size,
            "fut_cvf",
            source=xml_source,
            decimals=2,
        )
        _streamlit_override_row(
            st,
            i18n.t("tte_field_label", lang),
            params.time_to_expiry,
            "fut_tte",
            source=xml_source,
            decimals=6,
        )
        st.caption(i18n.t("tte_info_futures", lang))
        psr = _streamlit_override_row(
            st, i18n.t("field_psr", lang), params.price_scan_range, "fut_psr", source=xml_source, decimals=4
        )
        emm = _streamlit_override_row(
            st, i18n.t("field_emm", lang), params.extreme_move_multiplier, "fut_emm", source=xml_source
        )
        emcf = _streamlit_override_row(
            st,
            i18n.t("field_emcf", lang),
            params.extreme_move_covered_fraction,
            "fut_emcf",
            source=xml_source,
        )

        st.divider()
        icsc = st.number_input(
            i18n.t("icsc_label_futures", lang),
            value=0.0,
            min_value=0.0,
            step=0.0001,
            format="%.4f",
            key="fut_icsc",
            help=i18n.t("icsc_help_futures_leg", lang),
        )
        icc = st.number_input(
            i18n.t("field_icc", lang),
            value=0.0,
            min_value=0.0,
            step=0.0001,
            format="%.4f",
            key="fut_icc",
            help=i18n.t("icc_help", lang),
        )

    if st.button(i18n.t("calculate_button", lang), type="primary"):
        position = fe.FuturesPosition(
            ticker=ticker, contracts=int(contracts), contract_size=contract_size
        )
        span_result = fe.calculate_futures_margin(
            position=position,
            price=price,
            price_scan_range=psr,
            extreme_move_multiplier=emm,
            extreme_move_covered_fraction=emcf,
            intra_commodity_spread_charge=icsc,
            inter_commodity_spread_credit=icc,
        )
        scenario_table = _futures_scenario_table(position, price, psr, emm, emcf)
        st.session_state["futures_results"] = {
            "span": span_result,
            "scenarios": scenario_table,
            "ticker": ticker,
            "expiry": expiry,
            "contracts": int(contracts),
        }

    results = st.session_state.get("futures_results")
    if not results or results["ticker"] != ticker or results["expiry"] != expiry:
        return

    span = results["span"]
    currency = "TL" if lang == "tr" else "TRY"
    st.divider()
    st.metric(
        i18n.t(
            "min_margin_metric_futures",
            lang,
            ticker=ticker,
            expiry=expiry.strftime("%d.%m.%Y"),
        ),
        f"{span['total_initial_margin']:,.2f} {currency}",
    )
    st.caption(i18n.t("single_position_note", lang))
    st.caption(
        i18n.t("scanning_risk_caption", lang, value=f"{span['scan_risk']:,.2f}"),
        help=i18n.t("scanning_risk_help", lang),
    )
    if span["intra_commodity_spread_charge"]:
        st.caption(
            i18n.t(
                "icsc_contribution_caption",
                lang,
                value=f"{span['intra_commodity_spread_charge']:,.2f}",
            )
        )
    if span["inter_commodity_spread_credit"]:
        st.caption(
            i18n.t(
                "icc_contribution_caption",
                lang,
                value=f"{span['inter_commodity_spread_credit']:,.2f}",
            )
        )

    st.subheader(i18n.t("scenario_table_subheader", lang))
    st.markdown(i18n.t("worst_scenario_line", lang, n=results["scenarios"].attrs["worst_scenario_no"]))
    # _display_table ÖNCE (değişmedi, hep Türkçe sütun adlarıyla çalışır) --
    # sonra SADECE ekrana basılacak metin sonucu çevriliyor (bkz.
    # i18n.localize_scenario_table, BIST_Opsiyon.py'deki denk kullanım).
    display = _display_table(results["scenarios"])
    st.table(i18n.localize_scenario_table(display, lang))


# Streamlit'in "pages/" çalıştırıcısı, seçili sayfa script'ini __name__="__main__"
# olarak çalıştırır (BIST_Opsiyon.py'nin kendi __main__ korumasıyla AYNI davranış,
# bkz. BIST_Opsiyon.py'nin sonu) -- bu da bu dosya yanlışlıkla plain bir modül olarak
# import edilirse (ör. bir test) Streamlit UI kodunun tetiklenmemesini sağlar.
if __name__ == "__main__":
    run_futures_page()
