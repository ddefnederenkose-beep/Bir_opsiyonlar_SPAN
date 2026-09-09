"""Arayüz metinleri için TR/EN çeviri katmanı.

BAĞIMSIZ, SAF-GÖRÜNTÜLEME KATMANI: Bu modül SADECE ekranda gösterilen
metinleri seçer -- hiçbir hesaplama fonksiyonuna (compute_span_result,
calculate_span_margin, _build_scenario_table, _sonuclar_rows,
_format_result_table, _format_comparison_table, futures_engine.py vb.)
DOKUNMAZ ve onlardan HİÇBİRİNİ import etmez. O fonksiyonların döndürdüğü
DataFrame'lerin sütun adları/satır etiketleri hâlâ (testlerle kilitli
olduğu için, bkz. tests/test_bist_opsiyon.py) TÜRKÇE ve SABİTTİR --
İngilizce görünümde bunlar BIST_Opsiyon.py/vadeli işlem sayfası
tarafından, bu modüldeki DISPLAY_COLUMN_TRANSLATIONS/DISPLAY_FIELD_TRANSLATIONS
sözlükleriyle SADECE GÖRÜNTÜLEME ANINDA (bir kopya üzerinde) yeniden
etiketlenir -- hesaplama katmanının kendisi hiç değişmez.

Kullanım:
    from bist_span import i18n
    lang = i18n.get_lang(st)          # "tr" ya da "en", session_state'ten
    st.title(i18n.t("title", lang))   # anahtar bulunamazsa TR'ye düşer

Para birimi notu: Türkçe arayüzde "TL" (günlük kısaltma) kullanılır;
İngilizce arayüzde ISO 4217 kodu "TRY" kullanılır -- "TL" İngilizce
finans terminolojisinde tanınan/standart bir kısaltma DEĞİLDİR, "TRY"
uluslararası standarttır (bkz. kullanıcı talebi: "İngilizce finans
terminolojisinde farklı bir anlamı varsa bunu göz önüne al").
"""

from __future__ import annotations

import re

DEFAULT_LANG = "tr"
LANGUAGES = ("tr", "en")


def get_lang(st) -> str:
    """Aktif arayüz dilini session_state'ten okur (yoksa TR varsayılan)."""
    lang = st.session_state.get("lang", DEFAULT_LANG)
    return lang if lang in LANGUAGES else DEFAULT_LANG


