"""A Tailwind colour class the theme does not define generates no CSS.

The Restart Now button used `text-bg-base`. The theme colour is `bg`, so the text
inherited `text-warning` from its banner: amber on amber, a button with no
visible label. Found live on 2026-11-01.
"""
import glob
import os
import re

_WEB = os.path.join(os.path.dirname(__file__), "..", "web")


def _sources():
    pattern = os.path.join(_WEB, "src", "**", "*.svelte")
    for path in glob.glob(pattern, recursive=True):
        with open(path, encoding="utf-8") as f:
            yield path, f.read()


def test_no_source_uses_a_bg_base_colour():
    bad = [p for p, text in _sources()
           if re.search(r"\b(text|bg|border)-bg-base\b", text)]
    assert bad == []


def test_the_built_css_has_the_class_the_buttons_use():
    css = "".join(open(p, encoding="utf-8").read()
                  for p in glob.glob(os.path.join(_WEB, "dist", "assets", "*.css")))
    assert ".text-bg{" in css
