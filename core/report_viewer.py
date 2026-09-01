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
  var themes = ["graphite", "midnight", "studio-light"];
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
  function allBoards() {
    var seen = {};
    return (model.leaderboards || []).concat(model.stat_tables || [])
      .filter(function (board) {
        var key = String(board.stat || "").toLowerCase();
        if (!key || seen[key] || !(board.rows || []).length) return false;
        if (Object.prototype.hasOwnProperty.call(board, "value_label") &&
            !board.value_label) return false;
        seen[key] = true;
        return true;
      });
  }
  function boardCard(board, limit) {
    var rows = (board.rows || []).slice(0, limit || 10);
    var body = rows.map(function (row, index) {
      return "<tr><td class=\"rank\">" + esc(row.rank || index + 1) +
        "</td><td><b>" + esc(row.name) + "</b>" +
        (row.account ? "<small>" + esc(row.account) + "</small>" : "") +
        "</td><td><span class=\"profession\">" +
        esc(row.profession || "—") + "</span></td><td class=\"number\">" +
        fmt(row.value) + "</td></tr>";
    }).join("");
    return "<article class=\"board\"><h3>" + esc(titleCase(board.stat)) +
      "</h3><div class=\"table-wrap\"><table><thead><tr><th>#</th>" +
      "<th>Player</th><th>Class</th><th class=\"number\">" +
      esc(board.value_label || "Score") + "</th>" +
      "</tr></thead><tbody>" + body + "</tbody></table></div></article>";
  }
  function totalsCards(compact) {
    var t = model.totals || {};
    var cards = [
      ["Fights", t.fights],
      ["Enemy downs", t.enemy_downs],
      ["Enemy kills", t.enemy_kills],
      ["Our downs", t.ally_downs],
      ["Our deaths", t.ally_deaths],
      ["K/D", t.kdr]
    ];
    return "<div class=\"kpis" + (compact ? " compact" : "") + "\">" +
      cards.map(function (item) {
        return "<div class=\"kpi\"><strong>" + fmt(item[1]) +
          "</strong><span>" + esc(item[0]) + "</span></div>";
      }).join("") + "</div>";
  }
  function reportHeading(kicker) {
    var s = model.session || {};
    var title = s.commander ? esc(s.commander) + "’s night" : "Night report";
    var bits = [s.date, humanDuration(s.total_duration)].filter(Boolean).map(esc);
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
    "--good:#34c989;--bad:#ff6b71;--purple:#ad8cff;--on-accent:#041215}",
    ":root[data-theme=midnight]{--bg:#090d1b;--surface:#10162a;--panel:#161e35;",
    "--panel-2:#1b2742;--line:#2b3859;--line-soft:#202b48;--text:#f1f4ff;",
    "--muted:#9ca9ca;--faint:#7181aa;--accent:#6d8cff;--accent-2:#ffb65c;",
    "--good:#44d29a;--bad:#ff7183;--purple:#bd8cff;--on-accent:#080c18}",
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
    "border-radius:10px;background:var(--panel)}.kpi strong{display:block;color:var(--accent);",
    "font-size:24px}.kpi span{color:var(--muted);font-size:12px}.boards{display:grid;",
    "grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.board{min-width:0;",
    "border:1px solid var(--line);background:var(--panel);border-radius:10px;overflow:hidden}",
    ".board h3{margin:0;padding:14px 16px;border-bottom:1px solid var(--line);",
    "font-size:15px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}",
    "th,td{padding:8px 11px;border-bottom:1px solid var(--line-soft);text-align:left;",
    "white-space:nowrap}th{color:var(--muted);font-size:11px;text-transform:uppercase;",
    "letter-spacing:.06em}td small{display:block;color:var(--faint)}.rank,.number{",
    "text-align:right;font-variant-numeric:tabular-nums}.profession{color:var(--text)}",
    ".empty{padding:24px;border:1px dashed var(--line);border-radius:10px;color:var(--muted)}",
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
        return "<tr><td class=\"number\">" + fmt(f.index) + "</td><td>" +
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
        return "<tr><td class=\"number\">" + (i + 1) + "</td><td><b>" +
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
  function fightPulse() {
    var fights = model.fights || [];
    if (!fights.length) return "<p class=\"empty\">No fight timeline was available.</p>";
    var width = Math.max(560, fights.length * 25), height = 150;
    var max = Math.max.apply(null, fights.map(function (f) {
      return Number(f.kills || f.downs || 0);
    }).concat([1]));
    var points = fights.map(function (f, i) {
      var x = 15 + i * ((width - 30) / Math.max(1, fights.length - 1));
      var y = height - 22 - (Number(f.kills || f.downs || 0) / max) * 105;
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    return "<div class=\"chart-card\"><div class=\"chart-title\"><b>Fight pulse</b>" +
      "<span>Enemy kills by fight</span></div><div class=\"svg-scroll\"><svg " +
      "viewBox=\"0 0 " + width + " " + height + "\" role=\"img\" " +
      "aria-label=\"Enemy kills across the night\"><line x1=\"15\" y1=\"128\" " +
      "x2=\"" + (width - 15) + "\" y2=\"128\" class=\"axis\"></line>" +
      "<polyline points=\"" + points + "\" class=\"trend\"></polyline>" +
      fights.map(function (f, i) {
        var x = 15 + i * ((width - 30) / Math.max(1, fights.length - 1));
        var y = height - 22 - (Number(f.kills || f.downs || 0) / max) * 105;
        return "<circle cx=\"" + x.toFixed(1) + "\" cy=\"" + y.toFixed(1) +
          "\" r=\"4\"><title>Fight " + esc(f.index || i + 1) + ": " +
          esc(f.kills || f.downs || 0) + "</title></circle>";
      }).join("") + "</svg></div></div>";
  }
  function poisonSpotlight() {
    var rows = (model.poison || []).slice().sort(function (a, b) {
      return Number(b.apps_per_min || 0) - Number(a.apps_per_min || 0);
    });
    var top = rows[0] || {};
    var apps = rows.reduce(function (sum, row) { return sum + Number(row.apps || 0); }, 0);
    return "<article class=\"spotlight poison\"><div><span class=\"eyebrow\">" +
      "Signature pressure</span><h2>Poison Pressure</h2><p>Front-and-center because " +
      "Demon Queen squads care about healing denial and sustained poison coverage.</p>" +
      "<p class=\"accuracy\"><b>Accuracy:</b> logs show poison output and applications; " +
      "they do not prove which applications triggered Demon Queen Relic.</p></div>" +
      "<div class=\"spot-metrics\"><div><strong>" + fmt(apps) +
      "</strong><span>applications</span></div><div><strong>" +
      esc(top.name || "—") + "</strong><span>top pressure · " +
      fmt(top.apps_per_min) + " apps/min</span></div></div></article>";
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
    if (!rows.length) return "<span class=\"muted\">Unavailable</span>";
    return "<div class=\"chips\">" + rows.map(function (row) {
      var label = typeof row === "string" ? row :
        (row[labelKey] || row.name || row.skill || row.profession || "Unknown");
      var value = typeof row === "string" ? "" :
        (row[valueKey] != null ? row[valueKey] : (row.count != null ? row.count : row.damage));
      return "<span><b>" + esc(label) + "</b>" +
        (value == null || value === "" ? "" : " · " + fmt(value)) + "</span>";
    }).join("") + "</div>";
  }
  function enemyScopes() {
    return model.enemy_intel && model.enemy_intel.scopes || [];
  }
  function enemyCoverage() {
    var c = model.enemy_intel && model.enemy_intel.coverage || {};
    var selected = c.selected_fights == null ? "—" : c.selected_fights;
    var reported = c.reported_fights == null ? (c.modeled_fights == null ? "—" : c.modeled_fights) : c.reported_fights;
    return "<div class=\"coverage\"><span><b>" + fmt(selected) + "</b> selected</span>" +
      "<span><b>" + fmt(reported) + "</b> parsed</span><span><b>" +
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
    return "<p class=\"scope-note\">" + (scope && sessionOnly ?
      "All opponents / unavailable by color" : "Observed for this scope") + "</p>" +
      "<div class=\"intel-grid\"><article class=\"intel-card\"><h3>Incoming damage skills</h3>" +
      chips(pressure.top_damage_skills, "skill", "damage", 10) + "</article>" +
      "<article class=\"intel-card\"><h3>Conditions and damage profile</h3>" +
      chips((pressure.conditions_in || []).concat(pressure.debuffs_in || []), "name", "count", 10) +
      (pressure.damage_profile ? chips([pressure.damage_profile], "label", "value", 2) : "") +
      "</article><article class=\"intel-card\"><h3>Incoming strips</h3>" +
      chips(pressure.incoming_strips, "skill", "count", 10) + "</article>" +
      "<article class=\"intel-card\"><h3>Control and pulls</h3>" +
      chips((pressure.cc || []).concat(pressure.pulls || []), "skill", "count", 10) +
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
      "Enemy colors and distinct groups are never blended into one fake party grid.</div>" +
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
    if (!groups.length) return "<p class=\"empty\">No estimated party reconstruction was available.</p>";
    return "<div class=\"evidence-key\"><span><i class=\"observed\"></i>Observed profession</span>" +
      "<span><i class=\"inferred\"></i>Inferred placement</span><span><i class=\"unknown\"></i>Unknown</span></div>" +
      "<div class=\"party-grid\">" + groups.map(function (party, i) {
        var members = (party.members || []).map(function (member) {
          var evidence = member.evidence || (member.observed ? "observed" : "inferred");
          return "<li class=\"" + esc(evidence) + "\"><b>" +
            esc(member.profession || "Unknown") + "</b><span>" +
            esc(member.role || "Role unknown") + "</span><small>" +
            esc(evidence === "observed" ? "Observed" : "Inferred placement") +
            "</small></li>";
        }).join("");
        var unknown = Number(party.unknown_slots || party.open_slots || 0);
        while (unknown-- > 0) members += "<li class=\"unknown\"><b>Unknown</b>" +
          "<span>Unidentified slot</span><small>Unknown</small></li>";
        return "<article class=\"party\"><header><b>Party " +
          fmt(party.party || party.index || i + 1) + "</b><span>" +
          fmt(party.confidence) + " confidence</span></header><ol>" + members +
          "</ol></article>";
      }).join("") + "</div><p class=\"accuracy\"><b>Estimated Enemy Group Comp:</b> " +
      "profession counts are observed where available; five-player party placement and roles " +
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
      subpanel("overview", "summary", poisonSpotlight() +
        sectionHead("Command view", "The important things", "Performance, pressure, and standout players without the table hunt.") +
        boardGrid([], 8, "No overview boards were found."), false) +
      subpanel("overview", "timeline", sectionHead("Fight by fight", "Night timeline", "Follow momentum and open the detailed rows below.") +
        fightPulse() + fightsTable(), true);
    var dps = subnav("dps", [["overview","Overview"],["direct","Direct"],["conditions","Conditions"],["skills","Skills"],["pressure","Pressure"]]) +
      subpanel("dps", "overview", sectionHead("Damage", "DPS overview", "Output, burst, downs, and kills.") + boardGrid(["damage","dps","burst","kill","down"], 12), false) +
      subpanel("dps", "direct", sectionHead("Power", "Direct damage", "Power pressure and burst output.") + boardGrid(["power","direct","burst"], 15), true) +
      subpanel("dps", "conditions", poisonSpotlight() + sectionHead("Condition pressure", "Conditions", "Poison gets its full breakdown here alongside condition output.") + poisonTable() + boardGrid(["condition","condi"], 15), true) +
      subpanel("dps", "skills", sectionHead("Execution", "Damage by skill", "Which abilities actually produced the output.") + boardGrid(["skill","ability"], 20), true) +
      subpanel("dps", "pressure", sectionHead("Conversion", "Downs and pressure", "Damage that converted into downs and kills.") + boardGrid(["down","kill","pressure"], 20), true);
    var support = subnav("support", [["overview","Overview"],["cleanses","Cleanses"],["strips","Strips & CC"],["boons","Boons"],["res","Resurrects"]]) +
      subpanel("support", "overview", sectionHead("Squad utility", "Support overview", "The players who kept the squad functional.") + boardGrid(["cleanse","strip","boon","support","res","cc"], 12), false) +
      subpanel("support", "cleanses", boardGrid(["cleanse"], 20), true) +
      subpanel("support", "strips", boardGrid(["strip","cc","control"], 20), true) +
      subpanel("support", "boons", boardGrid(["boon","quickness","stability","alacrity"], 20), true) +
      subpanel("support", "res", boardGrid(["res","revive","rally"], 20), true);
    var healing = subnav("healing", [["overview","Overview"],["barrier","Healing & Barrier"],["profiles","Profiles"],["skills","By Skill / Target"]]) +
      subpanel("healing", "overview", sectionHead("Sustain", "Healing overview", "Healing, barrier, and survival impact.") + boardGrid(["heal","barrier","shield"], 15), false) +
      subpanel("healing", "barrier", boardGrid(["heal","barrier","shield"], 25), true) +
      subpanel("healing", "profiles", boardGrid(["hps","heal","support"], 25), true) +
      subpanel("healing", "skills", boardGrid(["heal skill","healing skill","target"], 25), true);
    var scores = subnav("scores", [["all","All"],["offense","Offense"],["support","Support"],["healing","Healing"],["defense","Defense"]]) +
      subpanel("scores", "all", highScoreGrid([]), false) +
      subpanel("scores", "offense", highScoreGrid(["damage","dps","kill","down","burst"]), true) +
      subpanel("scores", "support", highScoreGrid(["cleanse","strip","boon","cc","res"]), true) +
      subpanel("scores", "healing", highScoreGrid(["heal","barrier"]), true) +
      subpanel("scores", "defense", highScoreGrid(["defense","damage taken","death","survival"]), true);
    var details = subnav("details", [["fights","Fights"],["players","Players"],["attendance","Attendance"],["composition","Composition"],["tables","All Tables"]]) +
      subpanel("details", "fights", fightsTable(), false) +
      subpanel("details", "players", "<label class=\"search\">Filter players<input id=\"player-filter\" placeholder=\"Name, account, class…\"></label>" + boardGrid([], 50), true) +
      subpanel("details", "attendance", boardGrid(["attendance","fight attendance"], 50), true) +
      subpanel("details", "composition", squadCards() || "<p class=\"empty\">No squad composition was found.</p>", true) +
      subpanel("details", "tables", "<div id=\"all-boards\">" + boardGrid([], 100) + "</div>" + highScoreGrid([]) + poisonTable(), true);
    var body = "<div class=\"session-strip\"><b>Sparky Pro</b><span>" +
      esc((model.session || {}).date || "Night report") + "</span><span>" +
      esc(humanDuration((model.session || {}).total_duration) || "Duration unavailable") +
      "</span><span>Offline · deterministic</span></div>" +
      reportHeading("Pro · command analytics") + totalsCards(false) +
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
      "<button id=\"open-classic\">Open Classic</button></div></section>";
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
      ".svg-scroll{overflow:auto}.chart-card svg{width:100%;min-width:560px;height:150px}.axis{stroke:var(--line)}",
      ".trend{fill:none;stroke:var(--accent);stroke-width:3;stroke-linejoin:round}.chart-card circle{fill:var(--accent-2);stroke:var(--bg);stroke-width:2}",
      ".coverage,.intel-controls,.intel-kpis{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.coverage span{padding:9px 12px;",
      "border:1px solid var(--line);border-radius:999px;color:var(--muted)}.coverage b{color:var(--text)}",
      ".intel-controls label{color:var(--muted);font-size:12px}.intel-kpis{display:grid;grid-template-columns:repeat(3,1fr)}",
      ".wide{margin:12px 0}.chips{display:flex;flex-wrap:wrap;gap:7px}.chips span{padding:6px 9px;background:var(--panel-2);",
      "border:1px solid var(--line-soft);border-radius:999px;color:var(--muted);font-size:12px}.chips b{color:var(--text)}",
      ".scope-note,.muted{color:var(--muted)}.comparison-banner{padding:13px;border-left:4px solid var(--accent-2);",
      "background:var(--panel);margin:12px 0}.party-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}",
      ".party{border:1px dashed var(--line);border-radius:10px;background:var(--panel);overflow:hidden}.party header{display:flex;",
      "justify-content:space-between;padding:10px 12px;border-bottom:1px solid var(--line)}.party header span{color:var(--muted);font-size:11px}",
      ".party ol{list-style:none;margin:0;padding:8px}.party li{position:relative;padding:8px 9px 8px 13px;border-bottom:1px solid var(--line-soft)}",
      ".party li:last-child{border:0}.party li:before{content:'';position:absolute;left:0;top:10px;bottom:10px;width:3px;background:var(--good)}",
      ".party li.inferred:before{background:var(--accent-2)}.party li.unknown:before{background:var(--faint)}.party li span,.party li small{display:block;color:var(--muted);font-size:11px}",
      ".evidence-key{display:flex;gap:14px;color:var(--muted);font-size:12px;margin:12px 0}.evidence-key i{display:inline-block;width:8px;height:8px;",
      "border-radius:50%;margin-right:5px;background:var(--good)}.evidence-key i.inferred{background:var(--accent-2)}.evidence-key i.unknown{background:var(--faint)}",
      ".ai-read{margin-top:18px;border-left:4px solid var(--purple)}.ai-read small{color:var(--muted)}",
      ".source-callout{display:flex;justify-content:space-between;align-items:center;padding:18px;margin-top:22px;border:1px solid var(--line);",
      "border-radius:10px;background:var(--panel)}.source-callout button{background:var(--accent);color:var(--on-accent)}",
      "@media(max-width:800px){.tabs{overflow:auto}.section-head,.spotlight{display:block}.party-grid{grid-template-columns:repeat(2,1fr)}}",
      "@media(max-width:600px){.session-strip{margin:-18px -12px 18px;overflow:auto}.tabs{top:34px}.intel-grid,.comparison-grid,.squad-grid,",
      ".party-grid,.intel-kpis{grid-template-columns:1fr}.search input,.intel-controls select{min-width:0;width:100%}}",
      "@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}"
    ].join("");
    return "<!doctype html><html data-theme=\"" + esc(currentTheme) +
      "\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
      "<title>Sparky Pro report</title><style>" + commonCss + extraCss +
      "</style></head><body><main class=\"wrap\">" + body + "</main></body></html>";
  }
  function wireSparky(doc) {
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
          ". Party estimates remain fight-specific until a recurring cohort is proven.</p>" +
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
          fmt(fight.confidence) + "</strong><span>reconstruction confidence</span></div></div>" +
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