def t(key: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
    """Anahtara karşılık gelen metni döner.

    Args:
        key: TRANSLATIONS'taki anahtar.
        lang: "tr" ya da "en".
        **kwargs: Şablonun içindeki {yer_tutucular} için ÖNCEDEN
            biçimlendirilmiş (ör. zaten ",.2f" uygulanmış) string parçaları.
            Sayısal biçimlendirme burada DEĞİL, çağıran tarafta yapılır --
            böylece bu modül hiçbir sayısal formatlama kararı almaz.

    Anahtar TRANSLATIONS'ta yoksa (programlama hatası, olmamalı) anahtarın
    kendisini döner -- sessizce patlamak yerine ekranda görünür bir iz bırakır.
    """
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key
    template = entry.get(lang) or entry.get(DEFAULT_LANG, key)
    return template.format(**kwargs) if kwargs else template


TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- Üst navigasyon (BIST_Opsiyon.py: _top_nav) ---------------------
    "nav_options": {"tr": "📊 Opsiyon", "en": "📊 Options"},
    "nav_futures": {"tr": "📈 Vadeli İşlem", "en": "📈 Futures"},
    # --- Opsiyon sayfası: başlık / giriş ---------------------------------
    "page_title_options": {
        "tr": "VİOP SPAN Teminat Hesaplama — BIST Opsiyon Marjin Hesaplayıcı",
        "en": "VIOP SPAN Margin Calculator — BIST Option Margin Calculator",
    },
    "title_options": {
        "tr": "BIST Opsiyonları — Minimum SPAN Teminatı (Call & Put)",
        "en": "BIST Options — Minimum SPAN Margin (Call & Put)",
    },
    "intro_options": {
        "tr": (
            "Bir opsiyonu satıp (yazıp) kısa pozisyon aldığında, Takasbank'ın "
            "senden isteyeceği minimum başlangıç teminatını SPAN metodolojisiyle hesaplar.  \n"
            "Opsiyonu alan (uzun pozisyon) taraf için teminat gerekmez — bu hesap sadece opsiyon satıcıları içindir."
        ),
        "en": (
            "When you sell (write) an option and take a short position, this calculates "
            "the minimum initial margin Takasbank will require from you using the SPAN methodology.  \n"
            "No margin is required for the buyer (long position) — this calculator is for option sellers/writers only."
        ),
    },
    "caption_intro_options": {
        "tr": (
            "Firma ve vade gir, 'Verileri Çek'e bas. Güncel fiyat/volatilite ve "
            "Takasbank risk parametreleri otomatik çekilir; istersen her bileşeni "
            "aşağıda tek tek değiştirebilirsin."
        ),
        "en": (
            "Enter the stock and expiration, then click 'Fetch Data'. Current price/volatility "
            "and Takasbank risk parameters are fetched automatically; you can override each "
            "component individually below if you want."
        ),
    },
    "advanced_settings": {"tr": "Gelişmiş ayarlar", "en": "Advanced settings"},
    "risk_params_file_label": {
        "tr": "Takasbank Risk Parametre Dosyası (PDF/JSON yolu)",
        "en": "Takasbank Risk Parameter File (PDF/JSON path)",
    },
    "contracts_label": {
        "tr": "Kontrat Sayısı (kısa pozisyon için negatif)",
        "en": "Number of Contracts (negative for a short position)",
    },
    "risk_params_read_error": {
        "tr": "Risk parametre dosyası okunamadı: {error}",
        "en": "Could not read the risk parameter file: {error}",
    },
    "ticker_label": {"tr": "Hisse", "en": "Stock"},
    "ticker_help": {
        "tr": (
            "Bu liste, seçili risk parametre dosyasında tam opsiyon "
            "verisi (PSR/VSR/SOM vb.) bulunan hisselerdir -- resmi "
            "BIST30 endeks listesiyle birebir aynı olmayabilir."
        ),
        "en": (
            "This list contains stocks with complete option data (PSR/VSR/SOM, etc.) "
            "in the selected risk parameter file — it may not exactly match the official "
            "BIST30 index list."
        ),
    },
    "fetch_data_button": {"tr": "Verileri Çek", "en": "Fetch Data"},
    "fetch_data_spinner": {
        "tr": (
            "Fiyat/volatilite, risk parametreleri ve Takasbank verileri çekiliyor "
            "(ilk çekişte ~10-30 saniye sürebilir)..."
        ),
        "en": (
            "Fetching price/volatility, risk parameters, and Takasbank data "
            "(the first fetch can take ~10-30 seconds)..."
        ),
    },
    "fetch_data_error": {
        "tr": "Veri çekilemedi: {error}",
        "en": "Could not fetch data: {error}",
    },
    "ticker_changed_info": {
        "tr": "Hisse değişti — tekrar 'Verileri Çek'e bas.",
        "en": "Stock changed — click 'Fetch Data' again.",
    },
    # --- Veri kaynağı / son güncelleme bilgi bloğu -----------------------
    "status_eod": {
        "tr": "gün sonu (EOD, o günün nihai verisi)",
        "en": "end of day (EOD, that day's final data)",
    },
    "status_intraday": {
        "tr": "gün içi ara güncelleme — daha yeni bir dosya çıktıkça otomatik yenilenir",
        "en": "intraday update — automatically refreshes as newer files are published",
    },
    "published_line": {
        "tr": "Takasbank'ın yayınladığı belge: {timestamp}",
        "en": "Document published by Takasbank: {timestamp}",
    },
    "cached_line": {
        "tr": "son güncelleme (bizim çekişimiz): {timestamp}",
        "en": "last fetched (by us): {timestamp}",
    },
    "data_banner_options": {
        "tr": (
            ":green[●] Spot, taban/piyasa fiyatı, T, faiz oranı, PSR, VSR, "
            "Extreme Move ve implied volatility [Takasbank'ın günlük PC-SPAN "
            "dosyasından]({link}) otomatik çekiliyor · veri tarihi: "
            "{date} · {update_line} ({status}). Aşağıda 'Değiştir' ile her alanı "
            "elle üzerine yazabilirsin."
        ),
        "en": (
            ":green[●] Spot price, base/market price, T, interest rate, PSR, VSR, "
            "Extreme Move, and implied volatility are fetched automatically from "
            "[Takasbank's daily PC-SPAN file]({link}) · data date: "
            "{date} · {update_line} ({status}). You can manually override any field "
            "below using 'Override'."
        ),
    },
    "data_unavailable_warning": {
        "tr": (
            "⚠️ Takasbank'ın günlük XML verisi şu an çekilemedi — spot/taban fiyat "
            "ve risk parametreleri için yedek kaynaklara (PDF / yfinance / teorik "
            "hesap) düşülüyor."
        ),
        "en": (
            "⚠️ Takasbank's daily XML data could not be fetched right now — falling "
            "back to backup sources (PDF / yfinance / theoretical calculation) for "
            "spot/base price and risk parameters."
        ),
    },
    # --- Vade / Strike ----------------------------------------------------
    "expiry_strike_subheader": {"tr": "Vade ve Strike", "en": "Expiration and Strike"},
    "expiry_label": {"tr": "Vade Tarihi", "en": "Expiration Date"},
    "expiry_help": {
        "tr": "Takasbank'ın güncel dosyasında bu hisse için gerçekten mevcut olan vadeler.",
        "en": "Expiration dates actually available for this stock in Takasbank's current file.",
    },
    "expiry_missing_warning": {
        "tr": (
            "Bu hisse için Takasbank XML verisi bulunamadı — vade tarihini elle gir. "
            "T/faiz/PSR/VSR/volatilite otomatik çekilemeyecek, mevcut kaynaklara "
            "(Takasbank PDF / yfinance historical) düşülecek."
        ),
        "en": (
            "No Takasbank XML data found for this stock — enter the expiration date "
            "manually. T/interest rate/PSR/VSR/volatility cannot be fetched "
            "automatically and will fall back to available sources "
            "(Takasbank PDF / yfinance historical)."
        ),
    },
    "strike_label": {"tr": "Kullanım Fiyatı (Strike)", "en": "Strike Price"},
    "strike_label_manual": {"tr": "Kullanım Fiyatı", "en": "Strike Price"},
    "strike_help": {
        "tr": (
            "Takasbank'ın bu vade için gerçekten listelediği strike'lar "
            "(güncel fiyata en yakını varsayılan)."
        ),
        "en": (
            "Strike prices actually listed by Takasbank for this expiration "
            "(the one closest to the current price is selected by default)."
        ),
    },
    "both_missing_caption": {
        "tr": "CALL {strike} ve PUT {strike}, bu pozisyonlar bu tarihte işlem görmemektedir.",
        "en": "CALL {strike} and PUT {strike} — these positions did not trade on this date.",
    },
    "call_missing_caption": {
        "tr": "CALL {strike}, bu pozisyon bu tarihte işlem görmemektedir, sadece PUT {strike} pozisyonu bulunmaktadır.",
        "en": "CALL {strike} did not trade on this date — only the PUT {strike} position is available.",
    },
    "put_missing_caption": {
        "tr": "PUT {strike}, bu pozisyon bu tarihte işlem görmemektedir, sadece CALL {strike} pozisyonu bulunmaktadır.",
        "en": "PUT {strike} did not trade on this date — only the CALL {strike} position is available.",
    },
    # --- T (vadeye kalan süre) --------------------------------------------
    "tte_field_label": {
        "tr": "Vadeye Kalan Süre (T, yıl)",
        "en": "Time to Expiration (T, years)",
    },
    "tte_auto_business_days": {
        "tr": "Otomatik: {value}  ·  _(iş günü/250)_",
        "en": "Auto: {value}  ·  _(business days/250)_",
    },
    "tte_auto_calendar_days": {
        "tr": "Otomatik: {value} ({days} gün / 365)  ·  _hesaplanan_",
        "en": "Auto: {value} ({days} days / 365)  ·  _calculated_",
    },
    "override_checkbox": {"tr": "Değiştir", "en": "Override"},
    "tte_input_label": {"tr": "T (yıl)", "en": "T (years)"},
    "tte_help": {
        "tr": "Bir referans hesaplayıcının (Excel vb.) ondalık T'siyle birebir karşılaştırmak için kullan.",
        "en": "Use this to compare exactly against a reference calculator's (e.g. Excel) decimal T value.",
    },
    "tte_info_futures": {
        "tr": (
            "_T, sadece bilgi amaçlıdır — vadeli işlem teminatı doğrusal bir fiyat "
            "şokuna dayandığı için (Black-Scholes yok) hesaba doğrudan girmez._"
        ),
        "en": (
            "_T is for informational purposes only — since futures margin is based on "
            "a linear price shock (no Black-Scholes), it does not directly enter the calculation._"
        ),
    },
    # --- Otomatik çekilen değerler / alan etiketleri ----------------------
    "auto_fetched_expander": {"tr": "Otomatik Çekilen Değerler", "en": "Auto-Fetched Values"},
    "field_spot": {"tr": "Güncel Fiyat (Spot)", "en": "Current Price (Spot)"},
    "field_current_price_futures": {"tr": "Güncel Fiyat", "en": "Current Price"},
    "field_contract_multiplier": {"tr": "Kontrat Çarpanı", "en": "Contract Multiplier"},
    "field_risk_free_rate": {"tr": "Risksiz Faiz Oranı", "en": "Risk-Free Interest Rate"},
    "field_psr": {"tr": "Price Scan Range (PSR)", "en": "Price Scan Range (PSR)"},
    "field_vsr": {"tr": "Volatility Scan Range (VSR)", "en": "Volatility Scan Range (VSR)"},
    "field_emm": {"tr": "Extreme Move Multiplier", "en": "Extreme Move Multiplier"},
    "field_emcf": {
        "tr": "Extreme Move Covered Fraction",
        "en": "Extreme Move Covered Fraction",
    },
    "field_som": {"tr": "Short Option Minimum (SOM)", "en": "Short Option Minimum (SOM)"},
    "source_takasbank_xml": {"tr": "Takasbank XML", "en": "Takasbank XML"},
    "source_takasbank_pdf": {
        "tr": "Takasbank dökümanı (PDF)",
        "en": "Takasbank document (PDF)",
    },
    "source_default_no_xml": {
        "tr": "varsayılan (Takasbank XML bulunamadı)",
        "en": "default (Takasbank XML not found)",
    },
    "source_yfinance_fallback": {
        "tr": "yfinance (fallback — Takasbank'ta bulunamadı)",
        "en": "yfinance (fallback — not found in Takasbank)",
    },
    "source_yfinance_iv_missing": {
        "tr": "yfinance historical (IV bulunamadı)",
        "en": "yfinance historical (IV not found)",
    },
    "source_theoretical_bs": {
        "tr": "teorik Black-Scholes (Takasbank piyasa fiyatı bulunamadı)",
        "en": "theoretical Black-Scholes (Takasbank market price not found)",
    },
    "volatility_header": {"tr": "**Volatilite**", "en": "**Volatility**"},
    "field_call_vol": {"tr": "Volatilite — Call", "en": "Volatility — Call"},
    "field_put_vol": {"tr": "Volatilite — Put", "en": "Volatility — Put"},
    "settlement_price_header": {
        "tr": "**Call/Put Opsiyon Uzlaşma Fiyatı**",
        "en": "**Call/Put Option Settlement Price**",
    },
    "field_call_base": {"tr": "Taban Fiyat — Call", "en": "Base Price — Call"},
    "field_put_base": {"tr": "Taban Fiyat — Put", "en": "Base Price — Put"},
    "icsc_label_options": {
        "tr": "Vadeler Arası Spread Ücreti (Intra-Commodity Spread Charge, TL)",
        "en": "Intra-Commodity Spread Charge (TRY)",
    },
    "icsc_help": {
        "tr": (
            "Takasbank'ın {ticker} için yayınladığı referans değer: {value} TL / "
            "spread birimi. Bu ücret SADECE aynı dayanak varlıkta birden fazla "
            "vadeli gerçek bir spread pozisyonun varsa uygulanır. Aşağıdaki tek "
            "bacaklı/tek vadeli pozisyon için doğru değer 0'dır — spread "
            "pozisyonun olduğunu biliyorsan alanı değiştir."
        ),
        "en": (
            "Takasbank's published reference value for {ticker}: {value} TRY / "
            "spread unit. This charge applies ONLY if you actually hold a "
            "multi-expiration spread position in the same underlying. The correct "
            "value for the single-leg/single-expiration position below is 0 — "
            "override this field only if you actually have a spread position."
        ),
    },
    # --- Hesapla / sonuçlar -------------------------------------------------
    "calculate_button": {"tr": "Hesapla", "en": "Calculate"},
    "both_missing_error": {
        "tr": (
            "Ne CALL ne de PUT bu strike/vade için Takasbank verisinde "
            "bulunuyor — bu pozisyon bu tarihte işlem görmemektedir, "
            "hesaplama yapılamaz."
        ),
        "en": (
            "Neither CALL nor PUT is found in Takasbank data for this strike/expiration "
            "— this position did not trade on this date, calculation is not possible."
        ),
    },
    "call_min_margin_label": {"tr": "Call — Min. Teminat", "en": "Call — Min. Margin"},
    "put_min_margin_label": {"tr": "Put — Min. Teminat", "en": "Put — Min. Margin"},
    "call_not_traded_warning": {
        "tr": "CALL bu tarihte işlem görmemektedir.",
        "en": "CALL did not trade on this date.",
    },
    "put_not_traded_warning": {
        "tr": "PUT bu tarihte işlem görmemektedir.",
        "en": "PUT did not trade on this date.",
    },
    "span_risk_caption": {"tr": "SPAN Risk: {value} TL", "en": "SPAN Risk: {value} TRY"},
    "span_risk_help_base": {
        "tr": "16 SPAN senaryosundan en kötüsü (Scanning Risk).",
        "en": "The worst of the 16 SPAN scenarios (Scanning Risk).",
    },
    "span_risk_help_som": {
        "tr": (
            " Bu pozisyonda Scanning Risk ({scan} TL), Short Option Minimum'un "
            "({som} TL) altında kaldığı için SOM tabanı uygulandı."
        ),
        "en": (
            " In this position, Scanning Risk ({scan} TRY) fell below the Short "
            "Option Minimum ({som} TRY), so the SOM floor was applied."
        ),
    },
    "nov_caption": {"tr": "NOV: {value} TL", "en": "NOV: {value} TRY"},
    "nov_help": {
        "tr": (
            "Net Opsiyon Değeri (Madde 37/2): bu kısa opsiyonu ŞU AN geri satın "
            "alma maliyeti (|kontrat| × piyasa fiyatı × kontrat çarpanı). Kısa "
            "pozisyon için her zaman teminata EKLENİR -- Takasbank'ın resmi "
            "ekranında 'Available Net Option' olarak geçer."
        ),
        "en": (
            "Net Option Value (Article 37/2): the cost to buy back this short option "
            "RIGHT NOW (|contracts| × market price × contract multiplier). For a "
            "short position, this is always ADDED to the margin — it appears as "
            "'Available Net Option' on Takasbank's official screen."
        ),
    },
    "total_caption": {"tr": "**Toplam: {value} TL**", "en": "**Total: {value} TRY**"},
    "final_note_options": {
        "tr": (
            "Gösterilen tutar, BISTECH/SPAN riski ve kısa opsiyonun güncel "
            "değeri dikkate alınarak hesaplanmıştır. Opsiyon satışından elde "
            "edilen prim bu hesaplamaya dahil edilmemiştir. İşlem gününde "
            "tahsil edilen opsiyon primi, Takasbank hesaplamasında başlangıç "
            "teminatı ihtiyacını azaltabilir."
        ),
        "en": (
            "The amount shown is calculated based on the BISTECH/SPAN risk and the "
            "short option's current value. The premium received from selling the "
            "option is not included in this calculation. The option premium collected "
            "on the trade date may reduce the initial margin requirement in Takasbank's "
            "calculation."
        ),
    },
    # --- Aracı kurum çarpanları ---------------------------------------------
    "broker_expander": {
        "tr": "Aracı Kurumların Takasbank Minimum Teminatına Uyguladığı Çarpanlar",
        "en": "Multipliers Brokerages Apply to Takasbank's Minimum Margin",
    },
    "broker_intro": {
        "tr": (
            "Takasbank, VİOP'ta işlem gören her kontrat için SPAN bazlı asgari "
            "(minimum) teminat tutarlarını belirler ve yayınlar. Ancak aracı "
            "kurumlar, kendi risk yönetimi politikaları gereği bu asgari "
            "tutarın üzerine ek bir güvenlik marjı koyabilir. Tespit edilen "
            "bazı aracı kurumların uyguladığı çarpanlar:"
        ),
        "en": (
            "Takasbank sets and publishes SPAN-based minimum margin amounts for "
            "every contract traded on VIOP. However, brokerages may add an extra "
            "safety margin on top of this minimum under their own risk management "
            "policies. Multipliers identified for some brokerages:"
        ),
    },
    "broker_list": {
        "tr": (
            "- [Garanti BBVA Yatırım](https://www.garantibbvayatirim.com.tr/urunlerimiz/viop): 2x Min Teminat\n"
            "- [Ziraat Yatırım](https://www.ziraatyatirim.com.tr/tr/turev-araclar-v%C4%B1op): 2,00x Min Teminat\n"
            "- [Fiba Yatırım](https://www.fibayatirim.com.tr/viop-teminat-tamamlama-span-carpani-ve-stop-out-uygulamasi-hakkinda-bilgilendirme): "
            "1,5x Min Teminat (Takasbank'ın güncel SPAN parametreleri üzerinden)\n"
            "- [Tacirler Yatırım](https://tacirler.com.tr/viop-teminat-rasyolarinin-guncellenmesi-hk-02-01-2025): "
            "1x — Takasbank'ın uyguladığı oranları doğrudan kullanıyor, ek çarpan yok "
            "(kaynak Ocak 2025 tarihli, teyide açık)\n"
            "- [Osmanlı Menkul](https://www.osmanlimenkul.com.tr/hisse-ve-viop/hisse-ve-viop-urunlerimiz/hisse-turev/viop-teminat-ve-limit-bilgileri): "
            "kullanılan teminat 7.500.000 TL eşiğini aştığında kademeli çarpan uygulanıyor "
            "(tam sayısal değer sayfada belirtilmiyor, dosyaya bağlı)\n"
            "- [IKON Menkul](http://www.ikonmenkul.com.tr/viop-baslangic-teminatlari): "
            "Takasbank oranlarına piyasa koşullarına göre değişken \"Ek Teminat\" uyguluyor "
            "(sabit bir çarpan belirtilmiyor)"
        ),
        "en": (
            "- [Garanti BBVA Yatırım](https://www.garantibbvayatirim.com.tr/urunlerimiz/viop): 2x Min. Margin\n"
            "- [Ziraat Yatırım](https://www.ziraatyatirim.com.tr/tr/turev-araclar-v%C4%B1op): 2.00x Min. Margin\n"
            "- [Fiba Yatırım](https://www.fibayatirim.com.tr/viop-teminat-tamamlama-span-carpani-ve-stop-out-uygulamasi-hakkinda-bilgilendirme): "
            "1.5x Min. Margin (based on Takasbank's current SPAN parameters)\n"
            "- [Tacirler Yatırım](https://tacirler.com.tr/viop-teminat-rasyolarinin-guncellenmesi-hk-02-01-2025): "
            "1x — uses Takasbank's rates directly, no extra multiplier "
            "(source dated January 2025, subject to confirmation)\n"
            "- [Osmanlı Menkul](https://www.osmanlimenkul.com.tr/hisse-ve-viop/hisse-ve-viop-urunlerimiz/hisse-turev/viop-teminat-ve-limit-bilgileri): "
            "a tiered multiplier applies once the margin used exceeds the 7,500,000 TRY "
            "threshold (the exact numeric value is not stated on the page, depends on the account)\n"
            "- [IKON Menkul](http://www.ikonmenkul.com.tr/viop-baslangic-teminatlari): "
            "applies a variable \"Additional Margin\" to Takasbank's rates depending on "
            "market conditions (no fixed multiplier stated)"
        ),
    },
    "components_button": {"tr": "Bileşenler", "en": "Breakdown"},
    "how_it_works_subheader": {
        "tr": "SPAN Mekanizması Nasıl Çalışır?",
        "en": "How Does the SPAN Mechanism Work?",
    },
    "how_it_works_body": {
        "tr": (
            "SPAN, dayanak varlığın fiyatının ve volatilitesinin farklı yönlerde "
            "hareket ettiği **16 farklı risk senaryosu** kurar: fiyat için "
            "Price Scan Range'in (PSR) 0, ±1/3, ±2/3 ve ±tamamı kadar şoklar, "
            "her fiyat seviyesinde volatilite için hem yukarı hem aşağı şoklar "
            "(bu 14 senaryoyu oluşturur), artı PSR'nin çok daha büyük bir katı "
            "kadar (Extreme Move Multiplier) 2 'aşırı hareket' senaryosu daha.\n\n"
            "Her senaryoda opsiyon Black-Scholes ile yeniden fiyatlanır ve kısa "
            "pozisyonun o senaryodaki kâr/zararı hesaplanır. **En kötü (en büyük "
            "zararlı) senaryo** 'Scanning Risk' olarak seçilir — çünkü teminat, "
            "olabilecek en kötü tek günlük hareketi karşılayacak kadar olmalıdır."
        ),
        "en": (
            "SPAN builds **16 different risk scenarios** in which the underlying "
            "asset's price and volatility move in different directions: for price, "
            "shocks of 0, ±1/3, ±2/3, and the full amount of the Price Scan Range "
            "(PSR); at each price level, both upward and downward volatility shocks "
            "(this makes up 14 scenarios), plus 2 more 'extreme move' scenarios at a "
            "much larger multiple of the PSR (the Extreme Move Multiplier).\n\n"
            "In each scenario, the option is repriced with Black-Scholes and the "
            "short position's profit/loss under that scenario is calculated. The "
            "**worst (largest-loss) scenario** is selected as the 'Scanning Risk' "
            "— because the margin must be enough to cover the worst possible "
            "one-day move."
        ),
    },
    "scenario_table_subheader": {
        "tr": "16 SPAN Senaryosu ve P&L (Scanning Risk dökümü)",
        "en": "16 SPAN Scenarios and P&L (Scanning Risk Breakdown)",
    },
    "call_worst_scenario": {
        "tr": "**Call** — en kötü senaryo: #{n}",
        "en": "**Call** — worst scenario: #{n}",
    },
    "put_worst_scenario": {
        "tr": "**Put** — en kötü senaryo: #{n}",
        "en": "**Put** — worst scenario: #{n}",
    },
    "worst_scenario_line": {
        "tr": "En kötü senaryo: #{n}",
        "en": "Worst scenario: #{n}",
    },
    "call_no_scenario": {
        "tr": "CALL bu tarihte işlem görmemektedir — senaryo tablosu yok.",
        "en": "CALL did not trade on this date — no scenario table.",
    },
    "put_no_scenario": {
        "tr": "PUT bu tarihte işlem görmemektedir — senaryo tablosu yok.",
        "en": "PUT did not trade on this date — no scenario table.",
    },
    # --- Vadeli işlem sayfası ------------------------------------------------
    "page_title_futures": {
        "tr": "BIST Vadeli İşlem SPAN Teminat Hesaplama",
        "en": "BIST Futures SPAN Margin Calculator",
    },
    "title_futures": {
        "tr": "BIST Vadeli İşlem — Minimum SPAN Teminatı",
        "en": "BIST Futures — Minimum SPAN Margin",
    },
    "intro_futures": {
        "tr": (
            "Bir vadeli işlem sözleşmesinde (uzun ya da kısa fark etmeksizin) Takasbank'ın "
            "senden isteyeceği minimum başlangıç teminatını SPAN metodolojisiyle hesaplar.  \n"
            "Future'da risk simetriktir — hem alıcı hem satıcı taraf, piyasa aleyhe hareket "
            "ettiğinde sınırsız kayıp riski taşır, bu yüzden ikisi de aynı şekilde "
            "teminatlandırılır."
        ),
        "en": (
            "Calculates the minimum initial margin Takasbank will require from you on a "
            "futures contract (long or short, it doesn't matter) using the SPAN methodology.  \n"
            "Risk is symmetric in futures — both the buyer and the seller carry unlimited "
            "loss risk when the market moves against them, so both sides are margined "
            "the same way."
        ),
    },
    "caption_intro_futures": {
        "tr": (
            "Hisse ve vade seç, 'Hesapla'ya bas. Güncel fiyat ve Takasbank risk parametreleri "
            "(PSR, Extreme Move) otomatik çekilir; istersen her bileşeni aşağıda tek tek "
            "değiştirebilirsin."
        ),
        "en": (
            "Select the stock and expiration, then click 'Calculate'. Current price and "
            "Takasbank risk parameters (PSR, Extreme Move) are fetched automatically; you "
            "can override each component individually below if you want."
        ),
    },
    "fetch_futures_spinner": {
        "tr": "Takasbank vadeli işlem verisi çekiliyor...",
        "en": "Fetching Takasbank futures data...",
    },
    "fetch_futures_error": {
        "tr": "Vadeli işlem verisi çekilemedi: {error}",
        "en": "Could not fetch futures data: {error}",
    },
    "no_stock_futures_warning": {
        "tr": "Şu an Takasbank verisinde hisse senedi vadeli işlem sözleşmesi bulunamadı.",
        "en": "No stock futures contract found in Takasbank's data right now.",
    },
    "ticker_help_futures": {
        "tr": (
            "Takasbank'ın güncel PC-SPAN dosyasında gerçek (sanal marjin serisi "
            "olmayan) bir vadeli işlem sözleşmesi bulunan hisse senedi semboller."
        ),
        "en": (
            "Stock symbols that have a real futures contract (not a virtual margin "
            "series) in Takasbank's current PC-SPAN file."
        ),
    },
    "no_expiry_warning_futures": {
        "tr": "{ticker} için Takasbank verisinde vadeli işlem sözleşmesi bulunamadı.",
        "en": "No futures contract found in Takasbank's data for {ticker}.",
    },
    "data_banner_futures": {
        "tr": (
            ":green[●] Fiyat, T, PSR ve Extreme Move [Takasbank'ın günlük PC-SPAN "
            "dosyasından]({link}) otomatik çekiliyor · veri tarihi: "
            "{date} · {update_line} ({status}). Aşağıda 'Değiştir' ile her alanı "
            "elle üzerine yazabilirsin."
        ),
        "en": (
            ":green[●] Price, T, PSR, and Extreme Move are fetched automatically from "
            "[Takasbank's daily PC-SPAN file]({link}) · data date: "
            "{date} · {update_line} ({status}). You can manually override any field "
            "below using 'Override'."
        ),
    },
    "data_banner_futures_no_info": {
        "tr": (
            "Fiyat, T, PSR ve Extreme Move [Takasbank'ın günlük PC-SPAN "
            "dosyasından]({link}) otomatik çekiliyor · veri tarihi: "
            "{date}. Aşağıda 'Değiştir' ile her alanı elle üzerine yazabilirsin."
        ),
        "en": (
            "Price, T, PSR, and Extreme Move are fetched automatically from "
            "[Takasbank's daily PC-SPAN file]({link}) · data date: "
            "{date}. You can manually override any field below using 'Override'."
        ),
    },
    "icsc_label_futures": {
        "tr": "Vadeler Arası Yayılma Riski (Intra-Commodity Spread Charge, TL)",
        "en": "Intra-Commodity Spread Charge (TRY)",
    },
    "icsc_help_futures_leg": {
        "tr": (
            "Sadece AYNI dayanak varlıkta birden fazla vadeli gerçek bir spread "
            "pozisyonun varsa uygulanır. Tek bacaklı/tek vadeli bir pozisyon için "
            "doğru değer 0'dır."
        ),
        "en": (
            "This applies only if you actually hold a multi-expiration spread "
            "position in the SAME underlying. The correct value for a "
            "single-leg/single-expiration position is 0."
        ),
    },
    "field_icc": {
        "tr": "Ürünler Arası Yayılma İndirimi (Inter-Commodity Spread Credit, TL)",
        "en": "Inter-Commodity Spread Credit (TRY)",
    },
    "icc_help": {
        "tr": (
            "Farklı ama korelasyonlu ürünler arası spread indirimidir. Sadece "
            "gerçek bir spread pozisyonun varsa uygula."
        ),
        "en": (
            "This is a spread credit between different but correlated products. "
            "Only apply it if you actually have a spread position."
        ),
    },
    "min_margin_metric_futures": {
        "tr": "{ticker} {expiry} — Min. Teminat",
        "en": "{ticker} {expiry} — Min. Margin",
    },
    "single_position_note": {
        "tr": (
            "Bu tutar, sadece TEK bir vadeli işlem pozisyonu içindir. Gerçek bir "
            "portföyde farklı vade/dayanak varlıklardaki başka pozisyonlar birbirini "
            "etkileyip (spread riski/indirimi nedeniyle) toplam teminat ihtiyacını "
            "düşürebilir ya da artırabilir."
        ),
        "en": (
            "This amount is for a SINGLE futures position only. In a real portfolio, "
            "other positions in different expirations/underlyings can interact "
            "(through spread charges/credits) to lower or raise the total margin "
            "requirement."
        ),
    },
    "scanning_risk_caption": {
        "tr": "Tarama Riski (Scanning Risk): {value} TL",
        "en": "Scanning Risk: {value} TRY",
    },
    "scanning_risk_help": {
        "tr": "16 SPAN senaryosundan en kötüsü (en büyük zarar).",
        "en": "The worst (largest loss) of the 16 SPAN scenarios.",
    },
    "icsc_contribution_caption": {
        "tr": "+ Vadeler Arası Yayılma Riski: {value} TL",
        "en": "+ Intra-Commodity Spread Charge: {value} TRY",
    },
    "icc_contribution_caption": {
        "tr": "- Ürünler Arası Yayılma İndirimi: {value} TL",
        "en": "- Inter-Commodity Spread Credit: {value} TRY",
    },
    # --- 16 senaryo tablosu (görüntüleme anında yeniden etiketleme) --------
    "col_scen": {"tr": "Sen.", "en": "Scen."},
    "col_description": {"tr": "Açıklama", "en": "Description"},
    "col_price_multiplier": {"tr": "Fiyat Çarpanı", "en": "Price Multiplier"},
    "col_vol_direction": {"tr": "Vol Yönü", "en": "Vol Direction"},
    "col_s_new": {"tr": "S_yeni", "en": "S_new"},
    "col_iv_new": {"tr": "IV_yeni", "en": "IV_new"},
    "col_call_price": {"tr": "Call Fiyatı", "en": "Call Price"},
    "col_put_price": {"tr": "Put Fiyatı", "en": "Put Price"},
    "col_shocked_price": {"tr": "Şoklu Fiyat", "en": "Shocked Price"},
    "col_difference": {"tr": "Fark", "en": "Difference"},
    "col_short_pnl": {"tr": "Kısa K/Z (TL)", "en": "Short P&L (TRY)"},
    # --- "Bileşenler" detay tablosu (görüntüleme anında yeniden etiketleme) -
    "col_field": {"tr": "Alan", "en": "Field"},
    "col_value": {"tr": "Değer", "en": "Value"},
    "row_stock": {"tr": "Hisse", "en": "Stock"},
    "row_strike": {"tr": "Strike", "en": "Strike"},
    "row_option_type": {"tr": "Opsiyon Tipi", "en": "Option Type"},
    "row_contracts": {"tr": "Kontrat Sayısı", "en": "Number of Contracts"},
    "row_expiry": {"tr": "Vade Tarihi", "en": "Expiration Date"},
    "row_tte": {"tr": "Vadeye Kalan Süre (yıl)", "en": "Time to Expiration (years)"},
    "row_spot": {"tr": "Güncel Fiyat (Spot)", "en": "Current Price (Spot)"},
    "row_hist_vol": {"tr": "Historical Volatility", "en": "Historical Volatility"},
    "row_option_price": {"tr": "Opsiyon Fiyatı (şoksuz)", "en": "Option Price (unshocked)"},
    "row_risk_params_header": {
        "tr": "--- Risk Parametreleri ---",
        "en": "--- Risk Parameters ---",
    },
    "row_psr": {"tr": "Price Scan Range (PSR)", "en": "Price Scan Range (PSR)"},
    "row_vsr": {"tr": "Volatility Scan Range (VSR)", "en": "Volatility Scan Range (VSR)"},
    "row_emm": {"tr": "Extreme Move Multiplier", "en": "Extreme Move Multiplier"},
    "row_emcf": {
        "tr": "Extreme Move Covered Fraction",
        "en": "Extreme Move Covered Fraction",
    },
    "row_icsc_ref": {
        "tr": "Intra-Commodity Spread Charge (Takasbank referans)",
        "en": "Intra-Commodity Spread Charge (Takasbank reference)",
    },
    "row_results_header": {"tr": "--- SONUÇLAR ---", "en": "--- RESULTS ---"},
    "row_scan_risk": {"tr": "Scanning Risk (TL)", "en": "Scanning Risk (TRY)"},
    "row_icsc_plus": {"tr": "+ Intra-Commodity Spread", "en": "+ Intra-Commodity Spread"},
    "row_delivery_plus": {"tr": "+ Delivery Risk", "en": "+ Delivery Risk"},
    "row_total_no_som": {"tr": "Toplam (SOMsuz)", "en": "Total (excl. SOM)"},
    "row_som": {"tr": "Short Option Minimum (SOM)", "en": "Short Option Minimum (SOM)"},
    "row_total_margin": {
        "tr": "TOPLAM BAŞLANGIÇ TEMİNATI",
        "en": "TOTAL INITIAL MARGIN",
    },
    "row_active_scenario": {"tr": "Aktif Senaryo #", "en": "Active Scenario #"},
}


