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


_ORIGINAL_TITLE = "<title>Streamlit</title>"
_ORIGINAL_NOSCRIPT = "<noscript>You need to enable JavaScript to run this app.</noscript>"
_ORIGINAL_LANG = '<html lang="en">'

# Google, sayfada meta description bulunmadığı ya da onu yetersiz bulduğu
# durumda snippet'i sayfanın GÖRÜNÜR metninden (JS'siz ilk geçişte: <noscript>)
# çıkarabiliyor -- ekran görüntüsündeki "Streamlit You need to enable
# JavaScript to run this app." tam olarak buydu. Bu yüzden <noscript>'in
# içine de gerçek açıklamayı koyuyoruz (JS gerektiği bilgisi sonda kalıyor).
_NOSCRIPT_REPLACEMENT = (
    f"<noscript><h1>{TITLE}</h1><p>{DESCRIPTION}</p>"
    "<p>Uygulamayı kullanmak için JavaScript gereklidir.</p></noscript>"
)


def patch(index_path: Path) -> bool:
    """index.html'i yamalar. Her değişiklik KENDİ BAŞINA idempotent -- ör.
    önceki bir deploy'da sadece <title>+meta yamalandıysa, bu sürüm
    <noscript>/lang eksiklerini de tamamlar.

    Returns:
        True: en az bir yeni değişiklik uygulandı. False: hepsi zaten yamalıydı.
    """
    html = index_path.read_text(encoding="utf-8")
    original = html

    # Streamlit sürüm güncellemesinde bu metinler değişmiş olabilir --
    # eşleşmezse o adım sessizce atlanır (site yine de çalışır, sadece o
    # tek etiket yamalanmaz).
    html = html.replace(_ORIGINAL_TITLE, f"<title>{TITLE}</title>", 1)
    html = html.replace(_ORIGINAL_NOSCRIPT, _NOSCRIPT_REPLACEMENT, 1)
    html = html.replace(_ORIGINAL_LANG, '<html lang="tr">', 1)

    if _MARKER not in html:
        html = html.replace("</head>", _build_head_snippet() + "  </head>", 1)

    if html == original:
        return False
    index_path.write_text(html, encoding="utf-8")
    return True


def main() -> None:
    # Procfile: `python scripts/patch_static_index.py && streamlit run ...` --
    # bu script HATA verirse (izin, beklenmeyen dosya düzeni vb.) `&&`
    # yüzünden site HİÇ BAŞLAMAZ. SEO yaması bir "olsa iyi olur" -- site
    # ayakta kalması çok daha önemli, bu yüzden her hatayı yutup 0 ile çıkıyoruz.
    try:
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
    except Exception as exc:  # noqa: BLE001 -- bilerek geniş, bkz. yukarısı
        print(f"[patch_static_index] UYARI: yama uygulanamadı ({exc!r}), site yine de başlatılıyor.")


if __name__ == "__main__":
    main()
