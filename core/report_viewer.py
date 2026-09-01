"""Build one offline night report with three viewer-selectable presentations.

The upstream report remains byte-for-byte available as Classic. A compact
skin-ready model powers the native Simple and Sparky views. Nothing is fetched
at view time: the original report is stored once as base64(gzip(html)).
"""

from __future__ import annotations

import base64
import gzip
import html as html_escape
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from core.night_model import build_night_model
from core.report_pack import is_packed, unpack_html


REPORT_VIEWS = ("sparky", "simple", "classic")
DEFAULT_REPORT_VIEW = "sparky"
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_PAYLOAD_RE = re.compile(
    r'<script id="classic-payload" type="text/plain">'
    r"([A-Za-z0-9+/=]+)</script>"
)
_VIEWER_MARKER = 'data-sparkybot-report-viewer="1"'
_SELECTED_FIGHTS_RE = re.compile(r"\((\d+)\s+fights?\)", re.IGNORECASE)


def normalize_report_view(value: str | None) -> str:
    value = (value or "").strip().casefold()
    return value if value in REPORT_VIEWS else DEFAULT_REPORT_VIEW


_SHELL_TEMPLATE = r"""<!doctype html>
<html lang="en" data-sparkybot-report-viewer="1">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { color-scheme:dark; --bg:#0b0f14; --bar:#111720; --panel:#161d27;
          --line:#2b3544; --text:#f2f5f8; --muted:#9aa8b8; --accent:#22c7e8;
          --accent-strong:#0d91ac; --on-accent:#041215; }
  :root[data-theme="midnight"] { --bg:#090d1b; --bar:#10162a; --panel:#161e35;
          --line:#2b3859; --text:#f1f4ff; --muted:#9ca9ca; --accent:#6d8cff;
          --accent-strong:#536fda; --on-accent:#080c18; }
  :root[data-theme="blackout"] { --bg:#020305; --bar:#07090d; --panel:#0d1117;
          --line:#242c38; --text:#f4f7fb; --muted:#8d9aad; --accent:#29d3ff;
          --accent-strong:#168cab; --on-accent:#010304; }
  :root[data-theme="studio-light"] { color-scheme:light; --bg:#eef1f5;
          --bar:#ffffff; --panel:#f8fafc; --line:#cbd3dd; --text:#17202b;
          --muted:#627084; --accent:#006d83; --accent-strong:#005467;
          --on-accent:#ffffff; }
  * { box-sizing:border-box; }
  body { margin:0; height:100vh; overflow:hidden; display:flex;
         flex-direction:column; background:var(--bg); color:var(--text);
         font:14px/1.4 Inter,Segoe UI,system-ui,sans-serif; }
  #viewer-bar { min-height:54px; display:flex; align-items:center; gap:8px;
                padding:8px 14px; background:var(--bar);
                border-bottom:1px solid var(--line);
                z-index:2; }
  #viewer-bar strong { margin-right:4px; letter-spacing:.02em; }
  #viewer-bar button { border:1px solid var(--line); color:var(--text);
                       background:var(--panel); border-radius:8px; padding:7px 16px;
                       cursor:pointer; font:inherit; font-weight:650; }
  #viewer-bar button:hover { border-color:var(--accent-strong); }
  #viewer-bar button.active { color:var(--on-accent); border-color:var(--accent);
                              background:var(--accent); }
  #theme-picker { border:1px solid var(--line); border-radius:8px; padding:7px 9px;
                  background:var(--panel); color:var(--text); font:inherit; }
  #theme-label { color:var(--muted); margin-left:auto; font-size:12px; }
  #viewer-meta { color:var(--muted); font-size:12px; }
  #report-frame { flex:1; width:100%; border:0; background:#fff; }
  #opening { padding:48px 20px; text-align:center; color:var(--muted); }
  #opening.error { color:#ff9e9e; }
  @media (max-width:620px) {
    #viewer-bar { flex-wrap:wrap; }
    #viewer-bar strong { width:100%; }
    #viewer-meta,#theme-label { display:none; }
    #viewer-bar button { flex:1; padding:7px 8px; }
  }
</style>
</head>
<body>
<div id="viewer-bar">
  <strong>View this report:</strong>
  <button type="button" data-view="sparky">Pro</button>
  <button type="button" data-view="simple">Simple</button>
  <button type="button" data-view="classic">Classic</button>
  <label id="theme-label" for="theme-picker">Theme</label>
  <select id="theme-picker" aria-label="Report theme">
    <option value="graphite">Graphite</option>
    <option value="midnight">Midnight</option>
    <option value="blackout">Blackout</option>
    <option value="studio-light">Studio Light</option>
  </select>
  <span id="viewer-meta">One file · same night · your choice</span>
</div>
<div id="opening">Opening the report…</div>
<iframe id="report-frame" title="Night report" hidden></iframe>
<script id="viewer-settings" type="application/json">__SETTINGS__</script>
<script id="night-model" type="application/json">__MODEL__</script>
<script id="classic-payload" type="text/plain">__PAYLOAD__</script>
<script>
(async function () {
  "use strict";
  var views = ["sparky", "simple", "classic"];
  var settings = JSON.parse(document.getElementById("viewer-settings").textContent);
  var model = JSON.parse(document.getElementById("night-model").textContent);
  var opening = document.getElementById("opening");
  var frame = document.getElementById("report-frame");
  var meta = document.getElementById("viewer-meta");
  var classicHtml = "";
  var docs = {};
  var currentView = "";
  var themes = ["graphite", "midnight", "blackout", "studio-light"];
  var currentTheme = "graphite";

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmt(value) {
    if (value == null || value === "") return "—";
    if (typeof value === "number") return value.toLocaleString(undefined, {
      maximumFractionDigits: 2
    });
    return esc(value);
  }
  function titleCase(value) {
    return String(value || "Stats").replace(/[-_]+/g, " ")
      .replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }
  function readableLabel(value) {
    return titleCase(String(value || "").replace(/_/g, " "));
  }
  function confidenceLabel(confidence) {
    if (!confidence) return "Unknown";
    if (typeof confidence !== "object") return readableLabel(confidence);
    var level = readableLabel(confidence.level || "Unknown");
    var score = Number(confidence.score);
    return level + (Number.isFinite(score) ? " · " + Math.round(score * 100) + "%" : "");
  }
  function humanDuration(value) {
    var text = String(value || "");
    var hours = Number((text.match(/(\d+)\s*h\b/i) || [0, 0])[1]);
    var minutes = Number((text.match(/(\d+)\s*m\b/i) || [0, 0])[1]);
    var seconds = Number((text.match(/(\d+)\s*s\b/i) || [0, 0])[1]);
    var parts = [];
    if (hours) parts.push(hours + "h");
    if (minutes) parts.push(minutes + "m");
    if (!hours && seconds) parts.push(seconds + "s");
    return parts.join(" ") || text;
  }
  function drillAttrs(kind, title, body, evidence) {
    return " tabindex=\"0\" data-drill data-drill-kind=\"" + esc(kind) +
      "\" data-drill-title=\"" + esc(title) + "\" data-drill-body=\"" +
      esc(body) + "\" data-drill-evidence=\"" + esc(evidence || "Report evidence") + "\"";
  }
  function professionBase(profession) {
    var value = String(profession || "Unknown").toLowerCase();
    var families = {
      guardian:["guardian","dragonhunter","firebrand","willbender","luminary"],
      warrior:["warrior","berserker","spellbreaker","bladesworn","paragon"],
      revenant:["revenant","herald","renegade","vindicator","conduit"],
      ranger:["ranger","druid","soulbeast","untamed","galeshot"],
      thief:["thief","daredevil","deadeye","specter","antiquary"],
      engineer:["engineer","scrapper","holosmith","mechanist","amalgam"],
      elementalist:["elementalist","tempest","weaver","catalyst","evoker"],
      mesmer:["mesmer","chronomancer","mirage","virtuoso","troubadour"],
      necromancer:["necromancer","reaper","scourge","harbinger","ritualist"]
    };
    return Object.keys(families).filter(function (base) {
      return families[base].indexOf(value) >= 0;
    })[0] || "unknown";
  }
  function professionGlyph(profession) {
    var base = professionBase(profession);
    var embedded = model.profession_icons && model.profession_icons[profession];
    if (embedded) {
      var source = String(embedded);
      if (/^[A-Za-z0-9+/=]+$/.test(source)) source = "data:image/png;base64," + source;
      if (/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(source)) {
        return "<span class=\"profession-glyph\" data-prof-base=\"" + base +
          "\"><img class=\"profession-icon\" src=\"" + source + "\" alt=\"\"></span>";
      }
    }
    var marks = {
      guardian:["M12 2 20 6v6c0 5-3.4 8.4-8 10-4.6-1.6-8-5-8-10V6z","G"],
      warrior:["M5 3l14 14-2 2L3 5zm14 0L5 17l2 2L21 5z","W"],
      revenant:["M12 2l8 10-8 10-8-10zm0 5l-4 5 4 5 4-5z","R"],
      ranger:["M12 2c6 4 8 10 3 17-4 4-10 1-9-4 1-6 4-10 6-13z","R"],
      thief:["M4 19L18 3l2 2L8 21zm9-2l5 4 2-2-5-4z","T"],
      engineer:["M9 2h6l1 3 3-1 3 5-3 2v4l3 2-3 5-3-1-1 3H9l-1-3-3 1-3-5 3-2v-4L2 9l3-5 3 1z","E"],
      elementalist:["M13 2c1 5-4 6-4 11 0 2 1 4 3 5-5 0-8-3-8-7 0-5 5-7 9-10-1 4 3 5 4 8 2-2 3-4 0-7 5 3 7 7 5 12-1 4-5 7-10 7 5-3 5-7 3-11 5 5 1 9-2 13-5 3-10-1-15-7-16z","E"],
      mesmer:["M12 3c5 0 9 4 9 9-2-3-5-5-9-5s-7 2-9 5c0-5 4-9 9-9zm0 7c3 0 6 1 8 4-2 4-5 7-8 7s-6-3-8-7c2-3 5-4 8-4z","M"],
      necromancer:["M12 3c5 0 8 3 8 8 0 4-2 6-5 7v3h-2v-3h-2v3H9v-3c-3-1-5-3-5-7 0-5 3-8 8-8zm-3 7a2 2 0 100 4 2 2 0 000-4zm6 0a2 2 0 100 4 2 2 0 000-4z","N"],
      unknown:["M12 2a10 10 0 110 20 10 10 0 010-20z","?"]
    };
    var mark = marks[base];
    return "<span class=\"profession-glyph\" data-prof-base=\"" + base +
      "\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"" +
      mark[0] + "\"></path><text x=\"12\" y=\"15\">" + mark[1] +
      "</text></svg></span>";
  }
  function roleClass(role) {
    var value = String(role || "unknown").toLowerCase();
    if (/heal|sustain/.test(value)) return "heal";
    if (/support|boon|strip|cleanse|control|cc/.test(value)) return "support";
    if (/dps|damage|power|condition|condi/.test(value)) return "dps";
    if (/hybrid|combination/.test(value)) return "hybrid";
    return "unknown";
  }
  function roleDisplay(role) {
    var kind=roleClass(role);
    if (kind === "heal") return "Likely Healing";
    if (kind === "support") return "Likely Boon Support";
    if (kind === "dps") return "Likely DPS";
    if (kind === "hybrid") return "Likely DPS / Support";
    return "Role not inferred";
  }
  function allBoards() {
    var seen = {};
    return (model.stat_tables || [])
      .filter(function (board) {
        var key = String(board.stat || "").toLowerCase();
        if (!key || seen[key] || !(board.rows || []).length) return false;
        if (Object.prototype.hasOwnProperty.call(board, "value_label") &&
            !board.value_label) return false;
        seen[key] = true;
        return true;
      });
  }
  function allSourceBoards() {
    return (model.leaderboards || []).concat(model.stat_tables || [])
      .filter(function (board) { return (board.rows || []).length; });
  }
  function longTermLeaderboardGrid() {
    var boards = (model.leaderboards || []).filter(function(board){
      return (board.rows || []).length;
    });
    if (!boards.length) return "<p class=\"empty\">No long-term leaderboards were exported.</p>";
    return "<div class=\"history-callout\"><b>Historical context</b><span>These are " +
      "long-term averages across raids, not tonight’s performance totals.</span></div>" +
      "<div class=\"boards\">" + boards.map(function(board){
        var rows=(board.rows || []).slice(0,100), metric=/avg|average/i.test(String(board.value_label || "")) ?
          board.value_label : titleCase(board.stat) + " Avg";
        var body=rows.map(function(row,index){
          var raids=row.raids != null ? row.raids : row.fights;
          return "<tr" + drillAttrs("leaderboard-row", row.name || "Historical entry",
            metric + " " + fmt(row.average != null ? row.average : row.value) +
            " · " + fmt(raids) + " raids", "Long-term leaderboard") +
            (index >= 5 ? " class=\"board-extra\" hidden" : "") + "><td class=\"rank\">" +
            (index+1) + "</td><td><b>" + esc(row.name) + "</b></td><td>" +
            esc(row.profession || "—") + "</td><td class=\"number\">" +
            fmt(row.average != null ? row.average : row.value) + "</td><td class=\"number\">" +
            fmt(raids) + "</td></tr>";
        }).join("");
        return "<article class=\"board historical-board\"><h3>" + esc(titleCase(board.stat)) +
          "</h3><div class=\"table-wrap\"><table><thead><tr><th>#</th><th>Player</th>" +
          "<th>Class</th><th class=\"number\">" + esc(metric) +
          "</th><th class=\"number\">Raids</th></tr></thead><tbody>" + body +
          "</tbody></table></div>" + (rows.length > 5 ? "<footer class=\"board-actions\">" +
          "<button type=\"button\" data-expand-board aria-expanded=\"false\">Expand all " +
          rows.length + "</button></footer>" : "") + "</article>";
      }).join("") + "</div>";
  }
  function boardCard(board, limit) {
    var rows = (board.rows || []).slice(0, limit || 100);
    var initialLimit = 5;
    var metricLabel = board.value_label || titleCase(board.stat);
    var hasRate = rows.some(function(row) {
      return row.participation_weighted_rate != null || row.value_per_minute != null ||
        row.per_minute != null || row.rate != null;
    });
    var hasParticipation = rows.some(function(row) {
      return row.participation_time != null || row.duration != null || row.fights != null;
    });
    var body = rows.map(function (row, index) {
      var title = (row.name || "Entry") + " · " + titleCase(board.stat);
      var detail = (row.profession || "Class unavailable") + " · " +
        metricLabel + " " + fmt(row.value);
      var rate = row.participation_weighted_rate != null ? row.participation_weighted_rate :
        (row.value_per_minute != null ? row.value_per_minute :
          (row.per_minute != null ? row.per_minute : row.rate));
      var participation = row.participation_time != null ? row.participation_time :
        (row.duration != null ? row.duration : row.fights);
      return "<tr" + drillAttrs("leaderboard-row", title, detail,
        "Source leaderboard · rank " + (row.rank || index + 1)) +
        " data-total=\"" + esc(row.value == null ? "" : row.value) +
        "\" data-rate=\"" + esc(rate == null ? "" : rate) +
        "\" data-participation=\"" + esc(participation == null ? "" : participation) + "\"" +
        (index >= initialLimit ? " class=\"board-extra\" hidden" : "") +
        "><td class=\"rank\">" + esc(row.rank || index + 1) +
        "</td><td><b>" + esc(row.name) + "</b>" +
        (row.account ? "<small>" + esc(row.account) + "</small>" : "") +
        "</td><td><span class=\"profession\">" +
        esc(row.profession || "—") + "</span></td><td class=\"number\">" +
        fmt(row.value) + "</td>" + (hasRate ? "<td class=\"number\">" + fmt(rate) + "</td>" : "") +
        (hasParticipation ? "<td class=\"number\">" + fmt(participation) + "</td>" : "") + "</tr>";
    }).join("");
    return "<article class=\"board\"><h3>" + esc(titleCase(board.stat)) +
      "</h3><div class=\"table-wrap\"><table data-board-table><thead><tr><th>#</th>" +
      "<th>Player</th><th>Class</th><th class=\"number\">" +
      "<button type=\"button\" data-sort-key=\"total\">" + esc(metricLabel) + "</button></th>" +
      (hasRate ? "<th class=\"number\"><button type=\"button\" data-sort-key=\"rate\">Per minute / weighted</button></th>" : "") +
      (hasParticipation ? "<th class=\"number\"><button type=\"button\" data-sort-key=\"participation\">Participation</button></th>" : "") +
      "</tr></thead><tbody>" + body + "</tbody></table></div>" +
      (rows.length > initialLimit ? "<footer class=\"board-actions\"><button type=\"button\" data-expand-board " +
        "aria-expanded=\"false\">Expand all " + rows.length + "</button></footer>" : "") + "</article>";
  }
  function totalsCards(compact) {
    var t = model.totals || {};
    var cards = [
      ["Modeled fights", t.fights],
      ["Enemy downs", t.enemy_downs],
      ["Enemy kills", t.enemy_kills],
      ["Our downs", t.ally_downs],
      ["Our deaths", t.ally_deaths],
      ["K/D", t.kdr]
    ];
    return "<div class=\"kpis" + (compact ? " compact" : "") + "\">" +
      cards.map(function (item, index) {
        return "<button type=\"button\" class=\"kpi metric-" + index + "\"" +
          drillAttrs("summary-metric", item[0], fmt(item[1]), "Modeled report total") +
          "><strong>" + fmt(item[1]) + "</strong><span>" + esc(item[0]) + "</span></button>";
      }).join("") + "</div>";
  }
  function nightMvpCards() {
    var source = model.night_mvps || [];
    var rows = Array.isArray(source) ? source : (source.categories || source.mvps || []);
    if (!rows.length && source && typeof source === "object") {
      rows = ["damage","healing","resurrection","condition_cleanses","boon_strips",
        "boon_support","stability","crowd_control","pulls","fight_impact"]
        .map(function(key){var row=source[key]; if(row && !row.category) row.category=readableLabel(key); return row;})
        .filter(Boolean);
    }
    if (!rows.length) return "";
    return "<section class=\"mvp-section\"><div class=\"section-head\"><div><span class=\"eyebrow\">Night MVPs</span>" +
      "<h2>Category leaders</h2></div><p>Separate winners by metric; no invented universal score.</p></div>" +
      "<div class=\"mvp-grid\">" + rows.map(function(row){
        var winner=row.winner || row.player || row.name || {}, name=typeof winner === "string" ? winner : winner.name;
        var total=row.total != null ? row.total : (row.value != null ? row.value : winner.total);
        var rate=row.rate != null ? row.rate : (row.per_minute != null ? row.per_minute : winner.rate);
        var fightTime=row.fight_time || row.participation_time || winner.fight_time;
        var category=row.category || row.metric || "Category leader";
        var detail=(row.total_label || "Total") + " " + fmt(total) +
          (rate != null ? " · rate " + fmt(rate) : "") +
          (fightTime ? " · Fight Time " + fmt(fightTime) : "");
        return "<button type=\"button\" class=\"mvp-card\"" + drillAttrs("night-mvp", category + " · " +
          (name || "Winner unavailable"), detail + " · " + (row.why || row.reason || "Highest qualifying result"),
          row.evidence || row.source || "Tonight’s exact performance table") + "><span>" +
          esc(category) + "</span><b>" + esc(name || "Winner unavailable") + "</b><strong>" +
          fmt(total) + "</strong>" + (rate != null ? "<small>" + fmt(rate) + " rate</small>" : "") +
          (fightTime ? "<small>Fight Time " + fmt(fightTime) + "</small>" : "") +
          "<p>" + esc(row.why || row.reason || "Highest qualifying result") + "</p></button>";
      }).join("") + "</div></section>";
  }
  function reportHeading(kicker) {
    var s = model.session || {};
    var title = s.commander ? esc(s.commander) + "’s night" : "Night report";
    var bits = [s.date, s.total_duration ? "Combat time " + humanDuration(s.total_duration) : null]
      .filter(Boolean).map(esc);
    return "<header class=\"hero\"><span class=\"eyebrow\">" +
      esc(kicker) + "</span><h1>" + title + "</h1><p>" +
      (bits.join(" · ") || "Combined WvW fight log summary") + "</p></header>";
  }

  function applyTheme(theme) {
    if (themes.indexOf(theme) < 0) theme = "graphite";
    currentTheme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    var picker = document.getElementById("theme-picker");
    if (picker) picker.value = theme;
    try {
      if (frame.contentDocument) {
        frame.contentDocument.documentElement.setAttribute("data-theme", theme);
      }
    } catch (_error) {}
    try { localStorage.setItem("sparkybot-report-theme", theme); } catch (_error) {}
  }

  var commonCss = [
    ":root{color-scheme:dark;--bg:#0b0f14;--surface:#111720;--panel:#161d27;",
    "--panel-2:#1c2531;--line:#2b3544;--line-soft:#202936;--text:#f2f5f8;",
    "--muted:#9aa8b8;--faint:#708094;--accent:#22c7e8;--accent-2:#ffb84d;",
    "--good:#34c989;--bad:#ff6b71;--purple:#ad8cff;--on-accent:#041215;",
    "--role-dps:#ff6f61;--role-heal:#36d699;--role-support:#56a8ff;--role-hybrid:#c88cff;",
    "--series-kills:#29d3ff;--series-downs:#ffd166;--series-our-downs:#ff8a5b;--series-deaths:#ff4d6d;",
    "--guardian:#72d5ff;--warrior:#ffd166;--revenant:#9c7dff;--ranger:#7ee081;",
    "--thief:#c7cad1;--engineer:#e7a85d;--elementalist:#f06f61;--mesmer:#dc78ff;",
    "--necromancer:#64d49a}",
    ":root[data-theme=midnight]{--bg:#090d1b;--surface:#10162a;--panel:#161e35;",
    "--panel-2:#1b2742;--line:#2b3859;--line-soft:#202b48;--text:#f1f4ff;",
    "--muted:#9ca9ca;--faint:#7181aa;--accent:#6d8cff;--accent-2:#ffb65c;",
    "--good:#44d29a;--bad:#ff7183;--purple:#bd8cff;--on-accent:#080c18}",
    ":root[data-theme=blackout]{--bg:#020305;--surface:#07090d;--panel:#0d1117;",
    "--panel-2:#121823;--line:#242c38;--line-soft:#171e28;--text:#f4f7fb;",
    "--muted:#8d9aad;--faint:#657286;--accent:#29d3ff;--accent-2:#ffd166;",
    "--good:#39d98a;--bad:#ff4d6d;--purple:#c58cff;--on-accent:#010304}",
    ":root[data-theme=studio-light]{color-scheme:light;--bg:#eef1f5;--surface:#fff;",
    "--panel:#f8fafc;--panel-2:#eef3f7;--line:#cbd3dd;--line-soft:#dde3ea;",
    "--text:#17202b;--muted:#627084;--faint:#7c8998;--accent:#006d83;",
    "--accent-2:#a65d00;--good:#087a53;--bad:#bc3340;--purple:#6e4bb4;",
    "--on-accent:#fff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);",
    "font:14px/1.45 Inter,Segoe UI,system-ui,sans-serif}",
    ".wrap{max-width:1280px;margin:auto;padding:30px 24px 60px}",
    ".hero{padding:26px 28px;border:1px solid var(--line);border-radius:14px;",
    "background:var(--surface)}",
    ".hero h1{font-size:clamp(28px,5vw,54px);line-height:1.02;margin:7px 0 8px}",
    ".hero p{margin:0;color:var(--muted)}.eyebrow{text-transform:uppercase;",
    "letter-spacing:.17em;color:var(--accent);font-size:11px;font-weight:800}",
    ".kpis{display:grid;grid-template-columns:repeat(6,minmax(110px,1fr));",
    "gap:12px;margin:18px 0 26px}.kpi{padding:16px;border:1px solid var(--line);",
    "border-top:3px solid var(--accent);border-radius:10px;background:var(--panel);color:var(--text);",
    "font:inherit;text-align:left;cursor:pointer}.kpi.metric-1{border-top-color:var(--series-downs)}",
    ".kpi.metric-2{border-top-color:var(--series-kills)}.kpi.metric-3{border-top-color:var(--series-our-downs)}",
    ".kpi.metric-4{border-top-color:var(--series-deaths)}.kpi strong{display:block;color:var(--accent);",
    "font-size:24px}.kpi span{color:var(--muted);font-size:12px}.boards{display:grid;",
    "grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.board{min-width:0;",
    "border:1px solid var(--line);background:var(--panel);border-radius:10px;overflow:hidden}",
    ".board h3{margin:0;padding:14px 16px;border-bottom:1px solid var(--line);",
    "font-size:15px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}",
    "th,td{padding:8px 11px;border-bottom:1px solid var(--line-soft);text-align:left;",
    "white-space:nowrap}th{color:var(--muted);font-size:11px;text-transform:uppercase;",
    "letter-spacing:.06em}th button{border:0;background:transparent;color:inherit;font:inherit;",
    "font-weight:800;text-transform:inherit;cursor:pointer;padding:0}.board-actions{padding:9px 12px;",
    "border-top:1px solid var(--line-soft)}.board-actions button{border:1px solid var(--line);",
    "border-radius:6px;background:var(--panel-2);color:var(--text);padding:6px 9px;cursor:pointer}",
    "td small{display:block;color:var(--faint)}.rank,.number{",
    "text-align:right;font-variant-numeric:tabular-nums}.profession{color:var(--text)}",
    ".empty{padding:24px;border:1px dashed var(--line);border-radius:10px;color:var(--muted)}",
    "button:focus-visible,[tabindex]:focus-visible{outline:2px solid var(--accent);outline-offset:2px}",
    "@media(max-width:850px){.kpis{grid-template-columns:repeat(3,1fr)}",
    ".boards{grid-template-columns:1fr}}@media(max-width:520px){",
    ".wrap{padding:18px 12px 40px}.kpis{grid-template-columns:repeat(2,1fr)}",
    ".hero{padding:22px 18px}}"
  ].join("");

  function renderSimple() {
    var boards = allBoards().slice(0, 8);
    var body = reportHeading("Simple · the useful headlines") +
      totalsCards(true) +
      (boards.length ? "<section class=\"boards\">" +
        boards.map(function (b) { return boardCard(b, 10); }).join("") +
        "</section>" : "<p class=\"empty\">No headline boards were found. " +
        "Classic still contains every source table.</p>") +
      "<p class=\"foot\">Simple intentionally skips the fight-by-fight wall. " +
      "Choose Sparky for the full guided report or Classic for the untouched source.</p>";
    return "<!doctype html><html><head><meta charset=\"utf-8\">" +
      "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
      "<title>Simple report</title><style>" + commonCss +
      ".foot{color:#809a91;margin-top:26px}</style></head><body><main class=\"wrap\">" +
      body + "</main></body></html>";
  }

  function fightsTable() {
    var fights = model.fights || [];
    if (!fights.length) return "<p class=\"empty\">No fight rows were found.</p>";
    return "<div class=\"table-panel table-wrap\"><table><thead><tr>" +
      "<th>#</th><th>Time</th><th>Duration</th><th>Squad</th><th>Enemy</th>" +
      "<th>Downs</th><th>Kills</th><th>Damage out</th><th>Damage in</th>" +
      "</tr></thead><tbody>" + fights.map(function (f) {
        return "<tr" + drillAttrs("fight-row", "Fight " + (f.index || "—"),
          "Squad " + fmt(f.squad) + " vs " + fmt(f.enemy) + " · " +
          fmt(f.downs) + " downs · " + fmt(f.kills) + " kills",
          "Observed fight summary · " + (f.time_label || "time unavailable")) +
          "><td class=\"number\">" + fmt(f.index) + "</td><td>" +
          fmt(f.time_label) + "</td><td>" + fmt(f.duration) + "</td><td class=\"number\">" +
          fmt(f.squad) + "</td><td class=\"number\">" + fmt(f.enemy) +
          "</td><td class=\"number\">" + fmt(f.downs) + "</td><td class=\"number\">" +
          fmt(f.kills) + "</td><td class=\"number\">" + fmt(f.damage_out) +
          "</td><td class=\"number\">" + fmt(f.damage_in) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }
  function poisonTable() {
    var rows = (model.poison || []).slice().sort(function (a, b) {
      return (b.apps_per_min || 0) - (a.apps_per_min || 0);
    });
    if (!rows.length) return "<p class=\"empty\">No poison coverage data was found.</p>";
    return "<div class=\"table-panel table-wrap\"><table><thead><tr><th>#</th>" +
      "<th>Player</th><th>Class</th><th>Applications</th><th>Apps/min</th>" +
      "<th>Output</th></tr></thead><tbody>" + rows.map(function (r, i) {
        return "<tr" + drillAttrs("condition-row", r.name || "Poison output",
          fmt(r.apps) + " applications · " + fmt(r.apps_per_min) + " per minute",
          "Observed poison output; relic trigger attribution unavailable") +
          "><td class=\"number\">" + (i + 1) + "</td><td><b>" +
          esc(r.name) + "</b><small>" + esc(r.account) + "</small></td><td>" +
          esc(r.prof) + "</td><td class=\"number\">" + fmt(r.apps) +
          "</td><td class=\"number\">" + fmt(r.apps_per_min) +
          "</td><td class=\"number\">" + fmt(r.output) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }
  function squadCards() {
    var squads = model.squad_composition && model.squad_composition.squads || [];
    if (!squads.length) return "";
    return "<h2>Squad composition</h2><div class=\"squad-grid\">" +
      squads.map(function (s) {
        return "<article class=\"squad\"><h3>Fight " + fmt(s.fight) +
          "</h3><p>" + (s.players || []).map(function (p) {
            return "<span>" + esc(p.name) + " · " + esc(p.profession || "?") +
              "</span>";
          }).join("") + "</p></article>";
      }).join("") + "</div>";
  }
  function highScoreCards() {
    var blocks = model.high_scores && model.high_scores.blocks || [];
    if (!blocks.length) return "";
    return "<h2>High scores</h2><div class=\"boards\">" +
      blocks.map(function (block) {
        var rows = (block.rows || []).slice(0, 10).map(function (row, i) {
          return "<tr><td class=\"rank\">" + (i + 1) + "</td><td>" +
            esc((row.cells || []).join(" · ")) + "</td><td class=\"number\">" +
            fmt(row.score) + "</td></tr>";
        }).join("");
        return "<article class=\"board\"><h3>" +
          esc(block.caption || "High score") + "</h3><table><tbody>" +
          rows + "</tbody></table></article>";
      }).join("") + "</div>";
  }
  function boardsFor(terms) {
    if (!terms || !terms.length) return allBoards();
    return allBoards().filter(function (board) {
      var key = (String(board.stat || "") + " " +
        String(board.caption || "") + " " + String(board.value_label || "")).toLowerCase();
      return terms.some(function (term) { return key.indexOf(term) >= 0; });
    });
  }
  function boardGrid(terms, limit, emptyText) {
    var boards = boardsFor(terms);
    return boards.length ? "<div class=\"boards\">" + boards.map(function (board) {
      return boardCard(board, limit || 10);
    }).join("") + "</div>" : "<p class=\"empty\">" + esc(emptyText ||
      "No matching source table was available for this report.") + "</p>";
  }
  function sourceBoardGrid() {
    var boards = allSourceBoards();
    if (!boards.length) return "<p class=\"empty\">No source tables were available.</p>";
    return "<div class=\"detail-counts\"><span><b>" +
      fmt((model.leaderboards || []).length) + "</b> leaderboards</span><span><b>" +
      fmt((model.stat_tables || []).length) + "</b> stat tables</span><span><b>" +
      fmt((model.high_scores && model.high_scores.blocks || []).length) +
      "</b> high-score blocks</span><span><b>" + fmt((model.poison || []).length) +
      "</b> poison rows</span></div><div class=\"boards\">" +
      boards.map(function (board) { return boardCard(board, 100); }).join("") +
      "</div>";
  }
  function sectionHead(kicker, title, text) {
    return "<div class=\"section-head\"><div><span class=\"eyebrow\">" +
      esc(kicker) + "</span><h2>" + esc(title) + "</h2></div><p>" +
      esc(text) + "</p></div>";
  }
  function subnav(group, items) {
    return "<nav class=\"subtabs\" aria-label=\"" + esc(titleCase(group)) +
      " views\" data-subnav=\"" + esc(group) + "\">" + items.map(function (item, i) {
        return "<button type=\"button\" role=\"tab\" aria-selected=\"" +
          (i ? "false" : "true") + "\" class=\"" + (i ? "" : "selected") +
          "\" data-subtab=\"" + esc(item[0]) + "\">" + esc(item[1]) + "</button>";
      }).join("") + "</nav>";
  }
  function subpanel(group, id, content, hidden) {
    return "<div role=\"tabpanel\" data-subsection=\"" + esc(group) +
      "\" data-subview=\"" + esc(id) + "\"" + (hidden ? " hidden" : "") +
      ">" + content + "</div>";
  }
  function outcomeChart() {
    var fights = model.fights || [];
    if (!fights.length) return "<p class=\"empty\">No fight timeline was available.</p>";
    var series = [
      {key:"kills", label:"Kills", cls:"kills", value:function(f){return Number(f.kills || 0);}},
      {key:"downs", label:"Enemy downs", cls:"downs", value:function(f){return Number(f.downs || 0);}},
      {key:"ally_downs", label:"Our downs", cls:"our-downs", value:function(f){return Number(f.ally_downs || f.our_downs || f.downs_taken || 0);}},
      {key:"ally_deaths", label:"Our deaths", cls:"deaths", value:function(f){return Number(f.ally_deaths || f.our_deaths || f.deaths || 0);}}
    ];
    var width = Math.max(660, fights.length * 34), height = 210, baseY = 174;
    var max = Math.max.apply(null, fights.reduce(function(values, fight) {
      return values.concat(series.map(function(s){return s.value(fight);}));
    }, [1]));
    var groupWidth = (width - 36) / fights.length;
    var bars = fights.map(function (f, fightIndex) {
      return series.map(function (s, seriesIndex) {
        var value = s.value(f), barHeight = (value / max) * 132;
        var x = 19 + fightIndex * groupWidth + seriesIndex * Math.min(6, groupWidth / 5);
        return "<rect class=\"series-" + s.cls + "\" x=\"" + x.toFixed(1) +
          "\" y=\"" + (baseY - barHeight).toFixed(1) + "\" width=\"" +
          Math.max(3, Math.min(5, groupWidth / 6)).toFixed(1) + "\" height=\"" +
          Math.max(value ? 2 : 0, barHeight).toFixed(1) + "\"" +
          drillAttrs("chart-bar", "Fight " + (f.index || fightIndex + 1) + " · " + s.label,
            String(value), "Observed fight summary when exported; zero may mean unavailable") +
          "><title>Fight " + esc(f.index || fightIndex + 1) + " · " + s.label +
          ": " + value + "</title></rect>";
      }).join("");
    }).join("");
    return "<div class=\"chart-card outcome-chart\"><div class=\"chart-title\"><b>Fight outcomes</b>" +
      "<span>Compact multi-series timeline · select a bar for evidence</span></div>" +
      "<div class=\"chart-legend\">" + series.map(function(s){return "<span class=\"key-" +
      s.cls + "\">" + s.label + "</span>";}).join("") + "</div><div class=\"svg-scroll\"><svg " +
      "viewBox=\"0 0 " + width + " " + height + "\" role=\"img\" " +
      "aria-label=\"Kills, enemy downs, our downs, and our deaths by fight\"><line x1=\"15\" y1=\"" +
      baseY + "\" x2=\"" + (width - 15) + "\" y2=\"" + baseY +
      "\" class=\"axis\"></line>" + bars + "</svg></div></div>";
  }
  function fightPulse() { return outcomeChart(); }
  function metricBars(terms, title, tone, limit) {
    var rows = [];
    boardsFor(terms).forEach(function (board) {
      (board.rows || []).slice(0, limit || 8).forEach(function (row) {
        if (Number(row.value) || Number(row.value) === 0) rows.push({row:row, board:board});
      });
    });
    rows = rows.slice(0, limit || 8);
    if (!rows.length) return "<p class=\"empty\">No chartable values were exported.</p>";
    var max = Math.max.apply(null, rows.map(function(item){return Math.abs(Number(item.row.value)||0);}).concat([1]));
    return "<div class=\"bar-card tone-" + esc(tone || "accent") + "\"><div class=\"chart-title\"><b>" +
      esc(title) + "</b><span>Select a bar to inspect its source</span></div>" + rows.map(function(item){
        var row=item.row, value=Number(row.value)||0, width=Math.max(2, Math.abs(value)/max*100);
        return "<button type=\"button\" class=\"metric-row\"" + drillAttrs("chart-bar",
          row.name || title, titleCase(item.board.stat) + " · " + fmt(value),
          "Source leaderboard · " + (item.board.value_label || "reported value")) +
          "><span class=\"bar-label\">" + esc(row.name || "Entry") + "</span><i><em style=\"width:" +
          width.toFixed(1) + "%\"></em></i><b>" + fmt(value) + "</b></button>";
      }).join("") + "</div>";
  }
  function conditionHeatmap(pressure) {
    pressure = pressure || {};
    var rows = (pressure.conditions_in || []).concat(pressure.debuffs_in || [])
      .concat(pressure.condition_profile || []).slice(0, 18);
    if (!rows.length) return "<p class=\"empty\">No condition heatmap values were exported.</p>";
    var max = Math.max.apply(null, rows.map(function(row){return Number(row.uptime_percent || row.count || row.value || 0);}).concat([1]));
    return "<div class=\"heatmap\" aria-label=\"Incoming condition heatmap\">" + rows.map(function(row){
      var label=row.effect || row.name || row.condition || "Condition";
      var value=Number(row.uptime_percent || row.count || row.value || 0);
      var level=Math.max(.18,value/max);
      return "<button type=\"button\" class=\"heat-cell\" style=\"--heat:" + level.toFixed(2) + "\"" +
        drillAttrs("heatmap-cell", label, String(value), readableLabel(row.evidence || "observed")) +
        "><b>" + esc(label) + "</b><span>" + fmt(value) +
        (row.uptime_percent != null ? "% uptime" : "") + "</span></button>";
    }).join("") + "</div>";
  }
  function pressureBars(rows, title, tone) {
    rows = (rows || []).slice(0, 10);
    if (!rows.length) return "<p class=\"empty\">No observed values exported for this metric.</p>";
    var values=rows.map(function(row){return Number(row.damage || row.count || row.uptime_percent || row.value || 0);});
    var max=Math.max.apply(null, values.concat([1]));
    return "<div class=\"bar-card tone-" + esc(tone || "enemy") + "\"><div class=\"chart-title\"><b>" +
      esc(title) + "</b><span>All-opponent evidence unless scoped</span></div>" + rows.map(function(row,index){
        var label=row.skill || row.effect || row.name || "Entry", value=values[index];
        return "<button type=\"button\" class=\"metric-row\"" + drillAttrs("chart-bar", label,
          String(value), readableLabel(row.evidence || "observed") + " · " +
          readableLabel(row.source_scope || "session")) + "><span class=\"bar-label\">" + esc(label) +
          "</span><i><em style=\"width:" + Math.max(2,value/max*100).toFixed(1) + "%\"></em></i><b>" +
          fmt(value) + "</b></button>";
      }).join("") + "</div>";
  }
  function poisonContext() {
    return "<p class=\"accuracy condition-note\"><b>Poison evidence:</b> applications " +
      "and output are observed. The log cannot attribute individual applications to " +
      "Demon Queen Relic. Open any row for its evidence.</p>";
  }
  function highScoreGrid(terms) {
    var blocks = model.high_scores && model.high_scores.blocks || [];
    if (terms && terms.length) blocks = blocks.filter(function (block) {
      var key = String(block.caption || "").toLowerCase();
      return terms.some(function (term) { return key.indexOf(term) >= 0; });
    });
    if (!blocks.length) return "<p class=\"empty\">No matching high-score blocks were found.</p>";
    return "<div class=\"boards\">" + blocks.map(function (block) {
      var rows = (block.rows || []).map(function (row, i) {
        return "<tr><td class=\"rank\">" + (i + 1) + "</td><td>" +
          esc((row.cells || []).join(" · ")) + "</td><td class=\"number\">" +
          fmt(row.score) + "</td></tr>";
      }).join("");
      return "<article class=\"board\"><h3>" + esc(block.caption || "High score") +
        "</h3><div class=\"table-wrap\"><table><tbody>" + rows +
        "</tbody></table></div></article>";
    }).join("") + "</div>";
  }
  function chips(rows, labelKey, valueKey, limit) {
    rows = (rows || []).slice(0, limit || 12);
    if (!rows.length) return "<span class=\"muted\">No observed entries for this scope</span>";
    return "<div class=\"chips\">" + rows.map(function (row) {
      var label = typeof row === "string" ? row :
        (row[labelKey] || row.effect || row.name || row.skill || row.profession || "Unlabeled entry");
      var value = typeof row === "string" ? "" :
        (row[valueKey] != null ? row[valueKey] : (row.count != null ? row.count :
          (row.damage != null ? row.damage : row.uptime_percent)));
      return "<button type=\"button\"" + drillAttrs("pressure", label,
        value == null || value === "" ? "No numeric total exported" : String(value),
        readableLabel(row.evidence || "reported or inferred") + " · " +
          readableLabel(row.source_scope || "current report scope")) + "><b>" +
        esc(label) + "</b>" + (value == null || value === "" ? "" : " · " +
        fmt(value)) + "</button>";
    }).join("") + "</div>";
  }
  function conditionProfile(pressure) {
    pressure = pressure || {};
    var rows = (pressure.conditions_in || []).concat(pressure.debuffs_in || [])
      .concat(pressure.condition_profile || []);
    var profile = pressure.damage_profile || pressure.damage_mix;
    var profileHtml = "";
    if (profile && typeof profile === "object" &&
        !/not_available|unavailable/.test(String(profile.status || ""))) {
      profileHtml = chips(Array.isArray(profile) ? profile : [profile], "label", "value", 4);
    }
    if (rows.length || profileHtml) return chips(rows, "effect", "uptime_percent", 12) + profileHtml;
    return "<p class=\"inference-note\"><b>General pressure read:</b> No per-color breakdown " +
      "was exported. Use the observed incoming skill bars below to judge strike versus " +
      "condition pressure; exact proportions would be invented.</p>";
  }
  function stripProfile(scope, pressure) {
    pressure = pressure || {};
    var reported = (pressure.incoming_strips || []).concat(pressure.strips_in || [])
      .concat(pressure.generalized_incoming_strips || []);
    if (reported.length) return chips(reported, "effect", "count", 12);
    var aggregate = scope && scope.aggregate || {};
    var likely = (aggregate.professions || []).filter(function (row) {
      return /scourge|necromancer|reaper|harbinger|mesmer|chrono|spellbreaker|thief|specter/i
        .test(String(row.profession || ""));
    }).slice(0, 8);
    return "<p class=\"inference-note\"><b>Source total not exported.</b> " +
      (likely.length ? "Likely strip-capable professions observed: " +
        likely.map(function (row) { return esc(row.profession) + " ×" + fmt(row.count); }).join(", ") +
        ". This is capability evidence, not cast attribution." :
        "No precise skill or profession attribution is available for this scope.") + "</p>";
  }
  function enemyScopes() {
    return model.enemy_intel && model.enemy_intel.scopes || [];
  }
  function enemyCoverage() {
    var c = model.enemy_intel && model.enemy_intel.coverage || {};
    var selected = c.selected_fights == null ? "—" : c.selected_fights;
    var reported = c.reported_fights == null ? (c.modeled_fights == null ? "—" : c.modeled_fights) : c.reported_fights;
    var excluded = (typeof selected === "number" && typeof reported === "number") ?
      Math.max(0, selected - reported) : null;
    return "<div class=\"coverage\"><span><b>" + fmt(selected) + "</b> logs selected</span>" +
      "<span><b>" + fmt(reported) + "</b> modeled encounters</span>" +
      (excluded ? "<span><b>" + fmt(excluded) + "</b> unmodeled / excluded</span>" : "") +
      "<span><b>" +
      fmt(c.composition_snapshots) + "</b> composition snapshots</span><span><b>" +
      fmt((c.colors || []).join ? c.colors.join(" / ") : c.colors) +
      "</b> enemy colors</span></div>";
  }
  function pressurePanels(scope) {
    var intel = model.enemy_intel || {};
    var pressure = scope && scope.aggregate || intel.session_pressure || {};
    var sessionOnly = pressure.pressure_scope === "session_only" ||
      pressure.source_scope === "session_only" || (scope && !(scope.aggregate || {}).top_damage_skills);
    if (scope && sessionOnly) pressure = intel.session_pressure || pressure;
    var ccRows = (pressure.cc || []).concat(pressure.pulls || []);
    var stripRows = (pressure.incoming_strips || []).concat(pressure.strips_in || [])
      .concat(pressure.generalized_incoming_strips || []);
    return "<p class=\"scope-note\">" + (scope && sessionOnly ?
      "All opponents · No per-color breakdown in the source export" : "Observed for this scope") + "</p>" +
      "<div class=\"intel-visuals\">" +
      pressureBars(pressure.top_damage_skills, "Incoming Skill Damage", "damage") +
      conditionHeatmap(pressure) + pressureBars(ccRows, "Crowd Control and Pulls", "control") +
      pressureBars(stripRows, "Incoming Boon Strips", "strip") + "</div>" +
      "<div class=\"intel-grid\"><article class=\"intel-card\"><h3>Incoming damage skills</h3>" +
      chips(pressure.top_damage_skills, "skill", "damage", 10) + "</article>" +
      "<article class=\"intel-card\"><h3>Condition Damage profile</h3>" +
      conditionProfile(pressure) +
      "</article><article class=\"intel-card\"><h3>Incoming Boon Strips</h3>" +
      stripProfile(scope, pressure) + "</article>" +
      "<article class=\"intel-card\"><h3>Crowd Control and Pulls</h3>" +
      chips(ccRows, "skill", "count", 10) +
      "</article></div>";
  }
  function scopeSummary(scope) {
    var a = scope && scope.aggregate || {};
    return "<div class=\"intel-kpis\"><div><strong>" + fmt(a.enemy_size_avg) +
      "</strong><span>average enemy size</span></div><div><strong>" +
      fmt(a.enemy_size_max) + "</strong><span>largest observed</span></div><div><strong>" +
      fmt((scope && scope.fight_indexes || []).length) + "</strong><span>fight snapshots</span></div></div>" +
      "<article class=\"intel-card wide\"><h3>Observed professions</h3>" +
      chips(a.professions, "profession", "count", 30) + "</article>" + pressurePanels(scope);
  }
  function comparisonView() {
    var intel = model.enemy_intel || {};
    var comparisons = intel.all && intel.all.comparisons || [];
    var scopes = enemyScopes();
    if (!comparisons.length) comparisons = scopes.map(function (scope) {
      return {label:scope.label || scope.color, color:scope.color,
        enemy_size_avg:(scope.aggregate || {}).enemy_size_avg,
        enemy_size_max:(scope.aggregate || {}).enemy_size_max,
        professions:(scope.aggregate || {}).professions};
    });
    return "<div class=\"comparison-banner\"><b>All opponents is comparison-only.</b> " +
      "Enemy colors and distinct groups are never blended into one fake Subgroup grid.</div>" +
      (comparisons.length ? "<div class=\"comparison-grid\">" + comparisons.map(function (item) {
        return "<article class=\"intel-card color-card\" data-color=\"" +
          esc(String(item.color || item.label || "unknown").toLowerCase()) + "\"><h3>" +
          esc(item.label || item.color || "Opponent") + "</h3><p><b>" +
          fmt(item.enemy_size_avg) + "</b> average · <b>" + fmt(item.enemy_size_max) +
          "</b> max</p>" + chips(item.professions, "profession", "count", 8) +
          "</article>";
      }).join("") + "</div>" : "<p class=\"empty\">No color comparison was available.</p>") +
      pressurePanels(null);
  }
  function partyGrid(fight) {
    var groups = fight && fight.estimated_subgroups || [];
    if (!groups.length) return "<p class=\"empty\">No estimated Subgroup reconstruction was available.</p>";
    return "<div class=\"evidence-key\"><span><i class=\"observed\"></i>Observed profession</span>" +
      "<span><i class=\"inferred\"></i>Inferred placement</span><span><i class=\"unknown\"></i>Unknown</span></div>" +
      "<div class=\"party-grid\">" + groups.map(function (party, i) {
        var slots = (party.members || []).slice(0, 5).map(function(member){
          return {member:member, state:member.evidence || (member.observed ? "observed" : "inferred")};
        });
        var unknown = Number(party.unknown_slots || party.open_slots || 0);
        while (slots.length < 5 && unknown-- > 0) slots.push({member:{profession:"Unknown",role:"Unknown"},state:"unknown"});
        while (slots.length < 5) slots.push({member:{profession:"Open slot",role:"Unoccupied"},state:"open"});
        var partyNumber = party.party || party.index || i + 1;
        var members = slots.map(function(slot, slotIndex){
          var member=slot.member || {}, profession=member.profession || "Unknown";
          var role=member.role || member.inferred_role || member.primary_role ||
            (member.role_profile && member.role_profile.label) || "Role unknown";
          var roleKind=roleClass(role), roleText=roleDisplay(role), evidence=slot.state;
          var detail="Slot " + (slotIndex + 1) + " · " + roleText +
            " · " + (evidence === "observed" ? "observed profession" :
              evidence === "open" ? "unoccupied capacity" : "inferred from observed frequency and output");
          return "<button type=\"button\" class=\"party-slot " + esc(evidence) + "\"" +
            drillAttrs("party-slot", "Subgroup " + partyNumber + " · " + profession, detail,
              confidenceLabel(member.confidence || party.confidence)) +
            " aria-label=\"Subgroup " + esc(partyNumber) + " slot " + (slotIndex + 1) +
            ": " + esc(profession) + ", " + esc(roleText) + "\">" +
            professionGlyph(profession) + "<span class=\"slot-copy\"><b>" + esc(profession) +
            "</b><small>" + esc(evidence === "observed" ? "Observed class" :
              evidence === "open" ? "Open" : "Estimated slot") + "</small></span>" +
            "<span class=\"role-badge role-" + roleKind + "\">" +
            esc(roleText) + "</span></button>";
        }).join("");
        return "<article class=\"party\"><header><b>Subgroup " +
          fmt(partyNumber) + "</b><span>" +
          esc(confidenceLabel(party.confidence)) + " confidence</span></header>" +
          "<div class=\"party-slots\" aria-label=\"Subgroup " + esc(partyNumber) +
          " five-player slots\">" + members + "</div></article>";
      }).join("") + "</div><p class=\"accuracy\"><b>Estimated Enemy Squad Composition:</b> " +
      "profession counts are observed where available; five-player subgroup placement and roles " +
      "are inferred. This is a best-fit reconstruction, not hidden squad data.</p>";
  }
  function enemyIntelShell() {
    var scopes = enemyScopes();
    var options = scopes.map(function (scope) {
      return "<option value=\"" + esc(scope.id || scope.color) + "\">" +
        esc(scope.label || scope.color) + "</option>";
    }).join("");
    var ai = model.enemy_intel && model.enemy_intel.ai_analysis;
    return sectionHead("Opponent analysis", "Enemy Intel",
      "What they brought, what hit us, and the patterns that worked against us.") +
      enemyCoverage() + "<div class=\"intel-controls\"><label>Opponent scope<select " +
      "id=\"enemy-color\"><option value=\"all\">All opponents</option>" + options +
      "</select></label><label>Group or fight<select id=\"enemy-detail\" disabled>" +
      "<option value=\"summary\">Color summary</option></select></label></div>" +
      "<div id=\"enemy-panel\">" + comparisonView() + "</div>" +
      (ai ? "<article class=\"ai-read\"><span class=\"eyebrow\">Optional analysis</span>" +
        "<h2>AI Enemy Read</h2><p>" + esc(ai.summary || ai.text || ai) +
        "</p><small>Embedded when the report was generated. Opening this file makes no model call.</small></article>" : "");
  }
  function renderSparky() {
    var boards = allBoards();
    var overview = subnav("overview", [["summary","Night Summary"],["timeline","Fight Timeline"]]) +
      subpanel("overview", "summary", enemyCoverage() +
        sectionHead("Fight review", "Outcome and high-impact findings", "Fight conversion, losses, incoming damage, and standout players without the table hunt.") +
        nightMvpCards() + outcomeChart() +
        boardGrid([], 8, "No overview boards were found."), false) +
      subpanel("overview", "timeline", sectionHead("Fight by fight", "Night timeline", "Follow momentum and open the detailed rows below.") +
        fightsTable(), true);
    var dps = subnav("dps", [["overview","Overview"],["direct","Power Damage"],["conditions","Condition Damage"],["skills","Skills"],["pressure","Fight Impact"]]) +
      subpanel("dps", "overview", sectionHead("Damage", "DPS overview", "Output, burst, downs, and kills.") + metricBars(["damage","dps","burst","kill","down"], "Top damage and conversion", "damage", 10) + boardGrid(["damage","dps","burst","kill","down"], 12), false) +
      subpanel("dps", "direct", sectionHead("DPS", "Power Damage", "Power Damage and burst output.") + metricBars(["power","direct","burst"], "Power Damage", "power", 10) + boardGrid(["power","direct","burst"], 15), true) +
      subpanel("dps", "conditions", sectionHead("DPS", "Condition Damage", "Condition Damage and poison application evidence.") + metricBars(["condition","condi"], "Condition Damage", "condition", 10) + poisonContext() + poisonTable() + boardGrid(["condition","condi"], 15), true) +
      subpanel("dps", "skills", sectionHead("Execution", "Damage by skill", "Which abilities actually produced the output.") + metricBars(["skill","ability"], "Skill damage", "damage", 10) + boardGrid(["skill","ability"], 20), true) +
      subpanel("dps", "pressure", sectionHead("Conversion", "Fight Impact", "Damage that converted into enemy downs and kills.") + metricBars(["down","kill","pressure"], "Damage conversion", "danger", 10) + boardGrid(["down","kill","pressure"], 20), true);
    var support = subnav("support", [["overview","Overview"],["cleanses","Cleanses"],["strips","Boon Strips & Crowd Control"],["boons","Boon Support"],["res","Resurrects"]]) +
      subpanel("support", "overview", sectionHead("Squad utility", "Support overview", "Cleanses, Boon Strips, Crowd Control, Boon Support, and revives.") + metricBars(["cleanse","strip","boon","support","res","cc"], "Support impact", "support", 10) + boardGrid(["cleanse","strip","boon","support","res","cc"], 12), false) +
      subpanel("support", "cleanses", metricBars(["cleanse"], "Cleanse output", "support", 10) + boardGrid(["cleanse"], 20), true) +
      subpanel("support", "strips", metricBars(["strip","cc","control"], "Boon Strips and Crowd Control", "control", 10) + boardGrid(["strip","cc","control"], 20), true) +
      subpanel("support", "boons", metricBars(["boon","quickness","stability","alacrity"], "Boon Support Uptime", "support", 10) + boardGrid(["boon","quickness","stability","alacrity"], 20), true) +
      subpanel("support", "res", metricBars(["res","revive","rally"], "Resurrection output", "heal", 10) + boardGrid(["res","revive","rally"], 20), true);
    var healing = subnav("healing", [["overview","Overview"],["barrier","Healing & Barrier"],["profiles","Profiles"],["skills","By Skill / Target"]]) +
      subpanel("healing", "overview", sectionHead("Sustain", "Healing overview", "Healing, barrier, and survival impact.") + metricBars(["heal","barrier","shield"], "Sustain impact", "heal", 10) + boardGrid(["heal","barrier","shield"], 15), false) +
      subpanel("healing", "barrier", metricBars(["heal","barrier","shield"], "Healing and barrier", "heal", 10) + boardGrid(["heal","barrier","shield"], 25), true) +
      subpanel("healing", "profiles", metricBars(["hps","heal","support"], "Healing profiles", "heal", 10) + boardGrid(["hps","heal","support"], 25), true) +
      subpanel("healing", "skills", metricBars(["heal skill","healing skill","target"], "Healing by skill or target", "heal", 10) + boardGrid(["heal skill","healing skill","target"], 25), true);
    var scores = subnav("scores", [["all","All"],["offense","Offense"],["support","Support"],["healing","Healing"],["defense","Defense"],["leaderboards","Long-term Leaderboards"]]) +
      subpanel("scores", "all", highScoreGrid([]), false) +
      subpanel("scores", "offense", highScoreGrid(["damage","dps","kill","down","burst"]), true) +
      subpanel("scores", "support", highScoreGrid(["cleanse","strip","boon","cc","res"]), true) +
      subpanel("scores", "healing", highScoreGrid(["heal","barrier"]), true) +
      subpanel("scores", "defense", highScoreGrid(["defense","damage taken","death","survival"]), true) +
      subpanel("scores", "leaderboards", sectionHead("Historical context", "Long-term Leaderboards", "Averages across raids; never presented as tonight’s performance.") + longTermLeaderboardGrid(), true);
    var details = subnav("details", [["fights","Fights"],["players","Players"],["attendance","Attendance"],["composition","Composition"],["tables","All Tables"]]) +
      subpanel("details", "fights", fightsTable(), false) +
      subpanel("details", "players", "<label class=\"search\">Filter players<input id=\"player-filter\" placeholder=\"Name, account, class…\"></label>" + boardGrid([], 50), true) +
      subpanel("details", "attendance", boardGrid(["attendance","fight attendance"], 50), true) +
      subpanel("details", "composition", squadCards() || "<p class=\"empty\">No squad composition was found.</p>", true) +
      subpanel("details", "tables", "<div id=\"all-boards\">" + sourceBoardGrid() +
        "</div>" + highScoreGrid([]) + poisonTable(), true);
    var body = "<div class=\"session-strip\"><b>Sparky Pro</b><span>" +
      esc((model.session || {}).date || "Night report") + "</span><span>" +
      "Combat · " + esc(humanDuration((model.session || {}).total_duration) || "unavailable") +
      "</span><span>Offline · deterministic</span></div>" +
      reportHeading("Pro · fight review") + totalsCards(false) +
      "<nav class=\"tabs\" aria-label=\"Pro report views\" role=\"tablist\">" +
      [["overview","Overview"],["dps","DPS"],["support","Support"],["healing","Healing"],
       ["scores","High Scores"],["enemy","Enemy Intel"],["details","Details / Fights"]].map(function (item, i) {
        return "<button type=\"button\" role=\"tab\" aria-selected=\"" +
          (i ? "false" : "true") + "\" class=\"" + (i ? "" : "selected") +
          "\" data-tab=\"" + item[0] + "\">" + item[1] + "</button>";
      }).join("") + "</nav>" +
      "<section data-section=\"overview\">" + overview + "</section>" +
      "<section data-section=\"dps\" hidden>" + dps + "</section>" +
      "<section data-section=\"support\" hidden>" + support + "</section>" +
      "<section data-section=\"healing\" hidden>" + healing + "</section>" +
      "<section data-section=\"scores\" hidden>" + scores + "</section>" +
      "<section data-section=\"enemy\" hidden>" + enemyIntelShell() + "</section>" +
      "<section data-section=\"details\" hidden>" + details +
      "<div class=\"source-callout\"><b>Need the untouched upstream report?</b>" +
      "<button id=\"open-classic\">Open Classic</button></div></section>" +
      "<dialog id=\"drilldown\" aria-labelledby=\"drill-title\"><div class=\"drill-head\">" +
      "<div><span class=\"eyebrow\">Evidence drill-down</span><h2 id=\"drill-title\">Details</h2></div>" +
      "<button type=\"button\" id=\"drill-close\" aria-label=\"Close details\">Close</button></div>" +
      "<div class=\"drill-body\"><p id=\"drill-detail\"></p><div class=\"drill-evidence\" " +
      "id=\"drill-evidence\"></div><small>No network call is made; this evidence is embedded in the report.</small></div></dialog>";
    var extraCss = [
      ".session-strip{position:sticky;top:0;z-index:5;display:flex;gap:18px;align-items:center;",
      "padding:9px 14px;margin:-30px -24px 24px;background:var(--surface);border-bottom:1px solid var(--line);",
      "color:var(--muted);font-size:12px}.session-strip b{color:var(--text)}",
      ".tabs{position:sticky;top:36px;z-index:4;display:flex;gap:4px;margin:24px 0 16px;",
      "padding:6px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}",
      ".tabs button,.subtabs button,.source-callout button{border:1px solid transparent;border-radius:7px;",
      "padding:9px 12px;background:transparent;color:var(--muted);font:inherit;font-weight:700;cursor:pointer}",
      ".tabs button:hover,.subtabs button:hover{color:var(--text);border-color:var(--line)}",
      ".tabs button.selected{background:var(--accent);color:var(--on-accent)}",
      ".subtabs{display:flex;gap:6px;overflow:auto;margin:0 0 20px;border-bottom:1px solid var(--line);padding:0 0 8px}",
      ".subtabs button.selected{color:var(--accent);background:var(--panel);border-color:var(--line)}",
      ".section-head{display:flex;align-items:end;justify-content:space-between;gap:20px;margin:28px 0 14px}",
      ".section-head h2{font-size:24px;margin:4px 0 0}.section-head p{color:var(--muted);max-width:520px;margin:0}",
      ".table-panel{border:1px solid var(--line);border-radius:10px;background:var(--panel)}",
      ".search{display:block;color:var(--muted);margin:12px 0}.search input,.intel-controls select{display:block;",
      "margin-top:5px;min-width:240px;border:1px solid var(--line);border-radius:7px;padding:9px 11px;",
      "background:var(--panel);color:var(--text);font:inherit}",
      ".squad-grid,.intel-grid,.comparison-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}",
      ".squad,.intel-card,.chart-card,.ai-read{border:1px solid var(--line);border-radius:10px;padding:15px;background:var(--panel)}",
      ".squad h3,.intel-card h3{margin:0 0 10px}.squad span{display:block;color:var(--muted);font-size:12px}",
      ".spotlight{display:grid;grid-template-columns:1.6fr 1fr;gap:20px;padding:22px;border:1px solid var(--line);",
      "border-left:4px solid var(--good);border-radius:10px;background:var(--panel);margin:18px 0}",
      ".spotlight h2{font-size:28px;margin:5px 0}.spotlight p{color:var(--muted)}.spot-metrics{display:grid;gap:10px}",
      ".spot-metrics div,.intel-kpis div{padding:13px;background:var(--panel-2);border-radius:8px}",
      ".spot-metrics strong,.intel-kpis strong{display:block;font-size:22px;color:var(--accent)}",
      ".spot-metrics span,.intel-kpis span{color:var(--muted);font-size:12px}.accuracy{font-size:12px;color:var(--muted)}",
      ".chart-title{display:flex;justify-content:space-between;color:var(--muted)}.chart-title b{color:var(--text)}",
      ".svg-scroll{overflow:auto}.chart-card{margin:18px 0 30px}.chart-card svg{width:100%;min-width:560px;height:210px}.axis{stroke:var(--line)}",
      ".series-kills{fill:var(--series-kills)}.series-downs{fill:var(--series-downs)}.series-our-downs{fill:var(--series-our-downs)}",
      ".series-deaths{fill:var(--series-deaths)}.chart-legend{display:flex;flex-wrap:wrap;gap:14px;margin:12px 0;color:var(--muted);font-size:12px}",
      ".chart-legend span:before{content:'';display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;background:var(--accent)}",
      ".chart-legend .key-kills:before{background:var(--series-kills)}.chart-legend .key-downs:before{background:var(--series-downs)}",
      ".chart-legend .key-our-downs:before{background:var(--series-our-downs)}.chart-legend .key-deaths:before{background:var(--series-deaths)}",
      ".bar-card{padding:16px;border:1px solid var(--line);border-top:3px solid var(--accent);border-radius:10px;background:var(--panel);margin:16px 0 30px}",
      ".tone-damage,.tone-power{border-top-color:var(--role-dps)}.tone-condition{border-top-color:var(--purple)}.tone-support,.tone-control{border-top-color:var(--role-support)}",
      ".tone-heal{border-top-color:var(--role-heal)}.tone-danger,.tone-strip{border-top-color:var(--series-deaths)}",
      ".metric-row{display:grid;grid-template-columns:minmax(120px,220px) minmax(100px,1fr) auto;gap:12px;align-items:center;width:100%;",
      "padding:8px 0;border:0;border-bottom:1px solid var(--line-soft);background:transparent;color:var(--text);font:inherit;text-align:left;cursor:pointer}",
      ".metric-row i{height:9px;border-radius:2px;background:var(--panel-2);overflow:hidden}.metric-row em{display:block;height:100%;background:var(--accent)}",
      ".tone-damage .metric-row em,.tone-power .metric-row em{background:var(--role-dps)}.tone-condition .metric-row em{background:var(--purple)}",
      ".tone-support .metric-row em,.tone-control .metric-row em{background:var(--role-support)}.tone-heal .metric-row em{background:var(--role-heal)}",
      ".tone-danger .metric-row em,.tone-strip .metric-row em{background:var(--series-deaths)}.bar-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".heatmap{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:7px;padding:15px;border:1px solid var(--line);border-top:3px solid var(--purple);",
      "border-radius:10px;background:var(--panel);margin:16px 0 30px}.heat-cell{min-height:68px;padding:10px;border:1px solid color-mix(in srgb,var(--purple) 45%,var(--line));",
      "border-radius:7px;background:color-mix(in srgb,var(--purple) calc(var(--heat)*55%),var(--panel-2));color:var(--text);font:inherit;text-align:left;cursor:pointer}",
      ".heat-cell b,.heat-cell span{display:block}.heat-cell span{color:var(--muted);font-size:11px;margin-top:4px}.intel-visuals{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:16px 0 28px}",
      ".intel-visuals .bar-card,.intel-visuals .heatmap{margin:0}.inference-note{padding:10px;border-left:3px solid var(--accent-2);background:var(--panel-2);color:var(--muted)}",
      ".coverage,.intel-controls,.intel-kpis{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.coverage span{padding:9px 12px;",
      "border:1px solid var(--line);border-radius:999px;color:var(--muted)}.coverage b{color:var(--text)}",
      ".intel-controls label{color:var(--muted);font-size:12px}.intel-kpis{display:grid;grid-template-columns:repeat(3,1fr)}",
      ".wide{margin:12px 0}.chips{display:flex;flex-wrap:wrap;gap:7px}.chips span{padding:6px 9px;background:var(--panel-2);",
      "border:1px solid var(--line-soft);border-radius:999px;color:var(--muted);font-size:12px}.chips b{color:var(--text)}",
      ".scope-note,.muted{color:var(--muted)}.comparison-banner{padding:13px;border-left:4px solid var(--accent-2);",
      ".detail-counts{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.detail-counts span{padding:8px 10px;",
      "border:1px solid var(--line);border-radius:7px;color:var(--muted)}.detail-counts b{color:var(--text)}",
      "background:var(--panel);margin:12px 0}.party-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}",
      ".party{border:1px solid var(--line);border-radius:10px;background:var(--panel);overflow:hidden}.party header{display:flex;",
      "justify-content:space-between;padding:10px 12px;border-bottom:1px solid var(--line)}.party header span{color:var(--muted);font-size:11px}",
      ".party-slots{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px;padding:9px}.party-slot{position:relative;min-width:0;padding:8px 5px 7px;border:1px solid var(--line-soft);",
      "border-bottom:3px solid var(--accent-2);border-radius:7px;background:var(--panel-2);color:var(--text);font:inherit;text-align:center;cursor:pointer}.party-slot.observed{border-bottom-color:var(--good)}",
      ".party-slot.unknown,.party-slot.open{border-bottom-color:var(--faint);opacity:.72}.profession-glyph{display:grid;place-items:center;width:38px;height:38px;margin:0 auto 5px;color:var(--faint)}",
      ".profession-glyph svg,.profession-icon{width:100%;height:100%;object-fit:contain}.profession-glyph path{fill:currentColor}.profession-glyph text{fill:var(--bg);font-size:7px;font-weight:900;text-anchor:middle}",
      ".profession-glyph[data-prof-base=guardian]{color:var(--guardian)}.profession-glyph[data-prof-base=warrior]{color:var(--warrior)}.profession-glyph[data-prof-base=revenant]{color:var(--revenant)}",
      ".profession-glyph[data-prof-base=ranger]{color:var(--ranger)}.profession-glyph[data-prof-base=thief]{color:var(--thief)}.profession-glyph[data-prof-base=engineer]{color:var(--engineer)}",
      ".profession-glyph[data-prof-base=elementalist]{color:var(--elementalist)}.profession-glyph[data-prof-base=mesmer]{color:var(--mesmer)}.profession-glyph[data-prof-base=necromancer]{color:var(--necromancer)}",
      ".slot-copy b,.slot-copy small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.slot-copy b{font-size:10px}.slot-copy small{color:var(--muted);font-size:9px}",
      ".role-badge{display:block;margin-top:5px;padding:2px 3px;border-radius:3px;background:var(--faint);color:#020305;font-size:8px;font-weight:900;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".role-dps{background:var(--role-dps)}.role-heal{background:var(--role-heal)}.role-support{background:var(--role-support)}.role-hybrid{background:var(--role-hybrid)}",
      ".evidence-key{display:flex;gap:14px;color:var(--muted);font-size:12px;margin:12px 0}.evidence-key i{display:inline-block;width:8px;height:8px;",
      "border-radius:50%;margin-right:5px;background:var(--good)}.evidence-key i.inferred{background:var(--accent-2)}.evidence-key i.unknown{background:var(--faint)}",
      ".history-callout{display:flex;gap:10px;padding:12px;margin-bottom:14px;border-left:4px solid var(--accent-2);background:var(--panel);color:var(--muted)}.history-callout b{color:var(--text)}",
      ".mvp-section{margin:20px 0 30px}.mvp-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.mvp-card{min-width:0;padding:15px;border:1px solid var(--line);",
      "border-top:3px solid var(--role-support);border-radius:9px;background:var(--panel);color:var(--text);font:inherit;text-align:left;cursor:pointer}.mvp-card:nth-child(3n+1){border-top-color:var(--role-dps)}",
      ".mvp-card:nth-child(3n+2){border-top-color:var(--role-heal)}.mvp-card>span,.mvp-card small{display:block;color:var(--muted);font-size:11px}.mvp-card>b{display:block;font-size:16px;margin:5px 0}",
      ".mvp-card>strong{display:block;color:var(--accent);font-size:23px}.mvp-card p{color:var(--muted);font-size:11px;margin:8px 0 0}",
      ".ai-read{margin-top:18px;border-left:4px solid var(--purple)}.ai-read small{color:var(--muted)}",
      "dialog#drilldown{width:min(620px,calc(100vw - 28px));border:1px solid var(--line);border-top:4px solid var(--accent);border-radius:12px;padding:0;background:var(--panel);color:var(--text)}",
      "dialog#drilldown::backdrop{background:rgba(0,0,0,.74)}.drill-head{display:flex;justify-content:space-between;gap:14px;padding:17px 18px;border-bottom:1px solid var(--line)}",
      ".drill-head h2{margin:0;font-size:20px}.drill-head button{border:1px solid var(--line);border-radius:6px;background:var(--panel-2);color:var(--text);cursor:pointer}.drill-body{padding:18px}",
      ".drill-body p{font-size:16px}.drill-evidence{padding:10px;border-left:3px solid var(--accent-2);background:var(--panel-2);color:var(--muted)}",
      ".source-callout{display:flex;justify-content:space-between;align-items:center;padding:18px;margin-top:22px;border:1px solid var(--line);",
      "border-radius:10px;background:var(--panel)}.source-callout button{background:var(--accent);color:var(--on-accent)}",
      "@media(max-width:800px){.tabs{overflow:auto}.section-head,.spotlight{display:block}.party-grid,.intel-visuals{grid-template-columns:1fr}.mvp-grid{grid-template-columns:repeat(2,1fr)}}",
      "@media(max-width:600px){.session-strip{margin:-18px -12px 18px;overflow:auto}.tabs{top:34px}.intel-grid,.comparison-grid,.squad-grid,",
      ".party-grid,.intel-kpis,.mvp-grid{grid-template-columns:1fr}.search input,.intel-controls select{min-width:0;width:100%}.metric-row{grid-template-columns:minmax(90px,150px) 1fr auto}}",
      "@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}"
    ].join("");
    return "<!doctype html><html data-theme=\"" + esc(currentTheme) +
      "\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
      "<title>Sparky Pro report</title><style>" + commonCss + extraCss +
      "</style></head><body><main class=\"wrap\">" + body + "</main></body></html>";
  }
  function openDrilldown(doc, trigger) {
    var dialog=doc.getElementById("drilldown");
    if (!dialog || !trigger) return;
    doc.getElementById("drill-title").textContent=trigger.getAttribute("data-drill-title") || "Details";
    doc.getElementById("drill-detail").textContent=trigger.getAttribute("data-drill-body") || "No additional numeric detail was exported.";
    doc.getElementById("drill-evidence").textContent=trigger.getAttribute("data-drill-evidence") || "Report evidence";
    doc.__drillTrigger=trigger;
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
  }
  function wireSparky(doc) {
    doc.addEventListener("click", function(event){
      var trigger=event.target.closest("[data-drill]");
      if (trigger) openDrilldown(doc, trigger);
    });
    doc.addEventListener("keydown", function(event){
      if (event.key === "Escape") {
        var dialog=doc.getElementById("drilldown");
        if (dialog && dialog.open) dialog.close();
      }
      if ((event.key === "Enter" || event.key === " ") && event.target.matches("[data-drill]:not(button)")) {
        event.preventDefault(); openDrilldown(doc, event.target);
      }
    });
    var drill=doc.getElementById("drilldown"), drillClose=doc.getElementById("drill-close");
    if (drillClose) drillClose.addEventListener("click", function(){drill.close();});
    if (drill) drill.addEventListener("close", function(){
      if (doc.__drillTrigger && typeof doc.__drillTrigger.focus === "function") doc.__drillTrigger.focus();
    });
    Array.from(doc.querySelectorAll("[data-expand-board]")).forEach(function(button){
      button.addEventListener("click", function(){
        var expanded=button.getAttribute("aria-expanded") === "true";
        Array.from(button.closest(".board").querySelectorAll(".board-extra")).forEach(function(row){row.hidden=expanded;});
        button.setAttribute("aria-expanded", expanded ? "false" : "true");
        button.textContent=expanded ? "Expand all " + button.closest(".board").querySelectorAll("tbody tr").length : "Collapse";
      });
    });
    Array.from(doc.querySelectorAll("[data-sort-key]")).forEach(function(button){
      button.addEventListener("click", function(){
        var table=button.closest("table"), tbody=table.querySelector("tbody"), key=button.getAttribute("data-sort-key");
        var rows=Array.from(tbody.querySelectorAll("tr"));
        rows.sort(function(a,b){return (Number(b.getAttribute("data-"+key))||0)-(Number(a.getAttribute("data-"+key))||0);});
        var expand=table.closest(".board").querySelector("[data-expand-board]");
        var expanded=expand && expand.getAttribute("aria-expanded") === "true";
        rows.forEach(function(row,index){tbody.appendChild(row); row.hidden=!expanded && index>=5;});
      });
    });
    Array.from(doc.querySelectorAll("[data-tab]")).forEach(function (button) {
      button.addEventListener("click", function () {
        Array.from(doc.querySelectorAll("[data-tab]")).forEach(function (b) {
          b.classList.toggle("selected", b === button);
          b.setAttribute("aria-selected", b === button ? "true" : "false");
        });
        Array.from(doc.querySelectorAll("[data-section]")).forEach(function (s) {
          s.hidden = s.getAttribute("data-section") !== button.getAttribute("data-tab");
        });
        doc.defaultView.scrollTo(0, 0);
      });
    });
    Array.from(doc.querySelectorAll("[data-subnav]")).forEach(function (nav) {
      var group = nav.getAttribute("data-subnav");
      Array.from(nav.querySelectorAll("[data-subtab]")).forEach(function (button) {
        button.addEventListener("click", function () {
          Array.from(nav.querySelectorAll("[data-subtab]")).forEach(function (b) {
            b.classList.toggle("selected", b === button);
            b.setAttribute("aria-selected", b === button ? "true" : "false");
          });
          Array.from(doc.querySelectorAll("[data-subsection=\"" + group + "\"]"))
            .forEach(function (section) {
              section.hidden = section.getAttribute("data-subview") !==
                button.getAttribute("data-subtab");
            });
        });
      });
    });
    var openClassic = doc.getElementById("open-classic");
    if (openClassic) openClassic.addEventListener("click", function () {
      show("classic");
    });
    var filter = doc.getElementById("player-filter");
    if (filter) filter.addEventListener("input", function () {
      var wanted = filter.value.toLowerCase();
      Array.from(doc.querySelectorAll("[data-subview=players] tbody tr"))
        .forEach(function (row) {
        row.hidden = wanted && row.textContent.toLowerCase().indexOf(wanted) < 0;
      });
    });
    var colorSelect = doc.getElementById("enemy-color");
    var detailSelect = doc.getElementById("enemy-detail");
    var panel = doc.getElementById("enemy-panel");
    function selectedEnemyScope() {
      return enemyScopes().filter(function (scope) {
        return String(scope.id || scope.color) === colorSelect.value;
      })[0];
    }
    function fillEnemyDetails(scope) {
      detailSelect.innerHTML = "<option value=\"summary\">Color summary</option>";
      (scope.groups || []).forEach(function (group) {
        var option = doc.createElement("option");
        option.value = "group:" + String(group.id || group.label);
        option.textContent = group.label || group.id || "Detected group";
        detailSelect.appendChild(option);
      });
      var indexes = scope.fight_indexes || [];
      (model.enemy_intel && model.enemy_intel.fights || []).filter(function (fight) {
        return String(fight.color || "").toLowerCase() ===
          String(scope.color || "").toLowerCase() &&
          (!indexes.length || indexes.indexOf(fight.index) >= 0);
      }).forEach(function (fight) {
        var option = doc.createElement("option");
        option.value = "fight:" + String(fight.index);
        option.textContent = "Fight " + String(fight.index) + " · " +
          String(fight.enemy_count || "?") + " enemies";
        detailSelect.appendChild(option);
      });
    }
    function drawEnemy() {
      if (!panel || !colorSelect || !detailSelect) return;
      if (colorSelect.value === "all") {
        detailSelect.disabled = true;
        panel.innerHTML = comparisonView();
        return;
      }
      var scope = selectedEnemyScope();
      if (!scope) {
        panel.innerHTML = "<p class=\"empty\">This opponent scope was unavailable.</p>";
        return;
      }
      detailSelect.disabled = false;
      var value = detailSelect.value;
      if (value === "summary") {
        panel.innerHTML = scopeSummary(scope);
      } else if (value.indexOf("group:") === 0) {
        var id = value.slice(6);
        var group = (scope.groups || []).filter(function (item) {
          return String(item.id || item.label) === id;
        })[0] || {};
        panel.innerHTML = "<div class=\"comparison-banner\"><b>" +
          esc(group.label || "Detected group") + "</b> · fights " +
          esc((group.fight_indexes || []).join(", ") || "unavailable") +
          "</div><p class=\"accuracy\">Grouping method: " +
          esc(group.grouping_method || group.cohort_status || "best available evidence") +
          ". Subgroup estimates remain fight-specific until a recurring cohort is proven.</p>" +
          scopeSummary(scope);
      } else {
        var index = value.slice(6);
        var fight = (model.enemy_intel && model.enemy_intel.fights || []).filter(function (item) {
          return String(item.index) === index && String(item.color || "").toLowerCase() ===
            String(scope.color || "").toLowerCase();
        })[0];
        panel.innerHTML = fight ? "<div class=\"intel-kpis\"><div><strong>" +
          fmt(fight.enemy_count) + "</strong><span>observed enemy count</span></div>" +
          "<div><strong>" + fmt(fight.observed_profession_count) +
          "</strong><span>identified professions</span></div><div><strong>" +
          esc(confidenceLabel(fight.confidence)) + "</strong><span>reconstruction confidence</span></div></div>" +
          "<article class=\"intel-card wide\"><h3>Observed composition</h3>" +
          chips(fight.professions, "profession", "count", 40) + "</article>" +
          partyGrid(fight) + pressurePanels(scope) :
          "<p class=\"empty\">Fight composition was unavailable.</p>";
      }
    }
    if (colorSelect && detailSelect && panel) {
      colorSelect.addEventListener("change", function () {
        if (colorSelect.value === "all") {
          detailSelect.innerHTML = "<option value=\"summary\">Comparison only</option>";
        } else {
          fillEnemyDetails(selectedEnemyScope() || {});
        }
        drawEnemy();
      });
      detailSelect.addEventListener("change", drawEnemy);
    }
  }
  function documentFor(view) {
    if (!docs[view]) {
      docs[view] = view === "classic" ? classicHtml :
        (view === "simple" ? renderSimple() : renderSparky());
    }
    return docs[view];
  }
  function show(view) {
    if (views.indexOf(view) < 0) view = settings.defaultView;
    currentView = view;
    Array.from(document.querySelectorAll("[data-view]")).forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-view") === view);
    });
    frame.onload = function () {
      try { frame.contentDocument.documentElement.setAttribute("data-theme", currentTheme); }
      catch (_error) {}
      if (currentView === "sparky") {
        try { wireSparky(frame.contentDocument); } catch (_error) {}
      }
    };
    frame.srcdoc = documentFor(view);
    frame.hidden = false;
    opening.hidden = true;
    meta.textContent = view === "classic" ? "Untouched source report" :
      (view === "simple" ? "Fast headline view" : "Complete guided view");
    try { localStorage.setItem("sparkybot-report-view", view); } catch (_error) {}
    try { history.replaceState(null, "", "#view=" + view); } catch (_error) {}
  }
  Array.from(document.querySelectorAll("[data-view]")).forEach(function (button) {
    button.addEventListener("click", function () {
      show(button.getAttribute("data-view"));
    });
  });
  var themePicker = document.getElementById("theme-picker");
  if (themePicker) themePicker.addEventListener("change", function () {
    applyTheme(themePicker.value);
  });
  try { currentTheme = localStorage.getItem("sparkybot-report-theme") || currentTheme; }
  catch (_error) {}
  applyTheme(currentTheme);

  try {
    if (typeof DecompressionStream === "undefined") {
      throw new Error("Use a current Chrome, Edge, Firefox, or Safari browser.");
    }
    var payload = document.getElementById("classic-payload");
    var binary = atob(payload.textContent);
    payload.textContent = "";
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    var stream = new Blob([bytes]).stream()
      .pipeThrough(new DecompressionStream("gzip"));
    classicHtml = await new Response(stream).text();
    binary = "";
    bytes = null;

    var preferred = settings.defaultView;
    try {
      var remembered = localStorage.getItem("sparkybot-report-view");
      if (views.indexOf(remembered) >= 0) preferred = remembered;
    } catch (_error) {}
    var match = /[#&]view=(sparky|simple|classic)/.exec(location.hash || "");
    if (match) preferred = match[1];
    show(preferred);
  } catch (error) {
    opening.className = "error";
    opening.textContent = "Could not open this report: " + error.message;
  }
})();
</script>
</body>
</html>
"""


