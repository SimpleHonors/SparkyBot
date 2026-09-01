import json

from core.night_model import build_night_model
from core.report_viewer import (
    REPORT_VIEWS,
    build_switchable_report,
    convert_report_file,
    unpack_classic_report,
)
from core.report_pack import pack_html


def _tiddlers():
    return [
        {
            "title": "2026-08-30-20:00:00-Tag_Stats",
            "caption": "Duke",
            "text": (
                "|!Name|!Fights|!DownedEnemy|!killed|!DownedAlly|!DeadAlly|!KDR|h\n"
                "|Totals|4|80|55|10|5|11.0|"
            ),
        },
        {
            "title": "2026-08-30-20:00:00-Overview",
            "text": (
                "|!#|!Time|!Duration|!Squad|!Allies|!Enemy|!DownedEnemy|!killed|h\n"
                "|1|20:00|5m 0s|40|40|45|20|12|"
            ),
        },
    ]


def test_switchable_report_carries_three_views_and_one_classic_payload():
    classic = (
        "<!doctype html><html><head><title>Night &amp; Logs</title></head>"
        "<body><h1>Classic sentinel</h1></body></html>"
    )
    model = build_night_model(_tiddlers())

    report = build_switchable_report(classic, model, default_view="sparky")

    assert REPORT_VIEWS == ("sparky", "simple", "classic")
    assert report.count('id="classic-payload"') == 1
    assert all(f'data-view="{view}"' in report for view in REPORT_VIEWS)
    assert '"defaultView":"sparky"' in report
    assert "Sparky" in report and "Simple" in report and "Classic" in report
    assert unpack_classic_report(report) == classic
    assert "https://" not in report and "http://" not in report


def test_switchable_report_embeds_skin_ready_model_without_script_breakout():
    classic = "<html><title>Report</title><body>classic</body></html>"
    model = build_night_model(_tiddlers())
    model["warnings"].append("</script><script>bad()</script> __PAYLOAD__")

    report = build_switchable_report(classic, model, default_view="simple")

    assert '"defaultView":"simple"' in report
    assert "</script><script>bad()" not in report
    assert "__PAYLOAD__" in report
    assert json.dumps(model["totals"], separators=(",", ":")) in report


def test_invalid_default_view_falls_back_to_sparky():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
        default_view="definitely-not-a-view",
    )

    assert '"defaultView":"sparky"' in report


def test_convert_report_carries_selected_log_count_from_filename(tmp_path):
    path = tmp_path / "Raid Report 2026-08-31 (31 fights).html"
    path.write_text("<html><title>Report</title></html>", encoding="utf-8")

    convert_report_file(path, _tiddlers())

    report = path.read_text(encoding="utf-8")
    assert '"selected_fights":31' in report
    assert "logs selected" in report
    assert "modeled encounters" in report
    assert "unmodeled / excluded" in report
    assert "Modeled fights" in report
    assert "Combat time" in report
    assert "confidenceLabel" in report
    assert "roleDisplay(role)" in report


def test_pro_viewer_exposes_navigation_subviews_and_persistent_themes():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'data-view="sparky">Pro<' in report
    for label in (
        "Overview",
        "DPS",
        "Support",
        "Healing",
        "High Scores",
        "Enemy Intel",
        "Details / Fights",
        "Night Summary",
        "Fight Timeline",
        "Boon Strips & Crowd Control",
    ):
        assert label in report
    assert 'value="graphite">Graphite<' in report
    assert 'value="midnight">Midnight<' in report
    assert 'value="studio-light">Studio Light<' in report
    assert "sparkybot-report-theme" in report
    assert "prefers-reduced-motion" in report
    assert "allSourceBoards()" in report
    assert "leaderboards</span>" in report
    assert "stat tables</span>" in report


def test_enemy_intel_is_comparison_only_for_all_and_labels_estimates():
    model = build_night_model(_tiddlers())
    model["enemy_intel"] = {
        "coverage": {
            "selected_fights": 31,
            "reported_fights": 23,
            "composition_snapshots": 30,
            "colors": ["Green", "Red"],
        },
        "scopes": [
            {
                "id": "green",
                "label": "Green",
                "color": "Green",
                "fight_indexes": [1],
                "aggregate": {"professions": []},
                "groups": [],
            }
        ],
        "fights": [
            {
                "index": 1,
                "color": "Green",
                "enemy_count": 5,
                "estimated_subgroups": [
                    {
                        "party": 1,
                        "members": [
                            {
                                "profession": "Firebrand",
                                "role": "Support",
                                "evidence": "inferred",
                            }
                        ],
                    }
                ],
            }
        ],
        "all": {"mode": "comparison_only", "estimated_subgroups": None},
        "session_pressure": {},
        "ai_analysis": None,
    }

    report = build_switchable_report(
        "<html><title>Report</title></html>", model
    )

    assert "All opponents is comparison-only" in report
    assert "never blended into one fake Subgroup grid" in report
    assert "Estimated Enemy Squad Composition" in report
    assert "Observed profession" in report
    assert "Inferred placement" in report
    assert "Unknown" in report
    assert "AI Enemy Read" in report
    assert "fetch(" not in report


