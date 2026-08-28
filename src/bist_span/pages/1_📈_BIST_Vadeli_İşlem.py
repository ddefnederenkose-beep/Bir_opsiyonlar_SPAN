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

    st.set_page_config(
        page_title="BIST Vadeli İşlem SPAN Teminat Hesaplama",
        page_icon="📈",
        layout="wide",
    )

    _top_nav()  # BIST_Opsiyon.py'deki izole blok -- opsiyon/vadeli işlem üst seçici

    st.title("BIST Vadeli İşlem — Minimum SPAN Teminatı")
    st.markdown(
        "Bir vadeli işlem sözleşmesinde (uzun ya da kısa fark etmeksizin) Takasbank'ın "
        "senden isteyeceği minimum başlangıç teminatını SPAN metodolojisiyle hesaplar.  \n"
        "Future'da risk simetriktir — hem alıcı hem satıcı taraf, piyasa aleyhe hareket "
        "ettiğinde sınırsız kayıp riski taşır, bu yüzden ikisi de aynı şekilde "
        "teminatlandırılır."
    )
    st.caption(
        "Hisse ve vade seç, 'Hesapla'ya bas. Güncel fiyat ve Takasbank risk parametreleri "
        "(PSR, Extreme Move) otomatik çekilir; istersen her bileşeni aşağıda tek tek "
        "değiştirebilirsin."
    )
    with st.expander("Gelişmiş ayarlar"):
        contracts = st.number_input(
            "Kontrat Sayısı (kısa pozisyon için negatif)", value=-1, step=1, key="fut_contracts"
        )

    try:
        with st.spinner("Takasbank vadeli işlem verisi çekiliyor..."):
            fx.ensure_futures_daily_cache()
            all_futures_tickers = fx.list_futures_tickers()
    except Exception as exc:
        st.error(f"Vadeli işlem verisi çekilemedi: {exc}")
        return

    # Kapsam (bilinçli, ilk sürüm kararı -- bkz. futures_xml.py docstring'i):
    # şimdilik SADECE hisse senedi vadelileri listeleniyor -- elektrik/döviz/
    # değerli maden/endeks/faiz gibi egzotik futPf ürünleri (bkz.
    # _is_stock_futures_ticker) kapsam dışı.
    tickers = sorted(t for t in all_futures_tickers if _is_stock_futures_ticker(t))
    if not tickers:
        st.warning(
            "Şu an Takasbank verisinde hisse senedi vadeli işlem sözleşmesi bulunamadı."
        )
        return

    ticker = st.selectbox(
        "Hisse",
        options=tickers,
        index=tickers.index("AEFES") if "AEFES" in tickers else 0,
        help=(
            "Takasbank'ın güncel PC-SPAN dosyasında gerçek (sanal marjin serisi "
            "olmayan) bir vadeli işlem sözleşmesi bulunan hisse senedi semboller."
        ),
    )

    expiries = fx.list_futures_expiries(ticker)
    if not expiries:
        st.warning(f"{ticker} için Takasbank verisinde vadeli işlem sözleşmesi bulunamadı.")
        return
    expiry = st.selectbox(
        "Vade Tarihi",
        options=expiries,
        format_func=lambda d: d.strftime("%d.%m.%Y"),
        help="Takasbank'ın güncel dosyasında bu hisse için gerçekten mevcut olan vadeler.",
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
    if takasbank_info:
        durum = (
            "gün sonu (EOD, o günün nihai verisi)"
            if takasbank_info["is_final"]
            else "gün içi ara güncelleme — daha yeni bir dosya çıktıkça otomatik yenilenir"
        )
        published_at = takasbank_info.get("published_at")
        if published_at:
            update_line = f"Takasbank'ın yayınladığı belge: {published_at.strftime('%d.%m.%Y %H:%M')}"
        else:
            update_line = f"son güncelleme (bizim çekişimiz): {takasbank_info['cached_at'].strftime('%d.%m.%Y %H:%M')}"
        st.caption(
            f":green[●] Fiyat, T, PSR ve Extreme Move [Takasbank'ın günlük PC-SPAN "
            f"dosyasından]({source_link}) otomatik çekiliyor · veri tarihi: "
            f"{params.source_date.strftime('%d.%m.%Y')} · {update_line} ({durum}). "
            "Aşağıda 'Değiştir' ile her alanı elle üzerine yazabilirsin."
        )
    else:
        st.caption(
            f"Fiyat, T, PSR ve Extreme Move [Takasbank'ın günlük PC-SPAN "
            f"dosyasından]({source_link}) otomatik çekiliyor · veri tarihi: "
            f"{params.source_date.strftime('%d.%m.%Y')}. Aşağıda 'Değiştir' ile her "
            "alanı elle üzerine yazabilirsin."
        )

    with st.expander("Otomatik Çekilen Değerler"):
        price = _streamlit_override_row(
            st, "Güncel Fiyat", params.price, "fut_price", source="Takasbank XML", live=True
        )
        contract_size = _streamlit_override_row(
            st,
            "Kontrat Çarpanı",
            params.contract_size,
            "fut_cvf",
            source="Takasbank XML",
            decimals=2,
        )
        _streamlit_override_row(
            st,
            "Vadeye Kalan Süre (T, yıl)",
            params.time_to_expiry,
            "fut_tte",
            source="Takasbank XML",
            decimals=6,
        )
        st.caption(
            "_T, sadece bilgi amaçlıdır — vadeli işlem teminatı doğrusal bir fiyat "
            "şokuna dayandığı için (Black-Scholes yok) hesaba doğrudan girmez._"
        )
        psr = _streamlit_override_row(
            st, "Price Scan Range (PSR)", params.price_scan_range, "fut_psr", source="Takasbank XML", decimals=4
        )
        emm = _streamlit_override_row(
            st, "Extreme Move Multiplier", params.extreme_move_multiplier, "fut_emm", source="Takasbank XML"
        )
        emcf = _streamlit_override_row(
            st,
            "Extreme Move Covered Fraction",
            params.extreme_move_covered_fraction,
            "fut_emcf",
            source="Takasbank XML",
        )

        st.divider()
        icsc = st.number_input(
            "Vadeler Arası Yayılma Riski (Intra-Commodity Spread Charge, TL)",
            value=0.0,
            min_value=0.0,
            step=0.0001,
            format="%.4f",
            key="fut_icsc",
            help=(
                "Sadece AYNI dayanak varlıkta birden fazla vadeli gerçek bir spread "
                "pozisyonun varsa uygulanır. Tek bacaklı/tek vadeli bir pozisyon için "
                "doğru değer 0'dır."
            ),
        )
        icc = st.number_input(
            "Ürünler Arası Yayılma İndirimi (Inter-Commodity Spread Credit, TL)",
            value=0.0,
            min_value=0.0,
            step=0.0001,
            format="%.4f",
            key="fut_icc",
            help="Farklı ama korelasyonlu ürünler arası spread indirimidir. Sadece gerçek bir spread pozisyonun varsa uygula.",
        )

    if st.button("Hesapla", type="primary"):
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
    st.divider()
    st.metric(
        f"{ticker} {expiry.strftime('%d.%m.%Y')} — Min. Teminat",
        f"{span['total_initial_margin']:,.2f} TL",
    )
    st.caption(
        "Bu tutar, sadece TEK bir vadeli işlem pozisyonu içindir. Gerçek bir "
        "portföyde farklı vade/dayanak varlıklardaki başka pozisyonlar birbirini "
        "etkileyip (spread riski/indirimi nedeniyle) toplam teminat ihtiyacını "
        "düşürebilir ya da artırabilir."
    )
    st.caption(
        f"Tarama Riski (Scanning Risk): {span['scan_risk']:,.2f} TL",
        help="16 SPAN senaryosundan en kötüsü (en büyük zarar).",
    )
    if span["intra_commodity_spread_charge"]:
        st.caption(f"+ Vadeler Arası Yayılma Riski: {span['intra_commodity_spread_charge']:,.2f} TL")
    if span["inter_commodity_spread_credit"]:
        st.caption(f"- Ürünler Arası Yayılma İndirimi: {span['inter_commodity_spread_credit']:,.2f} TL")

    st.subheader("16 SPAN Senaryosu ve P&L (Scanning Risk dökümü)")
    st.markdown(f"En kötü senaryo: #{results['scenarios'].attrs['worst_scenario_no']}")
    st.table(_display_table(results["scenarios"]))


# Streamlit'in "pages/" çalıştırıcısı, seçili sayfa script'ini __name__="__main__"
# olarak çalıştırır (BIST_Opsiyon.py'nin kendi __main__ korumasıyla AYNI davranış,
# bkz. BIST_Opsiyon.py'nin sonu) -- bu da bu dosya yanlışlıkla plain bir modül olarak
# import edilirse (ör. bir test) Streamlit UI kodunun tetiklenmemesini sağlar.
if __name__ == "__main__":
    run_futures_page()
