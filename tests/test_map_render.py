"""Tests for HTML generation in map.py (render_html / _build_legend)."""
import json
import re

import pytest

from conftest import geo_entry


def _render(map_env, by_tag, live=False):
    all_tags = sorted(by_tag)
    colors = {t: map_env.TAG_COLORS[i % len(map_env.TAG_COLORS)] for i, t in enumerate(all_tags)}
    return map_env.render_html(by_tag, all_tags, colors, live=live)


def test_render_html_exits_when_no_entries(map_env):
    with pytest.raises(SystemExit) as exc:
        _render(map_env, {"Alpha": []})
    assert exc.value.code == 0


def test_render_html_basic_structure(map_env):
    html = _render(map_env, {"Alpha": [geo_entry("Alpha")]})
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert html.count("<script") == html.count("</script>")
    assert "<div id='map'></div>" in html


def test_render_html_centers_on_mean_of_coordinates(map_env):
    by_tag = {"Alpha": [geo_entry("Alpha", lat=44.0, lon=11.0),
                        geo_entry("Alpha", lat=46.0, lon=13.0, minutes_ago=20)]}
    html = _render(map_env, by_tag)
    assert "L.map('map',{center:[45.0,12.0]" in html


def test_render_html_embeds_all_entries_in_raw24h(map_env):
    entries = [geo_entry("Alpha"), geo_entry("Alpha", minutes_ago=20), geo_entry("Bravo")]
    by_tag = {"Alpha": entries[:2], "Bravo": entries[2:]}
    html = _render(map_env, by_tag)
    payload = re.search(r"var _raw24h=(\[.*?\]);\n", html, re.S).group(1)
    assert len(json.loads(payload)) == 3


def test_render_html_creates_one_feature_group_per_tag(map_env):
    by_tag = {"Alpha": [geo_entry("Alpha")], "Bravo": [geo_entry("Bravo")]}
    html = _render(map_env, by_tag)
    assert "var _fg0=L.featureGroup();" in html
    assert "var _fg1=L.featureGroup();" in html
    assert "var _fg2=" not in html


def test_render_html_tag_meta_has_color_letter_group(map_env):
    html = _render(map_env, {"Alpha": [geo_entry("Alpha")]})
    meta = json.loads(re.search(r"var _tagMeta=(\{.*?\});\n", html, re.S).group(1))
    assert meta["Alpha"] == {"color": "#facc15", "letter": "A", "group": "_fg0"}


def test_render_html_live_flag_adds_sse_client(map_env):
    by_tag = {"Alpha": [geo_entry("Alpha")]}
    assert "EventSource" in _render(map_env, by_tag, live=True)
    assert "EventSource" not in _render(map_env, by_tag, live=False)


def test_render_html_last_by_tag_uses_most_recent_fix(map_env):
    recent = geo_entry("Alpha", minutes_ago=5)
    older = geo_entry("Alpha", minutes_ago=90)
    html = _render(map_env, {"Alpha": [older, recent]})
    last = json.loads(re.search(r"var _lastByTag=(\{.*?\});\n", html, re.S).group(1))
    assert last["Alpha"] == recent["location_time"]


# --- Injection / escaping ---------------------------------------------------
# Tag names come from the Google Find Hub device list. For a tracker shared by
# another account the name is chosen by that account, so it is untrusted input.

def test_tag_name_cannot_break_out_of_html_attribute(map_env):
    evil = "x' onmouseover='alert(1)"
    letters = map_env.assign_letters([evil])
    legend = map_env._build_legend([evil], {evil: "#facc15"}, letters)
    # the quote that would end the attribute must be encoded
    assert "onmouseover='alert(1)" not in legend
    assert "&#x27;" in legend


def test_tag_name_cannot_close_the_script_block(map_env):
    evil = "</script><img src=x onerror=alert(1)>"
    html = _render(map_env, {evil: [geo_entry(evil)]})
    script_body = html.split("<script>\n")[-1]
    assert "</script><img" not in script_body


def test_semantic_name_is_not_rendered(map_env):
    """Semantic entries are filtered out by load_entries, so they never reach the HTML."""
    html = _render(map_env, {"Alpha": [geo_entry("Alpha")]})
    assert "semantic_name" not in html