# 16-senaryo tablosunun (_build_scenario_table / _futures_scenario_table)
# TÜRKÇE sütun adlarından, ilgili çeviri anahtarına eşleme -- görüntüleme
# anında DataFrame.rename(columns=...) ile kullanılır. Hesaplama katmanı
# hâlâ bu Türkçe adları üretir (bkz. modül docstring'i); burada SADECE
# ekrana basılacak KOPYA yeniden adlandırılır.
SCENARIO_COLUMN_KEYS: dict[str, str] = {
    "Sen.": "col_scen",
    "Açıklama": "col_description",
    "Fiyat Çarpanı": "col_price_multiplier",
    "Vol Yönü": "col_vol_direction",
    "S_yeni": "col_s_new",
    "IV_yeni": "col_iv_new",
    "Call Fiyatı": "col_call_price",
    "Put Fiyatı": "col_put_price",
    "Şoklu Fiyat": "col_shocked_price",
    "Fark": "col_difference",
    "Kısa K/Z (TL)": "col_short_pnl",
}

# "Bileşenler" (_format_result_table / _format_comparison_table) tablosunun
# TÜRKÇE "Alan" satır etiketlerinden çeviri anahtarına eşleme -- yukarıdakiyle
# AYNI mantık, sadece satır etiketleri için.
FIELD_ROW_KEYS: dict[str, str] = {
    "Hisse": "row_stock",
    "Strike": "row_strike",
    "Opsiyon Tipi": "row_option_type",
    "Kontrat Sayısı": "row_contracts",
    "Vade Tarihi": "row_expiry",
    "Vadeye Kalan Süre (yıl)": "row_tte",
    "Güncel Fiyat (Spot)": "row_spot",
    "Historical Volatility": "row_hist_vol",
    "Opsiyon Fiyatı (şoksuz)": "row_option_price",
    "--- Risk Parametreleri ---": "row_risk_params_header",
    "Price Scan Range (PSR)": "row_psr",
    "Volatility Scan Range (VSR)": "row_vsr",
    "Extreme Move Multiplier": "row_emm",
    "Extreme Move Covered Fraction": "row_emcf",
    "Intra-Commodity Spread Charge (Takasbank referans)": "row_icsc_ref",
    "--- SONUÇLAR ---": "row_results_header",
    "Scanning Risk (TL)": "row_scan_risk",
    "+ Intra-Commodity Spread": "row_icsc_plus",
    "+ Delivery Risk": "row_delivery_plus",
    "Toplam (SOMsuz)": "row_total_no_som",
    "Short Option Minimum (SOM)": "row_som",
    "TOPLAM BAŞLANGIÇ TEMİNATI": "row_total_margin",
    "Aktif Senaryo #": "row_active_scenario",
}


