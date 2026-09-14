"""Streamlit'in statik index.html'ine gerçek (JS'siz de okunabilen) <title>
ve SEO meta etiketleri enjekte eder.

NEDEN GEREKLİ: Streamlit bir SPA -- kendi paketindeki index.html'in
<title>'ı hep sabit "Streamlit" ve hiç <meta name="description"> içermez;
gerçek başlık/açıklama SADECE JS çalıştıktan SONRA (st.set_page_config +
BIST_Opsiyon.py'deki components.html enjeksiyonu) tarayıcıya yazılır. Bu,
Googlebot gibi JS çalıştıran tarayıcılar için yeterli ama Safari'nin adres
çubuğu önizlemesi, WhatsApp/Twitter link kartları gibi SADECE STATİK
HTML okuyan istemciler için YETERSİZ -- onlar hep "Streamlit" / "You need
to enable JavaScript to run this app." görüyordu (bkz. proje sohbet
geçmişi). Bu script, deploy'da (Procfile) sunucu başlamadan HEMEN ÖNCE
çalışıp bu paket dosyasını -- Railway'de her build'de sıfırdan kurulduğu
için -- kalıcı olarak yamalar.

Kapsam notu: Streamlit TEK bir statik index.html sunuyor (SPA), yani bu
başlık/açıklama SİTE GENELİ (hangi sayfada olursa olsun aynı) -- sayfaya
özel değil. Ana (Opsiyon) sayfasının başlığı/açıklaması kullanılıyor.

İdempotent: script birden fazla kez çalıştırılırsa (ör. yeniden deploy)
ikinci çalıştırmada hiçbir şey değişmez -- zaten yamalı dosyayı tekrar
yamalamaz.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit

TITLE = "VİOP SPAN Teminat Hesaplama — BIST Opsiyon Marjin Hesaplayıcı"
DESCRIPTION = (
    "VİOP opsiyonları ve vadeli işlemleri için Takasbank SPAN metodolojisiyle "
    "minimum başlangıç teminatını (marjin) anında hesaplayın. Güncel Takasbank "
    "verileriyle call, put ve vadeli işlem teminat tutarlarına bu sayfadan "
    "ulaşabilirsiniz."
)
SITE_URL = "https://viopteminat.com"

_MARKER = "<!-- viopteminat-static-seo-patch -->"


def _build_head_snippet() -> str:
    return (
        f"{_MARKER}\n"
        f'    <meta name="description" content="{DESCRIPTION}">\n'
        f'    <meta property="og:title" content="{TITLE}">\n'
        f'    <meta property="og:description" content="{DESCRIPTION}">\n'
        f'    <meta property="og:type" content="website">\n'
        f'    <meta property="og:url" content="{SITE_URL}">\n'
        f'    <meta name="twitter:card" content="summary">\n'
        f'    <meta name="twitter:title" content="{TITLE}">\n'
        f'    <meta name="twitter:description" content="{DESCRIPTION}">\n'
    )


def patch(index_path: Path) -> bool:
    """index.html'i yamalar. Zaten yamalıysa (marker varsa) dokunmaz.

    Returns:
        True: yeni yama uygulandı. False: zaten yamalıydı (no-op).
    """
    html = index_path.read_text(encoding="utf-8")
    if _MARKER in html:
        return False

    if "<title>Streamlit</title>" in html:
        html = html.replace("<title>Streamlit</title>", f"<title>{TITLE}</title>", 1)
    else:
        # Streamlit sürüm güncellemesinde <title> metni değişmiş olabilir --
        # sessizce atlamak yerine (başlık hiç değişmez) en azından meta
        # etiketlerini ekleyelim, </head> araması aşağıda zaten var.
        pass

    html = html.replace("</head>", _build_head_snippet() + "  </head>", 1)
    index_path.write_text(html, encoding="utf-8")
    return True


def main() -> None:
    static_dir = Path(os.path.dirname(streamlit.__file__)) / "static"
    index_path = static_dir / "index.html"
    if not index_path.exists():
        print(f"[patch_static_index] UYARI: {index_path} bulunamadı, atlanıyor.")
        return
    changed = patch(index_path)
    print(
        f"[patch_static_index] {'yama uygulandı' if changed else 'zaten yamalı, atlandı'} "
        f"-- {index_path}"
    )


if __name__ == "__main__":
    main()