def build_switchable_report(
    classic_html: str,
    night_model: dict[str, Any],
    *,
    default_view: str = DEFAULT_REPORT_VIEW,
) -> str:
    """Return one offline report containing Classic, Simple, and Sparky views."""
    default_view = normalize_report_view(default_view)
    title_match = _TITLE_RE.search(classic_html)
    title = (
        html_escape.unescape(title_match.group(1).strip())
        if title_match
        else "SparkyBot Night Report"
    )
    payload = base64.b64encode(
        gzip.compress(classic_html.encode("utf-8"), 9)
    ).decode("ascii")
    settings_json = json.dumps(
        {"defaultView": default_view}, separators=(",", ":")
    )
    model_json = json.dumps(
        night_model, ensure_ascii=False, separators=(",", ":")
    ).replace("<", "\\u003c")
    replacements = {
        "__TITLE__": html_escape.escape(title),
        "__SETTINGS__": settings_json,
        "__MODEL__": model_json,
        "__PAYLOAD__": payload,
    }
    return re.sub(
        r"__(?:TITLE|SETTINGS|MODEL|PAYLOAD)__",
        lambda match: replacements[match.group(0)],
        _SHELL_TEMPLATE,
    )


def unpack_classic_report(report: str) -> str:
    """Extract the untouched Classic HTML from a switchable report."""
    match = _PAYLOAD_RE.search(report)
    if not match:
        raise ValueError("not a SparkyBot switchable report")
    try:
        return gzip.decompress(base64.b64decode(match.group(1), validate=True)).decode(
            "utf-8"
        )
    except (EOFError, OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("switchable report payload is corrupt") from exc


def convert_report_file(
    report_path: Path,
    tiddlers: Iterable[dict[str, Any]],
    *,
    default_view: str = DEFAULT_REPORT_VIEW,
) -> Path:
    """Atomically replace a packed/raw upstream report with the three-view shell."""
    path = Path(report_path)
    source = path.read_text(encoding="utf-8")
    if _VIEWER_MARKER in source:
        classic_html = unpack_classic_report(source)
    elif is_packed(source):
        classic_html = unpack_html(source)
    else:
        classic_html = source
    selected_match = _SELECTED_FIGHTS_RE.search(path.name)
    selected_fights = int(selected_match.group(1)) if selected_match else None
    switched = build_switchable_report(
        classic_html,
        build_night_model(list(tiddlers), selected_fights=selected_fights),
        default_view=default_view,
    )

    fd, part_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".part"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(switched)
        os.replace(part_path, path)
        part_path = ""
    finally:
        if part_path:
            try:
                os.unlink(part_path)
            except OSError:
                pass
    return path