def localize_scenario_table(df, lang: str, description_col: str = "Açıklama"):
    """16-senaryo tablosunun görüntülenecek KOPYASINI hem sütun adları hem
    de "Açıklama" hücre içerikleri çevrilmiş olarak döner (bkz.
    translate_scenario_description). lang == "tr" ise dokunmadan döner.
    df.attrs (ör. worst_scenario_no) pandas'ın kendi davranışıyla kopyaya
    da taşınır."""
    if lang == "tr":
        return df
    df = df.copy()
    if description_col in df.columns:
        df[description_col] = df[description_col].map(
            lambda text: translate_scenario_description(text, lang)
        )
    return translate_columns(df, lang)


def translate_columns(df, lang: str):
    """Bir DataFrame'in KOPYASINI, sütun adları çevrilmiş olarak döner.

    lang == "tr" ise (ya da sütun bilinen bir anahtarla eşleşmiyorsa)
    dokunmadan döner -- df.rename hiçbir zaman orijinali değiştirmez.
    """
    if lang == "tr":
        return df
    mapping = {
        col: t(key, lang) for col, key in SCENARIO_COLUMN_KEYS.items() if col in df.columns
    }
    return df.rename(columns=mapping)


def translate_field_column(df, lang: str, column: str = "Alan"):
    """Bir DataFrame'in KOPYASINI, `column` sütunundaki (satır etiketleri)
    Türkçe değerleri çevrilmiş olarak döner. lang == "tr" ise dokunmadan döner.
    """
    if lang == "tr" or column not in df.columns:
        return df
    df = df.copy()
    df[column] = df[column].map(lambda v: t(FIELD_ROW_KEYS.get(v, ""), lang) if v in FIELD_ROW_KEYS else v)
    return df


