"""risk_params.py + span_engine.py uçtan uca entegrasyon testi.

Gerçek Takasbank fixture'ından parse edilen AKBNK risk parametreleriyle
somut bir opsiyon pozisyonu için tam SPAN hesabı yapar. Bu test,
katmanların birbiriyle doğru şekilde konuştuğunu ve sonucun makul bir
büyüklükte olduğunu doğrular (span_engine'in kendi testleri saf mantığı,
bu test ise gerçek veriyle bütünü doğrular).
"""

from __future__ import annotations

from pathlib import Path

from bist_span.risk_params import RiskParamsStore, parse_takasbank_span_file
from bist_span.span_engine import OptionPosition, calculate_span_margin

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "takasbank_span_sample.pdf"


def test_end_to_end_span_margin_for_akbnk_short_call():
    """AKBNK üzerine yazılan 10 kontratlık kısa call için tam SPAN hesabı."""
    store = RiskParamsStore()
    store.load_from_dataframe(parse_takasbank_span_file(FIXTURE_PATH))
    risk_params = store.get("AKBNK.IS")

    position = OptionPosition(
        ticker="AKBNK",
        strike=65,
        option_type="call",
        contracts=-10,  # 10 kontrat kısa (yazılmış)
        time_to_expiry=30 / 365,
        risk_free_rate=0.45,
    )

    result = calculate_span_margin(
        position=position,
        spot=62.0,
        volatility=0.35,
        risk_params=risk_params,
    )

    # SOM tek başına: 10 kontrat * risk_params.short_option_minimum
    assert result["short_option_minimum"] == 10 * risk_params.short_option_minimum
    # Final teminat her zaman SOM'dan büyük ya da eşit olmalı.
    assert result["total_initial_margin"] >= result["short_option_minimum"]
    # Makul bir büyüklükte olmalı: 10 kontrat * spot'un çok üzerinde
    # olmamalı (aşırı büyütülmüş bir hata varsa yakalar).
    assert result["total_initial_margin"] < 10 * position.strike * 100
    # Ara adımlar dict'te eksiksiz olmalı.
    assert set(result) == {
        "scan_risk",
        "intra_commodity_spread_charge",
        "delivery_risk",
        "inter_commodity_spread_credit",
        "short_option_minimum",
        "total_initial_margin",
    }
