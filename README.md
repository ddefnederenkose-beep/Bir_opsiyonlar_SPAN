# BIST SPAN Margin Calculator

Türk hisse senedi opsiyonları (BIST) için SPAN metodolojisiyle minimum
başlangıç teminatı (initial margin) hesaplayan Python projesi.

> **Durum:** 4 katman da dolduruldu ve gerçek Takasbank risk parametre
> PDF'leriyle uçtan uca test edildi. 31/31 test yeşil.

## Proje Yapısı

```
bist-span-margin/
├── requirements.txt
├── src/
│   └── bist_span/
│       ├── __init__.py
│       ├── data_fetch.py      # Fiyat/volatilite çekme + cache
│       ├── risk_params.py     # Takasbank SPAN risk parametreleri
│       ├── span_engine.py     # SPAN hesaplama motoru
│       └── BIST_Opsiyon.py    # CLI / Streamlit arayüzü
├── tests/
│   ├── test_data_fetch.py
│   ├── test_risk_params.py
│   └── test_span_engine.py
└── cache/                     # Günlük fiyat cache (git'e girmez)
```

## Katmanlar

1. **Veri Çekme Katmanı** (`data_fetch.py`)
   - `yfinance` ile güncel fiyat ve geçmiş fiyat serisi
   - Historical volatility: log return std dev × √252
   - Günlük dosya tabanlı cache

2. **Risk Parametre Katmanı** (`risk_params.py`)
   - Takasbank SPAN risk parametre dosyalarını parse etme
     (PSR, VSR, Extreme Move Multiplier, Extreme Move Covered
     Fraction, Intra-Commodity Spread Charge, Short Option Minimum)
   - Hisse bazında parametre deposu

3. **SPAN Hesaplama Motoru** (`span_engine.py`)
   - Black-Scholes call/put fiyatlama
   - 16 risk senaryosu üretimi
   - Scanning Risk ve Short Option Minimum hesaplama
   - Final formül:
     `Total Initial Margin = max(SOM, Scan Risk + Intra-Commodity
     Spread Charge + Delivery Risk - Inter-Commodity Spread Credit)`

4. **Kullanıcı Arayüzü** (`BIST_Opsiyon.py`)
   - Firma + strike + vade gir; sistem 1 kısa kontrat üzerinden hem
     call hem put için minimum SPAN teminatını hesaplar
   - Otomatik çekilen her bileşen (spot, volatilite, PSR, VSR, Extreme
     Move Multiplier/Covered Fraction, Intra-Commodity Spread Charge,
     SOM) tek tek manuel override edilebilir
   - Hem CLI hem Streamlit dashboard aynı çekirdeği (`compute_span_result`,
     `compute_call_and_put`) kullanır

## Kurulum

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Test

```bash
pytest                       # network testi dahil tüm testler
pytest -m "not network"      # yfinance'e gerçek ağ çağrısı yapan testi hariç tutar
```

## Kullanım

### CLI

```bash
# Hem call hem put (varsayılan, --option-type verilmezse):
python -m bist_span.main --ticker AKBNK --strike 65 --expiry 2026-09-20

# Tek taraf + manuel override örneği:
python -m bist_span.main --ticker AKBNK --strike 65 --option-type call \
    --expiry 2026-09-20 --volatility-scan-range 0.40 --short-option-minimum 2000
```

- `--contracts` verilmezse -1 (1 kısa/yazılmış kontrat) varsayılır.
- `--risk-params-file` verilmezse projedeki en güncel Takasbank fixture'ı
  (`tests/fixtures/takasbank_span_sample_2.pdf`) kullanılır; bir PDF ya da
  `RiskParamsStore.save` ile kaydedilmiş bir JSON cache verilebilir.
- Her risk parametresi ayrı bir flag ile override edilebilir:
  `--spot`, `--volatility`, `--price-scan-range`, `--volatility-scan-range`,
  `--extreme-move-multiplier`, `--extreme-move-covered-fraction`,
  `--intra-commodity-spread-charge`, `--short-option-minimum`.