# _scenario_description/_futures_scenario_description'ın ÜRETTİĞİ metnin
# (bkz. BIST_Opsiyon.py ve vadeli işlem sayfası -- her ikisi de bu SABİT
# kelime dağarcığıyla çalışır) İngilizce'ye çevrilmesi için sıralı
# (en spesifikten en genele) alt-string değişimleri. Bu fonksiyona
# dokunmak yerine hesaplama katmanının kendisini değiştirmek gerekseydi
# tests/test_bist_opsiyon.py'deki sütun/testler bozulurdu -- bu yüzden
# çeviri SADECE görüntüleme anında, üretilmiş metnin ÜZERİNDE yapılır.
_SCENARIO_DESC_EN_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Aşırı hareket yukarı", "Extreme move up"),
    ("Aşırı hareket aşağı", "Extreme move down"),
    ("Fiyat sabit", "Price unchanged"),
    ("tam PSR", "full PSR"),
    ("Fiyat ", "Price "),
    ("Vol yukarı", "Vol up"),
    ("Vol aşağı", "Vol down"),
)
_PERCENT_PREFIX_RE = re.compile(r"%(\d[\d.]*)")


def translate_scenario_description(text: str, lang: str) -> str:
    """"Fiyat +1/3 PSR / Vol yukarı" tarzı, _scenario_description'ın
    ürettiği SABİT kalıplı metni İngilizce'ye çevirir. lang == "tr" ise
    dokunmadan döner."""
    if lang == "tr":
        return text
    for tr_part, en_part in _SCENARIO_DESC_EN_REPLACEMENTS:
        text = text.replace(tr_part, en_part)
    # Türkçe yüzde gösterimi sayının ÖNÜNE gelir ("%32"); İngilizce'de
    # sayının ARDINDAN gelir ("32%") -- bu ikisi farklı okunur, düz
    # çeviri değil gerçek bir gösterim farkı.
    return _PERCENT_PREFIX_RE.sub(r"\1%", text)
