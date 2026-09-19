"""scripts/patch_static_index.py için testler.

Script bir paket üyesi değil (scripts/ altında), bu yüzden importlib ile
dosya yolundan yüklenir. Gerçek Streamlit index.html'ine DEĞİL, tmp_path'teki
küçük bir kopyaya karşı çalıştırılır.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "patch_static_index.py"


@pytest.fixture(scope="module")
def patcher():
    spec = importlib.util.spec_from_file_location("patch_static_index", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRISTINE_HTML = (
    '<!DOCTYPE html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n'
    "    <title>Streamlit</title>\n  </head>\n  <body>\n"
    "    <noscript>You need to enable JavaScript to run this app.</noscript>\n"
    '    <div id="root"></div>\n  </body>\n</html>\n'
)


def test_patch_replaces_title_noscript_lang_and_adds_meta(patcher, tmp_path):
    index = tmp_path / "index.html"
    index.write_text(PRISTINE_HTML, encoding="utf-8")

    assert patcher.patch(index) is True
    html = index.read_text(encoding="utf-8")

    assert "<title>Streamlit</title>" not in html
    assert f"<title>{patcher.TITLE}</title>" in html
    assert "You need to enable JavaScript to run this app." not in html
    assert patcher.DESCRIPTION in html.split("<noscript>")[1].split("</noscript>")[0]
    assert '<html lang="tr">' in html
    assert '<meta name="description"' in html
    assert 'property="og:title"' in html


def test_patch_is_idempotent(patcher, tmp_path):
    index = tmp_path / "index.html"
    index.write_text(PRISTINE_HTML, encoding="utf-8")

    assert patcher.patch(index) is True
    once = index.read_text(encoding="utf-8")
    assert patcher.patch(index) is False
    assert index.read_text(encoding="utf-8") == once
    assert once.count(patcher._MARKER) == 1


def test_patch_completes_a_file_patched_by_the_older_version(patcher, tmp_path):
    """Eski sürüm sadece <title> + meta yamalıyordu (noscript/lang değil) --
    yeni sürüm o yarım yamalı dosyayı tamamlamalı, meta'yı İKİLEMEMELİ."""
    index = tmp_path / "index.html"
    old_style = PRISTINE_HTML.replace(
        "<title>Streamlit</title>", f"<title>{patcher.TITLE}</title>"
    ).replace("</head>", patcher._build_head_snippet() + "  </head>")
    index.write_text(old_style, encoding="utf-8")

    assert patcher.patch(index) is True
    html = index.read_text(encoding="utf-8")
    assert '<html lang="tr">' in html
    assert "You need to enable JavaScript to run this app." not in html
    assert html.count(patcher._MARKER) == 1
    assert html.count('<meta name="description"') == 1


def test_patch_survives_unexpected_streamlit_markup(patcher, tmp_path):
    """Streamlit ileride index.html'i değiştirirse patch() patlamamalı."""
    index = tmp_path / "index.html"
    index.write_text("<html><head><title>Baska</title></head><body></body></html>", encoding="utf-8")
    patcher.patch(index)  # exception fırlatmamalı
    assert "<title>Baska</title>" in index.read_text(encoding="utf-8")


def test_main_never_raises_even_if_patching_fails(patcher, monkeypatch, capsys):
    """Procfile'da `&&` var -- main() hata fırlatırsa site hiç başlamaz."""

    def boom(_path):
        raise PermissionError("salt-okunur dosya sistemi")

    monkeypatch.setattr(patcher, "patch", boom)
    patcher.main()  # exception fırlatmamalı
    assert "yama uygulanamadı" in capsys.readouterr().out
