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
│       └── main.py            # CLI / Streamlit arayüzü
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

4. **Kullanıcı Arayüzü** (`main.py`)
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
streamlit run src/bist_span/main.py
```

Akış: hisse + vade gir → **"Verileri Çek"** → güncel fiyat/volatilite ve
Takasbank risk parametreleri otomatik gelir, her biri ayrı bir "Override"
checkbox'ıyla manuel değere çevrilebilir → strike gir (varsayılan: güncel
fiyat) → **"Hesapla"** → call ve put için minimum teminat yan yana.

## Yol Haritası

- [x] `data_fetch.py`: fiyat çekme + volatilite + günlük cache
- [x] `risk_params.py`: Takasbank PDF parser — iki farklı gerçek mektupla
      (farklı bölüm numaralandırmaları dahil) doğrulandı, bkz.
      `tests/fixtures/` ve modül docstring'i
- [x] `span_engine.py`: Black-Scholes + 16 senaryo + Scan Risk + SOM + final formül
- [x] `main.py`: CLI akışı + Streamlit dashboard
- [ ] (opsiyonel) Inter-Commodity Spread Credit ve Physical Delivery Margin
      parse'ı (şu an `calculate_span_margin`'e manuel/varsayılan 0 geçiliyor)
