import json

from core.night_model import build_night_model
from core.report_viewer import (
    REPORT_VIEWS,
    build_switchable_report,
    convert_report_file,
    unpack_classic_report,
    unpack_night_model,
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


def test_switchable_report_compresses_and_round_trips_night_model():
    classic = "<html><title>Report</title></html>"
    model = build_night_model(_tiddlers())
    model["repeated_evidence"] = ["same detailed evidence"] * 10_000

    report = build_switchable_report(classic, model)

    assert report.count('id="night-model-payload"') == 1
    assert unpack_night_model(report) == model
    assert len(report) < len(json.dumps(model))


def test_simple_view_has_a_distinct_briefing_identity():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'class=\\\"simple-briefing\\\"' in report
    assert 'body class=\\\"simple-report\\\"' in report
    assert "Nightly briefing · essential results" in report
    assert "font:500 clamp(34px,5vw,58px)/1.02 Georgia" in report
    assert "counter-reset:brief-board" in report
    assert "Sparky Pro" in report


def test_role_display_does_not_rewrite_dps_support_as_boon_support():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'if (/dps\\s*\\/\\s*support/.test(value)) label="DPS / Support"' in report


def test_skill_damage_table_uses_exported_fields_and_defines_down_contribution():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'title=\\"Down-Contribution Damage\\">Down Dmg' not in report
    assert '>Down Contribution</th>' in report
    assert '>Hits</th>' in report
    assert '>Damage / Hit</th>' in report
    assert "Damage dealt from 90% health through the down" in report
    assert "Barrier damage by outgoing skill was not retained" in report


def test_switchable_report_reuses_embedded_profession_icons_offline():
    classic = (
        '<html><title>Report</title><body>'
        '{"title":"Firebrand_icon_small.png","text":"iVBORw0KGgo=",'
        '"type":"image/png"}</body></html>'
    )

    report = build_switchable_report(classic, build_night_model(_tiddlers()))

    assert unpack_night_model(report)["profession_icons"] == {
        "Firebrand": "iVBORw0KGgo="
    }
    assert "data:image/png;base64," in report


def test_pro_fight_table_only_links_posted_fight_reports():
    model = build_night_model(_tiddlers())
    model["fights"][0]["report_url"] = "https://dps.report/example"

    report = build_switchable_report("<html><title>Report</title></html>", model)

    assert "Open Fight Log" in report
    assert "report_url || f.log_url" in report
    assert 'target=\\"_blank\\"' in report


def test_switchable_report_embeds_skin_ready_model_without_script_breakout():
    classic = "<html><title>Report</title><body>classic</body></html>"
    model = build_night_model(_tiddlers())
    model["warnings"].append("</script><script>bad()</script> __PAYLOAD__")

    report = build_switchable_report(classic, model, default_view="simple")

    assert '"defaultView":"simple"' in report
    assert "</script><script>bad()" not in report
    unpacked = unpack_night_model(report)
    assert "__PAYLOAD__" in unpacked["warnings"][-1]
    assert unpacked["totals"] == model["totals"]


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
    assert unpack_night_model(report)["enemy_intel"]["coverage"][
        "selected_fights"
    ] == 31
    assert "logs selected" in report
    assert "modeled encounters" in report
    assert "unmodeled / excluded" in report
    assert "Modeled fights" in report
    assert "Combat time" in report
    assert "confidenceLabel" in report
    assert "roleDisplay(role, inference)" in report


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


def test_enemy_intel_all_view_summarizes_all_fights_and_labels_estimates():
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

    assert "All fights composition" in report
    assert "Estimated Enemy Group Composition" in report
    assert "Profession frequency is observed; Subgroup placement and roles are estimated" in report
    assert "does not claim every enemy group used the same composition" in report
    assert "Estimated Enemy Squad Composition" in report
    assert "Observed profession" in report
    assert "Inferred placement" in report
    assert "Unknown" in report
    assert "AI Enemy Read" in report
    assert "fetch(" not in report


def test_enemy_intel_overlays_squad_results_and_comp_without_blending_colors():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function ourEnemyComparison(pressure, scope, fight)" in report
    assert "Our Squad vs Enemy" in report
    assert 'fightSum("damage_out")' in report
    assert 'enemy:fightSum("damage_in")' in report
    assert 'enemy:fightSum("ally_downs")' in report
    assert "matchedFights.reduce" in report
    assert "Per enemy / fight" in report
    assert "observed enemy player-fight appearances" in report
    assert "not total battlefield output" in report
    assert 'data-duel-mode=\\\"normalized\\\"' in report
    assert "Pressure tools are not color-separated by the source log" in report
    assert "shown only" in report and "under All opponents" in report
    assert "function compositionComparison(scope, fight)" in report
    assert "Our roster is observed" in report
    assert "scopes.map(function(scope){return compositionComparison(scope,null);})" in report


def test_enemy_intel_uses_prominent_scope_buttons_with_clear_active_state():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function enemyScopeButtons()" in report
    assert 'class=\\"enemy-scope-switcher\\"' in report
    assert 'data-enemy-scope=\\"all\\"' in report
    assert "modeled fights · compare every opponent" in report
    assert "scope.fight_indexes" in report
    assert "average enemies" in report
    assert "syncEnemyScopeButtons" in report
    assert 'button.setAttribute("aria-selected"' in report
    assert ".enemy-scope-button.selected" in report
    assert 'colorSelect.setAttribute("aria-hidden", "true")' in report


def test_enemy_scope_keeps_a_scope_specific_estimated_group_view():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function allFightsCompositionView(scope)" in report
    assert "allFightsCompositionView(scope)" in report
    assert "Estimated " in report and "Group Composition" in report
    assert "summarizes " in report and "matched fights" in report
    assert 'scopeRef + "|" + row.profession' in report


def test_enemy_intel_exposes_skill_frequency_damage_and_strip_direction():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function enemySkillPressureTable(pressure)" in report
    assert "Enemy Skill Pressure" in report
    assert "Connected Hits" in report
    assert "Cast Count" in report
    assert "Damage / Hit" in report
    assert "function stripPressureComparison(pressure)" in report
    assert "Our Boon Removal" in report
    assert "Incoming Boon Removal" in report
    assert "Top boon-removal contributors" in report
    assert 'pressureBars(pressure.top_damage_skills, "Most Connected Enemy Skill Hits"' in report
    assert '"connected_hits"' in report
    assert 'table.getAttribute("data-initial-limit")' in report


def test_enemy_build_evidence_names_unknown_food_and_trait_absence():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "No unique trait procs or enemy food / utility buffs were exposed" in report
    assert "none was exposed in target buff data for this night" in report
    assert "unknown, not unequipped" in report


def test_enemy_build_evidence_renders_profession_name_once_per_card():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'professionInline(row.profession)+esc(row.profession)' not in report
    assert '<h3>"+professionInline(row.profession)+"</h3>' in report


def test_enemy_build_heading_only_names_consumables_when_any_were_observed():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'var evidenceTitle=hasConsumables ? "Enemy Traits & Consumables" : "Observed Enemy Trait Procs"' in report
    assert '<h2>"+esc(evidenceTitle)+"</h2>' in report


def test_scoped_enemy_team_keeps_build_evidence_and_profession_role_candidates():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function professionRoleCandidate(profession,validation)" in report
    assert "professionRoleCandidate(profession,validation)" in report
    assert "enemyBuildEvidenceView(validation,rows.map" in report
    assert 'scope ? "" : "<div class=\\"intel-kpis\\"' not in report
    assert "profession-level role candidates" in report
    assert 'tags.indexOf("healing")>=0 && tags.indexOf("damage")<0' in report


def test_composition_comparison_sorts_enemy_frequency_before_our_frequency():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert '(enemyAvg[b]||0)-(enemyAvg[a]||0)' in report
    assert '(ourAvg[b]||0)-(ourAvg[a]||0)' in report


def test_scoped_enemy_view_has_a_compact_map_to_every_detailed_section():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function scopeRoadmap(scope)" in report
    assert "More Intel Below" in report
    assert "Profession Comparison" in report
    assert "Estimated Subgroups" in report
    assert "Fight Output" in report
    assert "Pressure Coverage" in report
    assert "function wireScopePanel()" in report
    assert "scope-mini-parties" in report
    assert 'cloneNode(true)' in report
    assert 'scrollIntoView({behavior:"smooth",block:"start"})' in report
    assert 'data-scope-section=\\"estimated-subgroups\\"' in report
    assert "row.hidden=expanded && index>=limit" in report


def test_profession_rows_render_icons_and_boon_economy_keeps_units_separate():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function professionInline" in report
    assert "score-player" in report
    assert "boonGenerationCharts()" in report
    assert "Boon Economy" in report
    assert "Boon removals received" in report
    assert "no combined score is invented" in report


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


def test_enemy_role_badges_include_offline_vector_role_glyphs():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function roleGlyph(role)" in report
    assert 'class=\\"role-glyph role-glyph-' in report
    assert 'roleGlyph(role)+esc(roleText)' in report
    assert ".role-glyph svg" in report


def test_pro_has_persisted_near_black_theme_and_semantic_color_pop():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'value="blackout" selected>Blackout<' in report
    assert 'var currentTheme = "blackout"' in report
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
    assert "No network call is made" not in report
    assert "Detailed breakdown" in report
    assert "function summaryDrillHtml" in report
    assert "All fights · highest first" in report
    assert "Enemies downed by the squad" in report


def test_player_drill_uses_tonights_full_profile_not_a_shallow_history_row():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function playerProfileHtml" in report
    assert "Tonight’s player snapshot" in report
    assert "Damage to Enemy Players" in report
    assert "Power DPS" in report
    assert "Key Boon Uptime" in report
    assert "High Scores from This Night" in report
    assert "Historical leaderboard rows already expose all exported history fields" in report


def test_power_damage_view_reads_nested_damage_fields():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function derivedDamageBoard" in report
    assert 'derivedDamageBoard("Power Damage","targetpower","targetpowerps","Power DPS")' in report
    assert "function powerDamageView" in report
    assert "powerDamageView()" in report


def test_player_skill_damage_table_is_responsive_without_horizontal_scroll_contract():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert r'class=\"skill-damage-table\"' in report
    assert r'data-label=\"Down Contribution\"' in report
    assert '.skill-damage-table .skill-name{width:31%}' in report
    assert '.skill-damage-table .skill-value{width:13.5%}' in report
    assert '.skill-damage-table{min-width:760px}' not in report
    assert '.skill-damage-table,.skill-damage-table tbody{display:block;width:100%;min-width:0}' in report
    assert '.skill-damage-table tbody tr[hidden]{display:none}' in report


def test_curated_support_healing_and_dps_views_do_not_use_ambiguous_duplicates():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function supportOverviewView" in report
    assert 'supportMetric("Condition Cleanses","condicleanse","Cleanses / min")' in report
    assert "Combat-Resurrection Healing" in report
    assert "this is not an overall support score" in report
    assert "function healingOverviewView" in report
    assert "function healingAndBarrierView" in report
    assert "function damageCompositionView" in report
    assert "Total DPS · Power + Condition" in report
    assert "fightImpactBoards(true)" in report


def test_high_score_and_enemy_comparison_views_have_semantic_layout_and_drills():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "Player / Fight / Skill" in report
    assert "score-rank" in report and "score-entry" in report
    assert "Our avg / fight" in report and "Enemy avg / fight" in report
    assert "average profession sightings per matched fight" in report
    assert 'drillAttrs("comparison-row"' in report
    assert "function comparisonDrillHtml" in report
    assert "function compositionProfessionDrillHtml" in report


def test_details_separates_tonight_players_from_historical_context():
    report = build_switchable_report(
        "<html><title>Report</title></html>,",
        build_night_model(_tiddlers()),
    )

    assert "Tonight’s Source Tables" in report
    assert "Historical · not tonight" in report
    assert "these players did not necessarily participate tonight" in report
    assert "tonight-source-tables" in report
    assert "historical-source-tables" in report


def test_observed_squad_composition_uses_five_slot_party_rows():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "Squad Composition by Party" in report
    assert "our-party-row" in report
    assert "our-party-members" in report
    assert "repeat(5,minmax(0,1fr))" in report
    assert 'drillAttrs("session-player",player.name' in report
    assert "Party numbers come directly from the exported squad table" in report


def test_fight_timeline_is_fully_expanded_and_uses_compact_time_columns():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function fightsTable(showAll)" in report
    assert "fightsTable(true)" in report
    assert "index >= 5 && !showAll" in report
    assert "fightClock(f.time_label)" in report
    assert "fight-col-time" in report


def test_fight_drill_uses_kd_and_down_share_with_explicit_sides():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function fightKd(fight)" in report
    assert "function fightDownShare(fight)" in report
    assert '"K/D " + fightKd(fight)' in report
    assert "kills /" in report and "deaths" in report
    assert "Down share" in report and "fightDownShare(fight)" in report
    assert "enemy downs /" in report and "squad downs" in report


def test_metric_bars_render_player_name_and_class_as_one_identity_block():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "function playerBarLabel" in report
    assert "player-bar-label" in report
    assert "metric-player-text" in report
    assert report.count("playerBarLabel(row)") >= 2
    assert "pressure-row .bar-label" in report
    assert 'class=\\"metric-row pressure-row\\"' in report


def test_poison_table_has_semantic_columns_top_five_and_profession_icons():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'class=\\"poison-table\\"' in report
    assert 'class=\\"poison-player\\"' in report
    assert "Poison / sec" in report
    assert "professionInline(r.prof || r.profession" in report
    assert "poison-extra" in report
    assert "Expand all " in report


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
    assert 'if (currentView !== "classic")' in report
    assert "wireInteractive(frame.contentDocument)" in report
    assert "data-sort-key" in report
    assert "player-cell" in report and "class-cell" in report
    assert "data-label" in report
    assert "table[data-board-table] thead" in report
    assert "content:attr(data-label)" in report
    assert "Classic called this source table Mechanics" in report
    assert "This is not a pull count" in report
    assert "actual observed pull events are unavailable" in report
    assert "pull_skill_logged_hit_events" in report
    assert "pull_skill_casts" in report
    assert "Flux State" in report and "Abyssal Blot" in report
    assert "participation_weighted_rate" in report
    assert "value_per_minute" in report
    assert 'board.value_label || titleCase(board.stat)' in report
    assert 'esc(board.value_label || "Score")' not in report


def test_category_table_headers_do_not_repeat_metric_or_wrap_numbers():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'isPullBoard ? "Connected Hits" : "Total"' in report
    assert 'Logged Hits</th><th class=\\"number\\">Connection Rate' in report
    assert 'Casts</th><th class=\\"number\\">Connected Hits / Cast' in report
    assert 'data-sort-key=\\"rate\\"' in report
    assert 'title=\\"" + esc(totalLabel)' in report
    assert '.number{white-space:nowrap;overflow-wrap:normal}' in report
    assert 'grid-template-columns:1fr;gap:16px}.board' in report


def test_all_sort_buttons_toggle_directions_and_fight_table_is_responsive():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'data-sort-direction' in report
    assert 'aria-sort' in report
    assert 'direction === "ascending" ? 1 : -1' in report
    assert 'fight-outcome-' in report
    assert 'data-label=\\"Duration\\"' in report
    assert '.fights-table tbody tr{display:grid' in report
    assert 'Pull-skill connected hits' in report


def test_pro_has_a_top_level_two_player_comparison_and_interactive_skill_chart():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert '["compare","Player Compare"]' in report
    assert '<section data-section=\\"compare\\" hidden>' in report
    assert 'id=\\"compare-player-a\\"' in report
    assert 'id=\\"compare-player-b\\"' in report
    assert 'Same profession only' in report
    assert 'Detailed weapon and rotation evidence was not retained' in report
    assert 'Selected-night player profile · only exported fields are shown' not in report
    assert 'id=\\"skill-chart-dialog\\"' in report
    assert 'data-skill-chart' in report
    assert 'data-skill-slice' in report
    assert 'skill-chart-label' in report
    assert 'function openSkillChart' in report
    assert 'skillChartSvg(rows,total,true,true,valueKey,unitLabel)' in report
    assert 'if(event.target===drill) drill.close()' in report
    assert 'if(event.target===skillChartDialog) skillChartDialog.close()' in report
    assert 'drillBody.scrollTop=0' in report
    assert 'dialogBody.scrollTop=0' in report


def test_player_compare_uses_useful_squad_evidence_and_class_spec_name_order():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert "var evidencePlayers=((model.player_skill_evidence || {}).players || [])" in report
    assert "if (player) player.tables[table.source_key]=row" in report
    assert "player.name && (player.skillDamage || player.evidence)" in report
    assert "professionBase(a.profession).localeCompare(professionBase(b.profession))" in report
    assert "comparisonEliteLabel(a).localeCompare(comparisonEliteLabel(b))" in report
    assert 'return base.toLowerCase() === profession.toLowerCase() ? "Core" : profession' in report
    assert 'base+" · "+elite+" · "+player.name' in report
    assert "Cast share by skill" in report
    assert "Cast share is usage frequency, not damage" in report
    assert "Per-skill Damage was not exported for this player" not in report


def test_player_compare_profile_and_metric_columns_use_symmetric_layout():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'class=\\\"compare-player-card\\\"' in report
    assert 'class=\\\"compare-player-copy\\\"' in report
    assert 'compare-player-a' in report
    assert 'compare-player-b' in report
    assert (
        ".compare-stat>.compare-player-a,.compare-stat>.compare-player-b"
        "{text-align:right;font-variant-numeric:tabular-nums}"
    ) in report
    assert (
        ".compare-player-card{display:grid;grid-template-columns:42px minmax(0,1fr)"
    ) in report


def test_player_compare_cast_fallback_has_the_same_chart_shell_with_cast_units():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'skillSharePie(casts,"Cast share by skill","count","casts")' in report
    assert 'data-chart-value-key=\\""+esc(valueKey)+"\\"' in report
    assert 'data-chart-unit=\\""+esc(unitLabel)+"\\"' in report
    assert 'skillChartSvg(rows,total,true,true,valueKey,unitLabel)' in report
    assert 'doc.__skillChartValueKey=valueKey' in report
    assert 'doc.__skillChartUnit=unitLabel' in report


def test_enemy_composition_never_defaults_missing_role_evidence_to_dps():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'member.role || member.inferred_role || "DPS"' not in report
    assert 'Object.keys(votes).sort(function(a,b){return votes[b]-votes[a];})[0] || "DPS"' not in report
    assert 'member.role || member.inferred_role || "Unknown"' in report


def test_rate_only_uptime_board_names_the_boon_and_omits_fake_total_column():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'var boardLabel = metric.label || board.display_label || titleCase(board.stat);' in report
    assert 'var hasTotal = rows.some(function(row) { return row.total != null; }) || !hasRate;' in report
    assert 'metric.rate_unit === "percent" ? "%" : ""' in report
    assert '/uptime|percent|%/i.test(rateLabel) ? "Uptime %"' in report
    assert '(hasTotal ? "<col class=\\"col-total\\">" : "")' in report


def test_empty_historical_leaderboards_do_not_create_a_dead_end_tab():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'var hasHistoricalLeaderboards=(model.leaderboards || []).some' in report
    assert '.concat(hasHistoricalLeaderboards ? [["leaderboards","Long-term Leaderboards"]] : [])' in report
    assert '(hasHistoricalLeaderboards ? subpanel("scores", "leaderboards"' in report


def test_raw_source_tables_hide_unscored_and_redundant_boards():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
    )

    assert 'if (board.show_as_board === false)' in report
    assert 'var boards = allBoards();' in report


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
    assert 'boonUptimeCharts(["stability","protection","aegis","resolution","resistance"' in report
    assert 'boonUptimeCharts(["might","fury","quickness","alacrity"]' in report


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