def test_poison_evidence_is_available_only_in_dps_conditions():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "poisonSpotlight" not in report
    assert "poisonContext() + poisonTable()" in report
    assert "Poison evidence" in report
    assert "Demon Queen" in report
    assert "cannot attribute individual applications" in report


def test_pro_overview_does_not_promote_poison_as_a_signature_metric():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    # Poison remains available under DPS > Conditions, but it is not a hero card.
    assert 'subpanel("dps", "conditions", poisonSpotlight()' not in report
    assert 'subpanel("overview", "summary", enemyCoverage() + poisonSpotlight()' not in report
    assert "Condition Damage" in report
    assert "poisonTable()" in report


def test_enemy_parties_render_as_five_tactical_profession_slots():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function professionGlyph" in report
    assert "function roleClass" in report
    assert "while (slots.length < 5)" in report
    assert "party-slots" in report
    assert "profession-glyph" in report
    assert "role-badge role-" in report
    assert "Subgroup " in report
    assert "--guardian:" in report
    assert "--necromancer:" in report
    assert "--ranger:" in report


def test_pro_has_persisted_near_black_theme_and_semantic_color_pop():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'value="blackout">Blackout<' in report
    assert 'data-theme="blackout"' in report
    assert "--bg:#020305" in report
    assert "--role-dps:" in report
    assert "--role-heal:" in report
    assert "--role-support:" in report
    assert 'localStorage.setItem("sparkybot-report-theme", theme)' in report
    assert 'localStorage.getItem("sparkybot-report-theme")' in report


def test_pro_items_offer_offline_accessible_drilldown_dialog():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'id=\\"drilldown\\"' in report
    assert 'aria-labelledby=\\"drill-title\\"' in report
    assert "function openDrilldown" in report
    assert 'closest("[data-drill]")' in report
    assert 'event.key === "Escape"' in report
    assert 'drillAttrs("party-slot"' in report
    assert 'drillAttrs("leaderboard-row"' in report
    assert 'drillAttrs("pressure"' in report
    assert "No network call is made" in report


def test_enemy_pressure_fallbacks_are_plain_language_not_unknown_walls():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function conditionProfile" in report
    assert "function stripProfile" in report
    assert "No per-color breakdown" in report
    assert "Source total not exported" in report
    assert "reported or inferred" in report


def test_pro_visuals_are_colorful_offline_and_share_drilldown_evidence():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function outcomeChart" in report
    assert "function metricBars" in report
    assert "function conditionHeatmap" in report
    assert "function pressureBars" in report
    assert "Kills" in report and "Enemy downs" in report
    assert "Our downs" in report and "Our deaths" in report
    assert 'drillAttrs("chart-bar"' in report
    assert 'drillAttrs("heatmap-cell"' in report
    assert "--series-kills:" in report
    assert "--series-deaths:" in report


def test_enemy_slot_prefers_embedded_exact_profession_icon_with_glyph_fallback():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "model.profession_icons" in report
    assert "data:image/png;base64," in report
    assert "profession-icon" in report
    assert "professionGlyph(profession)" in report


def test_category_tables_default_to_top_five_expand_and_sort_real_metrics():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "var initialLimit = 5" in report
    assert "Expand all" in report and "Collapse" in report
    assert "data-expand-board" in report
    assert "data-sort-key" in report
    assert "participation_weighted_rate" in report
    assert "value_per_minute" in report
    assert 'board.value_label || titleCase(board.stat)' in report
    assert 'esc(board.value_label || "Score")' not in report


def test_pro_separates_tonight_stats_from_history_and_surfaces_night_mvps():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "return (model.stat_tables || [])" in report
    assert "function longTermLeaderboardGrid" in report
    assert "Long-term Leaderboards" in report
    assert "not tonight’s performance totals" in report
    assert "function nightMvpCards" in report
    for category in (
        "damage",
        "healing",
        "resurrection",
        "condition_cleanses",
        "boon_strips",
        "stability",
        "crowd_control",
        "fight_impact",
    ):
        assert f'"{category}"' in report
    assert "no invented universal score" in report
    assert 'metricBars(["boon","quickness","stability","alacrity"]' in report


def test_convert_file_unpacks_upstream_loader_and_replaces_it_atomically(tmp_path):
    classic = "<html><head><title>Night</title></head><body>sentinel</body></html>"
    path = tmp_path / "night.html"
    path.write_text(pack_html(classic), encoding="utf-8")

    returned = convert_report_file(path, _tiddlers(), default_view="simple")
    switched = path.read_text(encoding="utf-8")

    assert returned == path
    assert 'data-sparkybot-report-viewer="1"' in switched
    assert '"defaultView":"simple"' in switched
    assert unpack_classic_report(switched) == classic
    assert not list(tmp_path.glob("*.part"))
