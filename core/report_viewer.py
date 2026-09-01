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
  function renderSparky() {
    var boards = allBoards();
    var body = reportHeading("Sparky · the complete guided view") +
      totalsCards(false) +
      "<nav class=\"tabs\" aria-label=\"Report sections\">" +
      "<button class=\"selected\" data-tab=\"overview\">Overview</button>" +
      "<button data-tab=\"players\">Players</button>" +
      "<button data-tab=\"fights\">Fights</button>" +
      "<button data-tab=\"poison\">Poison</button>" +
      "<button data-tab=\"source\">Every detail</button></nav>" +
      "<section data-section=\"overview\"><div class=\"section-head\"><div>" +
      "<span class=\"eyebrow\">Start here</span><h2>What happened tonight</h2>" +
      "</div><p>Headline performers and the numbers that tell the story.</p></div>" +
      (boards.length ? "<div class=\"boards\">" +
        boards.slice(0, 6).map(function (b) { return boardCard(b, 10); }).join("") +
        "</div>" : "<p class=\"empty\">No overview boards were found.</p>") +
      highScoreCards() + "</section>" +
      "<section data-section=\"players\" hidden><div class=\"section-head\"><div>" +
      "<span class=\"eyebrow\">All boards</span><h2>Player performance</h2></div>" +
      "<label class=\"search\">Filter <input id=\"player-filter\" " +
      "placeholder=\"Name, account, class…\"></label></div>" +
      (boards.length ? "<div id=\"all-boards\" class=\"boards\">" +
        boards.map(function (b) { return boardCard(b, 50); }).join("") +
        "</div>" : "<p class=\"empty\">No player tables were found.</p>") +
      squadCards() + "</section>" +
      "<section data-section=\"fights\" hidden><div class=\"section-head\"><div>" +
      "<span class=\"eyebrow\">Fight by fight</span><h2>The night in order</h2>" +
      "</div><p>One row per parsed combat log.</p></div>" + fightsTable() + "</section>" +
      "<section data-section=\"poison\" hidden><div class=\"section-head\"><div>" +
      "<span class=\"eyebrow\">Coverage</span><h2>Poison pressure</h2></div>" +
      "<p>Applications normalized by active fight time.</p></div>" +
      poisonTable() + "</section>" +
      "<section data-section=\"source\" hidden><div class=\"source-callout\">" +
      "<span class=\"eyebrow\">Nothing hidden</span><h2>Need every original " +
      "table and chart?</h2><p>Classic is the byte-for-byte upstream report " +
      "inside this same file.</p><button id=\"open-classic\">Open Classic</button>" +
      "</div></section>";
    var extraCss = [
      ".tabs{position:sticky;top:0;z-index:2;display:flex;gap:7px;margin:24px 0;",
      "padding:8px;border:1px solid #29443b;border-radius:14px;background:#0e1916ee;",
      "backdrop-filter:blur(12px)}.tabs button,.source-callout button{border:0;",
      "border-radius:9px;padding:9px 14px;background:transparent;color:#a9beb7;",
      "font:inherit;font-weight:700;cursor:pointer}.tabs button.selected,",
      ".source-callout button{background:#48dda5;color:#07140f}",
      ".section-head{display:flex;align-items:end;justify-content:space-between;",
      "gap:20px;margin:30px 0 14px}.section-head h2{font-size:24px;margin:4px 0 0}",
      ".section-head p{color:#8ca59c;max-width:420px}.table-panel{border:1px solid #273f37;",
      "border-radius:14px;background:#111c19}.search{color:#8ca59c}.search input{",
      "display:block;margin-top:5px;min-width:240px;border:1px solid #315247;",
      "border-radius:9px;padding:9px 11px;background:#0b1411;color:#edf8f4}",
      ".squad-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}",
      ".squad{border:1px solid #273f37;border-radius:12px;padding:12px;background:#111c19}",
      ".squad h3{margin:0 0 8px}.squad span{display:block;color:#a9beb7;font-size:12px}",
      ".source-callout{text-align:center;padding:60px 20px;border:1px solid #2c4d42;",
      "border-radius:18px;background:linear-gradient(135deg,#13251f,#0d1714)}",
      ".source-callout h2{font-size:32px;margin:8px 0}.source-callout p{color:#9db3ab}",
      ".source-callout button{padding:11px 24px;margin-top:10px}",
      "@media(max-width:700px){.tabs{overflow:auto}.section-head{display:block}",
      ".squad-grid{grid-template-columns:1fr}.search input{min-width:0;width:100%}}"
    ].join("");
    return "<!doctype html><html><head><meta charset=\"utf-8\">" +
      "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
      "<title>Sparky report</title><style>" + commonCss + extraCss +
      "</style></head><body><main class=\"wrap\">" + body +
      "</main></body></html>";
  }
  function wireSparky(doc) {
    Array.from(doc.querySelectorAll("[data-tab]")).forEach(function (button) {
      button.addEventListener("click", function () {
        Array.from(doc.querySelectorAll("[data-tab]")).forEach(function (b) {
          b.classList.toggle("selected", b === button);
        });
        Array.from(doc.querySelectorAll("[data-section]")).forEach(function (s) {
          s.hidden = s.getAttribute("data-section") !== button.getAttribute("data-tab");
        });
        doc.defaultView.scrollTo(0, 0);
      });
    });
    var openClassic = doc.getElementById("open-classic");
    if (openClassic) openClassic.addEventListener("click", function () {
      show("classic");
    });
    var filter = doc.getElementById("player-filter");
    if (filter) filter.addEventListener("input", function () {
      var wanted = filter.value.toLowerCase();
      Array.from(doc.querySelectorAll("#all-boards tbody tr")).forEach(function (row) {
        row.hidden = wanted && row.textContent.toLowerCase().indexOf(wanted) < 0;
      });
    });
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
    switched = build_switchable_report(
        classic_html,
        build_night_model(list(tiddlers)),
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
