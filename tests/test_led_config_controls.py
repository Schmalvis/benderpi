"""The LED controls on the Config page must reach the LEDs.

Found 2026-11-01. The colour boxes wrote `led_listening_color` and
`led_talking_color`, US spellings nothing reads. The brightness slider ran 0-255
into a driver that clamps to 0-1, so every value from 1 to 255 meant full. Each
side's own tests passed, so these read the keys out of the frontend source and
put its output through the real schema and the real config loader.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from pydantic import ValidationError  # noqa: E402
from config import Config  # noqa: E402
from web.routes.config_schema import clean_bender_config  # noqa: E402

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_PAGE = os.path.join(_ROOT, "web", "src", "pages", "Config.svelte")
_HELPER = os.path.join(_ROOT, "web", "src", "lib", "ledColour.js")
COLOUR_KEYS = ("led_listening_colour", "led_talking_colour")


def _page():
    with open(_PAGE, encoding="utf-8") as f:
        return f.read()


class TestColourKeys:
    def test_the_page_uses_the_keys_config_reads(self):
        page = _page()
        for key in COLOUR_KEYS:
            assert key in page
            assert hasattr(Config(config_path="/nonexistent"), key)

    def test_the_page_has_lost_the_us_spellings(self):
        assert not re.search(r"led_(listening|talking)_color\b", _page())


class TestColourSchema:
    @pytest.mark.parametrize("key", COLOUR_KEYS + ("led_colour",))
    def test_a_colour_is_three_bytes(self, key):
        assert clean_bender_config({key: [255, 0, 128]})[key] == [255, 0, 128]

    @pytest.mark.parametrize("bad", [
        "#ffffff", [255, 255], [255, 255, 255, 255], [256, 0, 0], [-1, 0, 0],
        None,
    ])
    @pytest.mark.parametrize("key", COLOUR_KEYS + ("led_colour",))
    def test_anything_else_is_refused(self, key, bad):
        # None too: _override_type_ok lets a null through by design, and
        # pixels.fill(None) would then raise inside the listening path.
        with pytest.raises(ValidationError):
            clean_bender_config({key: bad})

    def test_the_shipped_config_passes(self):
        with open(os.path.join(_ROOT, "bender_config.json")) as f:
            shipped = json.load(f)
        for key in COLOUR_KEYS:
            assert clean_bender_config({key: shipped[key]})[key] == shipped[key]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestColourConversion:
    @staticmethod
    def _run(expression):
        helper = os.path.abspath(_HELPER)
        script = (f"import {{ hexToRgb, rgbToHex }} from {json.dumps(helper)};"
                  f"console.log(JSON.stringify({expression}));")
        out = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True, text=True, timeout=20, check=True,
        )
        return json.loads(out.stdout)

    def test_a_page_colour_reaches_the_config_as_a_tuple(self, tmp_path):
        rgb = self._run("hexToRgb('#ff8000')")
        saved = clean_bender_config({"led_listening_colour": rgb})
        path = tmp_path / "bender_config.json"
        path.write_text(json.dumps(saved))
        assert Config(config_path=str(path)).led_listening_colour == (255, 128, 0)

    def test_a_config_colour_shows_as_hex(self):
        assert self._run("rgbToHex([0, 80, 255], '#ffffff')") == "#0050ff"

    def test_bad_text_gives_null(self):
        assert self._run("['ffffff', '#fff', '#gggggg', ''].map(hexToRgb)") == [
            None, None, None, None]

    def test_a_missing_or_bad_value_shows_the_fallback(self):
        assert self._run(
            "[undefined, null, 'x', [1, 2], [999, 0, 0]]"
            ".map(v => rgbToHex(v, '#ffffff'))"
        ) == ["#ffffff"] * 5


class TestBrightness:
    def test_the_slider_runs_on_the_scale_the_driver_reads(self):
        tag = re.search(r'id="led_brightness".*?/>', _page(), re.S).group(0)
        assert 'max="1"' in tag
        assert 'step="0.05"' in tag

    @pytest.mark.parametrize("saved,want", [
        (0, 0.0), (0.5, 0.5), (1, 1.0), (1.0, 1.0),
        (128, 128 / 255), (255, 1.0), (300, 1.0), (-1, 0.0),
    ])
    def test_old_0_255_saves_still_scale(self, saved, want):
        import leds
        assert leds.brightness_unit(saved) == pytest.approx(want)

    def test_a_null_brightness_uses_the_code_default(self):
        # _override_type_ok accepts a null for every key.
        import leds
        from config import Config
        assert leds.brightness_unit(None) == (
            Config(config_path="/nonexistent").led_brightness)


class TestBrightnessRoute:
    def test_the_dead_route_is_gone(self):
        # It had no caller and raised AttributeError on every request. A setter
        # here would change only bender-web's strip object, not bender-converse's.
        os.environ["BENDER_WEB_PIN"] = "testpin"
        from web.app import app
        paths = {getattr(r, "path", "") for r in app.routes}
        assert "/api/config/led-brightness" not in paths