### Streamlit Dashboard

```bash
streamlit run src/bist_span/BIST_Opsiyon.py
```

Akış: hisse + vade gir → **"Verileri Çek"** → güncel fiyat/volatilite ve
Takasbank risk parametreleri otomatik gelir, her biri ayrı bir "Override"
checkbox'ıyla manuel değere çevrilebilir → strike gir (varsayılan: güncel
fiyat) → **"Hesapla"** → call ve put için minimum teminat yan yana.

## Canlı Yayın ve Kendi Domain'inizle Barındırma

Şu an proje, `main` dalına her push'ta otomatik yeniden derlenen
**Streamlit Community Cloud**'da ücretsiz yayında (`*.streamlit.app`
adresinde). Bunun iki kısıtı var: (1) uzun süre ziyaretçi olmazsa
"uykuya" geçiyor, (2) özel domain bağlama desteği yok/kısıtlı.

Kendi domain'inizle, her zaman açık bir şekilde yayınlamak isterseniz
(ör. **Railway** — kolay kurulum, aylık birkaç dolar, özel domain
desteği var; alternatif olarak Render/Fly.io/bir VPS de olur), proje
buna hazır (`Procfile`, `.streamlit/config.toml` zaten ekli). Adımlar:

1. **Domain satın alın** (ör. Namecheap, GoDaddy, ya da bir `.tr`
   kayıtçısı — Natro/Turhost) — genelde yıllık birkaç dolar/TL.
2. **Railway'e kaydolun** (railway.app), "New Project" → "Deploy from
   GitHub repo" ile bu depoyu (`ddefnederenkose-beep/Bir_opsiyonlar_SPAN`)
   bağlayın. Railway, `Procfile`'ı otomatik bulup
   `streamlit run src/bist_span/BIST_Opsiyon.py --server.port $PORT ...`
   komutuyla başlatacaktır.
3. Railway'in proje ayarlarında **"Settings" → "Networking" → "Custom
   Domain"** kısmından domain'inizi ekleyin; size bir CNAME hedefi
   verecek (ör. `xxxx.up.railway.app`).
4. Domain'i satın aldığınız yerin **DNS panelinden**, o CNAME kaydını
   ekleyin (ör. `www` alt alan adı için). Railway, DNS doğrulandıktan
   sonra otomatik HTTPS (SSL) sertifikası sağlar.
5. Streamlit Community Cloud'daki eski link (`*.streamlit.app`) isterseniz
   test/yedek olarak kalmaya devam edebilir — ikisi birbirinden bağımsız
   çalışır, aynı GitHub deposunu paylaşırlar.

**SEO notu:** "margin calculator" gibi geniş İngilizce terimlerde üst
sıraya çıkmak (Investopedia, broker'lar vb. köklü sitelerle rekabet
yüzünden) gerçekçi değil. Türkçe, niş terimlerde (ör. "VİOP teminat
hesaplama", "SPAN teminat hesaplayıcı") çok daha az rekabet var —
sayfa başlığı/açıklaması bu terimleri içerecek şekilde ayarlanıp
[Google Search Console](https://search.google.com/search-console)'a
site eklenmesi, gerçek görünürlük için domain'den daha etkili bir
ilk adım.

## Yol Haritası

- [x] `data_fetch.py`: fiyat çekme + volatilite + günlük cache
- [x] `risk_params.py`: Takasbank PDF parser — iki farklı gerçek mektupla
      (farklı bölüm numaralandırmaları dahil) doğrulandı, bkz.
      `tests/fixtures/` ve modül docstring'i
- [x] `span_engine.py`: Black-Scholes + 16 senaryo + Scan Risk + SOM + final formül
- [x] `BIST_Opsiyon.py`: CLI akışı + Streamlit dashboard
- [ ] (opsiyonel) Inter-Commodity Spread Credit ve Physical Delivery Margin
      parse'ı (şu an `calculate_span_margin`'e manuel/varsayılan 0 geçiliyor)
