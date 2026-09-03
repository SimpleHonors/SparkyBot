"""Build one offline night report with three viewer-selectable presentations.

The upstream report remains byte-for-byte available as Classic. A compact
skin-ready model powers the native Simple and Sparky views. Nothing is fetched
at view time: both payloads are stored as base64(gzip(content)).
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
_MODEL_PAYLOAD_RE = re.compile(
    r'<script id="night-model-payload" type="text/plain">'
    r"([A-Za-z0-9+/=]+)</script>"
)
_VIEWER_MARKER = 'data-sparkybot-report-viewer="1"'
_SELECTED_FIGHTS_RE = re.compile(r"\((\d+)\s+fights?\)", re.IGNORECASE)
_PROFESSION_ICON_RE = re.compile(
    r'\{"title":"([^"<>]+)_icon_small\.png","text":"([A-Za-z0-9+/=]+)"'
)


def normalize_report_view(value: str | None) -> str:
    value = (value or "").strip().casefold()
    return value if value in REPORT_VIEWS else DEFAULT_REPORT_VIEW


_SHELL_TEMPLATE = r"""<!doctype html>
<html lang="en" data-sparkybot-report-viewer="1" data-theme="blackout">
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
    <option value="blackout" selected>Blackout</option>
    <option value="studio-light">Studio Light</option>
  </select>
  <span id="viewer-meta">One file · same night · your choice</span>
</div>
<div id="opening">Opening the report…</div>
<iframe id="report-frame" title="Night report" hidden></iframe>
<script id="viewer-settings" type="application/json">__SETTINGS__</script>
<script id="night-model-payload" type="text/plain">__MODEL_PAYLOAD__</script>
<script id="classic-payload" type="text/plain">__PAYLOAD__</script>
<script>
(async function () {
  "use strict";
  var views = ["sparky", "simple", "classic"];
  var settings = JSON.parse(document.getElementById("viewer-settings").textContent);
  var model = null;
  var opening = document.getElementById("opening");
  var frame = document.getElementById("report-frame");
  var meta = document.getElementById("viewer-meta");
  var classicHtml = "";
  var docs = {};
  var currentView = "";
  var themes = ["graphite", "midnight", "blackout", "studio-light"];
  var currentTheme = "blackout";

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
    var raw=String(value || ""), key=raw.toLowerCase().replace(/[\s_-]+/g, "");
    var labels={
      fighttime:"Fight Time",targetdamage:"Damage to Enemy Players",targetdamageps:"DPS",
      targetpower:"Power Damage",targetpowerps:"Power DPS",targetcondition:"Condition Damage",
      targetconditionps:"Condition DPS",targetbreakbardamage:"Breakbar Damage",
      alldamage:"All Damage",allpower:"All Power Damage",allcondition:"All Condition Damage",
      allbreakbardamage:"All Breakbar Damage",healing:"Healing",healingps:"Healing / sec",
      barrier:"Barrier",barrierps:"Barrier / sec",downedhealing:"Downed Healing",
      downedhealingps:"Downed Healing / sec",condicleanse:"Allied Conditions Cleansed",
      condicleansetime:"Condition Duration Removed",condicleanseself:"Self-Cleansed Conditions",
      condicleansetimeself:"Self-Cleansed Condition Duration",boonstrips:"Enemy Boons Removed",
      boonstripstime:"Enemy Boon Duration Removed",boonstripsdowned:"Boons Removed from Downed Enemies",
      boonstripstimedowned:"Boon Duration Removed from Downed Enemies",resurrects:"Resurrects",
      resurrecttime:"Resurrection Time",appliedcrowdcontrol:"Crowd Control Applied",
      appliedcrowdcontrolduration:"Crowd Control Duration",againstdowneddamage:"Damage to Downed Enemies",
      glickorating:"Glicko Rating",avgdamage:"Average Damage / sec",
      avgdowncontribution:"Average Down Contribution / sec",
      avgdamagebarrier:"Average Barrier Absorption / sec",avgboonstrips:"Average Enemy Boons Removed / sec",
      avgcleanses:"Average Conditions Cleansed / sec",avghealing:"Average Healing / sec",
      avgbarrier:"Average Barrier / sec",avgresurrects:"Average Resurrects / min",
      s1066resurrect:"Resurrect",s12601naturesrenewal:"Nature’s Renewal",
      s69336naturesrenewal:"Nature’s Renewal",s14419battlestandard:"Battle Standard",
      s1196revivepet:"Revive Pet",s1175bandage:"Bandage",s55024glyphofthestars:"Glyph of the Stars",
      persecond:"Per Second",perminute:"Per Minute"
    };
    if (labels[key]) return labels[key];
    return titleCase(raw.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " "));
  }
  function confidenceLabel(confidence) {
    if (!confidence) return "Unknown";
    if (typeof confidence !== "object") return readableLabel(confidence);
    var level = readableLabel(confidence.level || "Unknown");
    var score = Number(confidence.score);
    return level + (Number.isFinite(score) ? " · " + Math.round(score * 100) + "%" : "");
  }
  function humanDuration(value) {
    if (typeof value === "number" && Number.isFinite(value)) {
      var whole = Math.max(0, Math.round(value));
      var numericHours = Math.floor(whole / 3600);
      var numericMinutes = Math.floor((whole % 3600) / 60);
      var numericSeconds = whole % 60;
      return (numericHours ? numericHours + "h " : "") +
        (numericMinutes ? numericMinutes + "m " : "") + numericSeconds + "s";
    }
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
  function fightClock(value) {
    var match=String(value || "").match(/(?:^|\s)(\d{1,2}):(\d{2})(?::\d{2})?(?:\s|$)/);
    if (!match) return "";
    var hour=Number(match[1]), minute=match[2], suffix=hour>=12 ? "PM" : "AM";
    hour=hour%12 || 12;
    return hour+":"+minute+" "+suffix;
  }
  function fightKd(fight) {
    var kills=Number(fight && fight.kills || 0),deaths=Number(fight && fight.ally_deaths || 0);
    if (deaths > 0) return fmt(kills/deaths);
    return kills > 0 ? "∞" : "—";
  }
  function fightDownShare(fight) {
    var inflicted=Number(fight && fight.downs || 0),suffered=Number(fight && fight.ally_downs || 0);
    var total=inflicted+suffered;
    return total > 0 ? fmt(inflicted/total*100)+"%" : "—";
  }
  function drillAttrs(kind, title, body, evidence, ref) {
    return " tabindex=\"0\" data-drill data-drill-kind=\"" + esc(kind) +
      "\" data-drill-title=\"" + esc(title) + "\" data-drill-body=\"" +
      esc(body) + "\" data-drill-evidence=\"" + esc(evidence || "Report evidence") + "\"" +
      (ref == null ? "" : " data-drill-ref=\"" + esc(ref) + "\"");
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
      elementalist:["M12 2C9 7 5 9 5 15a7 7 0 0 0 14 0c0-4-2-7-5-10 0 4-2 5-3 7 0-3 2-5 1-10zm0 18a4 4 0 0 1-4-4c0-2 1-3 3-5 0 2 1 3 2 4 1-1 2-2 2-4 2 2 2 4 1 6a4 4 0 0 1-4 3z","E"],
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
  function professionInline(profession, showName) {
    profession=profession || "Unknown";
    return "<span class=\"profession-inline\">" + professionGlyph(profession) +
      (showName === false ? "" : "<span>" + esc(profession) + "</span>") + "</span>";
  }
  function playerBarLabel(row) {
    row=row || {};
    return "<span class=\"bar-label player-bar-label\">" +
      professionGlyph(row.profession || "Unknown") +
      "<span class=\"metric-player-text\"><b>" + esc(row.name || "Entry") +
      "</b><small>" + esc(row.profession || "Class unavailable") + "</small></span></span>";
  }
  function roleClass(role) {
    var value = String(role || "unknown").toLowerCase();
    if (/heal|sustain/.test(value)) return "heal";
    if (/support|boon|strip|cleanse|control|\bcc\b/.test(value)) return "support";
    if (/dps|damage|power|condition|condi/.test(value)) return "dps";
    if (/hybrid|combination/.test(value)) return "hybrid";
    return "unknown";
  }
  function roleGlyph(role) {
    var kind=roleClass(role),drawing;
    if (kind === "dps") drawing="<path d=\"M5 4l15 15M19 4L4 19M7 16l-3 4M17 16l3 4\"/>";
    else if (kind === "heal") drawing="<path class=\"filled\" d=\"M9 3h6v6h6v6h-6v6H9v-6H3V9h6z\"/>";
    else if (kind === "support") drawing="<path class=\"filled\" d=\"M12 2l8 3v6c0 5.2-3.1 9.2-8 11-4.9-1.8-8-5.8-8-11V5l8-3zm0 4L7 7.8V11c0 3.4 1.8 6.1 5 7.6 3.2-1.5 5-4.2 5-7.6V7.8L12 6z\"/>";
    else if (kind === "hybrid") drawing="<path d=\"M10.5 3L5 5v5c0 3.8 1.8 6.8 5.5 8.5M13 5l7 14M19 5l-7 14\"/>";
    else drawing="<path d=\"M9 8a3.2 3.2 0 116 2.1c-1.3 1.1-3 1.5-3 3.4M12 18.5v.1\"/>";
    return "<span class=\"role-glyph role-glyph-"+kind+"\" aria-hidden=\"true\"><svg viewBox=\"0 0 24 24\">"+drawing+"</svg></span>";
  }
  function roleDisplay(role, inference) {
    var value=String(role || "").toLowerCase();
    if (/unknown|unidentified|unoccupied|open slot/.test(value)) return "Role not inferred";
    var qualifier=readableLabel(inference && (inference.qualifier || inference.level) || "Estimated");
    if (qualifier === "Inferred") qualifier="Estimated";
    var label;
    if (/dps\s*\/\s*support/.test(value)) label="DPS / Support";
    else if (/support \/ healing|healer/.test(value)) label="Support / Healing";
    else if (/boon strip|strip/.test(value)) label="Boon Strips";
    else if (/crowd control|\bcc\b|control/.test(value)) label="Crowd Control";
    else if (/condition|condi/.test(value) && /dps|damage/.test(value)) label="Condition DPS";
    else if (/power/.test(value) && /dps|damage/.test(value)) label="Power DPS";
    else {
      var kind=roleClass(role);
      if (kind === "heal") label="Healing";
      else if (kind === "support") label="Boon Support";
      else if (kind === "dps") label="DPS";
      else if (kind === "hybrid") label="DPS / Support";
      else label=readableLabel(role);
    }
    return qualifier + " " + label;
  }
  function allBoards() {
    var seen = {};
    return (model.stat_tables || [])
      .filter(function (board) {
        var key = String(board.stat || "").toLowerCase();
        if (!key || seen[key] || !(board.rows || []).length) return false;
        if (board.show_as_board === false) return false;
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
      "long-term averages across raids, not tonight’s performance totals. Historical leaderboard rows already expose all exported history fields; they do not open a redundant detail drawer.</span></div>" +
      "<div class=\"boards\">" + boards.map(function(board){
        var rows=(board.rows || []).slice(0,100), metric=/avg|average/i.test(String(board.value_label || "")) ?
          board.value_label : titleCase(board.stat) + " Avg";
        var body=rows.map(function(row,index){
          var raids=row.raids != null ? row.raids : row.fights;
          return "<tr" + (index >= 5 ? " class=\"board-extra\" hidden" : "") +
            "><td class=\"rank\">" +
            (index+1) + "</td><td><b>" + esc(row.name) + "</b></td><td>" +
            professionInline(row.profession || "Unknown") + "</td><td class=\"number\">" +
            fmt(row.average != null ? row.average : row.value) + "</td><td class=\"number\">" +
            fmt(raids) + "</td></tr>";
        }).join("");
        return "<article class=\"board historical-board\"><h3>" + esc(titleCase(board.stat)) +
          "</h3><div class=\"table-wrap\"><table class=\"history-table\"><colgroup><col style=\"width:5%\"><col style=\"width:34%\"><col style=\"width:25%\"><col style=\"width:22%\"><col style=\"width:14%\"></colgroup><thead><tr><th>#</th><th>Player</th>" +
          "<th>Class</th><th class=\"number\" title=\"" + esc(metric) + "\">Avg" +
          "</th><th class=\"number\">Raids</th></tr></thead><tbody>" + body +
          "</tbody></table></div>" + (rows.length > 5 ? "<footer class=\"board-actions\">" +
          "<button type=\"button\" data-expand-board aria-expanded=\"false\">Expand all " +
          rows.length + "</button></footer>" : "") + "</article>";
      }).join("") + "</div>";
  }
  function boardCard(board, limit) {
    var rows = (board.rows || []).slice();
    var initialLimit = 5;
    var metric = board.metric || {};
    var boardLabel = metric.label || board.display_label || titleCase(board.stat);
    var totalLabel = metric.total_label || board.value_label || titleCase(board.stat);
    var rateLabel = metric.rate_label || "Participation-weighted rate";
    var isPullBoard = String(board.source_key || "") === "Pull-Skills";
    var boardNote = String(board.source_key || "") === "Mechanics" ?
      "Classic called this source table Mechanics. In Sparky views it means killing blows credited by the exported log." :
      (isPullBoard ?
        "This is not a pull count. The upstream combiner uses connected damage hits from skills that can pull because actual observed pull events are unavailable in the Elite Insights JSON. Pulsing skills inflate this proxy: Flux State has 12 storm hits, while Abyssal Blot has 5 impacts and pulls on its first pulse. New reports show cast counts separately when detailed rotations are available." : "");
    var compactRateLabel = /\bdps\b/i.test(rateLabel) ? "DPS" :
      /\bhps\b/i.test(rateLabel) ? "HPS" :
      /uptime|percent|%/i.test(rateLabel) ? "Uptime %" :
      /\/\s*(?:sec|second)s?\b/i.test(rateLabel) ? "Per Sec" :
      /\/\s*(?:min|minute)s?\b/i.test(rateLabel) ? "Per Min" : "Rate";
    var rateSuffix = metric.rate_unit === "percent" ? "%" : "";
    rows.sort(function(a,b) {
      var aRate = a.rate != null ? a.rate : (a.participation_weighted_rate != null ? a.participation_weighted_rate : null);
      var bRate = b.rate != null ? b.rate : (b.participation_weighted_rate != null ? b.participation_weighted_rate : null);
      if (aRate != null || bRate != null) return (Number(bRate) || 0) - (Number(aRate) || 0);
      return (Number(b.total != null ? b.total : b.value) || 0) - (Number(a.total != null ? a.total : a.value) || 0);
    });
    rows = rows.slice(0, limit || 100);
    var hasRate = rows.some(function(row) {
      return row.rate != null || row.participation_weighted_rate != null || row.value_per_minute != null ||
        row.per_minute != null || row.rate != null;
    });
    var hasTotal = rows.some(function(row) { return row.total != null; }) || !hasRate;
    var hasParticipation = rows.some(function(row) { return row.participation_time != null || row.duration != null; });
    var hasFights = board.show_fights_column !== false && rows.some(function(row) {
      return row.fight_count != null || row.fights != null;
    });
    var hasPullHits = isPullBoard && rows.some(function(row) { return row.pull_skill_logged_hit_events != null; });
    var hasPullCasts = isPullBoard && rows.some(function(row) { return row.pull_skill_casts != null; });
    var body = rows.map(function (row, index) {
      var title = (row.name || "Entry") + " · " + boardLabel;
      var total = hasTotal ? (row.total != null ? row.total : row.value) : null;
      var rate = row.rate != null ? row.rate : (row.participation_weighted_rate != null ? row.participation_weighted_rate :
        (row.value_per_minute != null ? row.value_per_minute :
          (row.per_minute != null ? row.per_minute : null)));
      var detail = (row.profession || "Class unavailable") +
        (hasTotal ? " · " + totalLabel + " " + fmt(total) : "") +
        (hasRate ? " · " + rateLabel + " " + fmt(rate) + rateSuffix : "") +
        (hasPullHits ? " · logged hits " + fmt(row.pull_skill_logged_hit_events) +
          " · connection rate " + fmt(row.pull_skill_connection_rate) + "%" : "") +
        (hasPullCasts ? " · casts " + fmt(row.pull_skill_casts) +
          " · connected hits/cast " + fmt(row.pull_skill_connected_hits_per_cast) : "");
      var participation = row.participation_time != null ? row.participation_time :
        (row.duration != null ? row.duration : null);
      var fights = row.fight_count != null ? row.fight_count : row.fights;
      return "<tr" + drillAttrs("leaderboard-row", title, detail,
        "Source: " + (board.source_tiddler || titleCase(board.stat)) + " · ranked by " +
        (hasRate ? rateLabel : totalLabel)) +
        " data-total=\"" + esc(total == null ? "" : total) +
        "\" data-rate=\"" + esc(rate == null ? "" : rate) +
        "\" data-participation=\"" + esc(participation == null ? "" : participation) +
        "\" data-fights=\"" + esc(fights == null ? "" : fights) + "\"" +
        (index >= initialLimit ? " class=\"board-extra\" hidden" : "") +
        "><td class=\"rank\">" + esc(row.rank || index + 1) +
        "</td><td class=\"player-cell\"><b>" + esc(row.name) + "</b>" +
        (row.account ? "<small>" + esc(row.account) + "</small>" : "") +
        "</td><td class=\"class-cell\">" + professionInline(row.profession || "Unknown") +
        "</td>" + (hasTotal ? "<td class=\"number\" data-label=\"" + esc(totalLabel) + "\">" + fmt(total) + "</td>" : "") +
        (hasPullHits ? "<td class=\"number\" data-label=\"Logged Hits\">" + fmt(row.pull_skill_logged_hit_events) + "</td>" +
          "<td class=\"number\" data-label=\"Connection Rate\">" + fmt(row.pull_skill_connection_rate) + "%</td>" : "") +
        (hasPullCasts ? "<td class=\"number\" data-label=\"Casts\">" + fmt(row.pull_skill_casts) + "</td>" +
          "<td class=\"number\" data-label=\"Connected Hits / Cast\">" + fmt(row.pull_skill_connected_hits_per_cast) + "</td>" : "") +
        (hasRate ? "<td class=\"number\" data-label=\"" + esc(compactRateLabel) + "\">" + fmt(rate) + rateSuffix + "</td>" : "") +
        (hasParticipation ? "<td class=\"number\" data-label=\"Fight Time\">" + humanDuration(Number(participation)) + "</td>" : "") +
        (hasFights ? "<td class=\"number\" data-label=\"Fights\">" + fmt(fights) + "</td>" : "") + "</tr>";
    }).join("");
    return "<article class=\"board\"><h3>" + esc(boardLabel) +
      "</h3>" + (boardNote ? "<p class=\"board-note\">" + esc(boardNote) + "</p>" : "") +
      "<div class=\"table-wrap\"><table data-board-table><colgroup>" +
      "<col class=\"col-rank\"><col class=\"col-player\"><col class=\"col-class\">" +
      (hasTotal ? "<col class=\"col-total\">" : "") +
      (hasPullHits ? "<col class=\"col-total\"><col class=\"col-rate\">" : "") +
      (hasPullCasts ? "<col class=\"col-total\"><col class=\"col-rate\">" : "") +
      (hasRate ? "<col class=\"col-rate\">" : "") +
      (hasParticipation ? "<col class=\"col-time\">" : "") +
      (hasFights ? "<col class=\"col-fights\">" : "") +
      "</colgroup><thead><tr><th>#</th><th>Player</th><th>Class</th>" +
      (hasTotal ? "<th class=\"number\"><button type=\"button\" data-sort-key=\"total\" title=\"" + esc(totalLabel) +
        "\">" + esc(isPullBoard ? "Connected Hits" : "Total") + "</button></th>" : "") +
      (hasPullHits ? "<th class=\"number\">Logged Hits</th><th class=\"number\">Connection Rate</th>" : "") +
      (hasPullCasts ? "<th class=\"number\">Casts</th><th class=\"number\">Connected Hits / Cast</th>" : "") +
      (hasRate ? "<th class=\"number\" aria-sort=\"descending\"><button type=\"button\" data-sort-key=\"rate\" title=\"" + esc(rateLabel) + "\">" + esc(compactRateLabel) + "</button></th>" : "") +
      (hasParticipation ? "<th class=\"number\"><button type=\"button\" data-sort-key=\"participation\">Fight Time</button></th>" : "") +
      (hasFights ? "<th class=\"number\"><button type=\"button\" data-sort-key=\"fights\">Fights</button></th>" : "") +
      "</tr></thead><tbody>" + body + "</tbody></table></div>" +
      (rows.length > initialLimit ? "<footer class=\"board-actions\"><button type=\"button\" data-expand-board " +
        "aria-expanded=\"false\">Expand all " + rows.length + "</button></footer>" : "") + "</article>";
  }
  function totalsCards(compact) {
    var t = model.totals || {};
    var fights = Number(t.fights) || 0;
    var cards = [
      ["fights", "Modeled fights", t.fights, "Fight logs included in this combined report", "Selected night · fight summaries"],
      ["downs", "Enemy downs", t.enemy_downs, "Enemies downed by the squad across " + fmt(t.fights) + " fights" +
        (fights ? " · " + fmt(Number(t.enemy_downs || 0) / fights) + " per fight" : ""),
        "Selected night · combined fight summaries"],
      ["kills", "Enemy kills", t.enemy_kills, "Enemies killed by the squad across " + fmt(t.fights) + " fights" +
        (fights ? " · " + fmt(Number(t.enemy_kills || 0) / fights) + " per fight" : ""),
        "Selected night · combined fight summaries"],
      ["ally_downs", "Our downs", t.ally_downs, "Squad members downed across " + fmt(t.fights) + " fights" +
        (fights ? " · " + fmt(Number(t.ally_downs || 0) / fights) + " per fight" : ""),
        "Selected night · combined fight summaries"],
      ["ally_deaths", "Our deaths", t.ally_deaths, "Squad member deaths across " + fmt(t.fights) + " fights" +
        (fights ? " · " + fmt(Number(t.ally_deaths || 0) / fights) + " per fight" : ""),
        "Selected night · combined fight summaries"],
      ["kdr", "K/D", t.kdr, "Enemy kills divided by squad deaths", "Selected night · calculated from combined fight summaries"]
    ];
    return "<div class=\"kpis" + (compact ? " compact" : "") + "\">" +
      cards.map(function (item, index) {
        return "<button type=\"button\" class=\"kpi metric-" + index + "\"" +
          drillAttrs("summary-metric:" + item[0], item[1] + " · " + fmt(item[2]), item[3], item[4]) +
          "><strong>" + fmt(item[2]) + "</strong><span>" + esc(item[1]) + "</span></button>";
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
        var fights=row.fight_count != null ? row.fight_count : winner.fight_count;
        var category=row.category || row.metric || "Category leader";
        var categoryKey=String(category).toLowerCase();
        var rateLabel=/damage/.test(categoryKey) ? "DPS" :
          /healing/.test(categoryKey) ? "HPS" :
          /stability/.test(categoryKey) ? "stability/sec" :
          /fight impact/.test(categoryKey) ? "down contribution/sec" :
          /cleans/.test(categoryKey) ? "cleanses/min" :
          /strip/.test(categoryKey) ? "strips/min" :
          /resur/.test(categoryKey) ? "resurrects/min" :
          /crowd|control/.test(categoryKey) ? "CC/min" :
          /pull/.test(categoryKey) ? "connected hits/min" : readableLabel(row.rate_unit || "rate");
        var totalLabel=row.metric_label || row.total_label || "Total";
        var reason=row.why || row.reason || "Highest session total";
        var detail=totalLabel + " " + fmt(total) +
          (rate != null ? " · " + rateLabel + " " + fmt(rate) : "") +
          (fightTime ? " · Fight Time " + humanDuration(Number(fightTime)) : "") +
          (fights != null ? " · " + fmt(fights) + " fights" : "");
        var sourceInfo=row.source || {}, evidenceLabel=sourceInfo.table ?
          "Tonight’s " + sourceInfo.table + " table" : "Tonight’s exact performance table";
        return "<button type=\"button\" class=\"mvp-card\"" + drillAttrs("night-mvp", category + " · " +
          (name || "Winner unavailable"), detail + " · " + reason,
          evidenceLabel) + "><span>" +
          esc(category) + "</span><span class=\"mvp-winner\">" + professionInline(row.profession || winner.profession || "Unknown", false) +
          "<b>" + esc(name || "Winner unavailable") + "</b><small>" + esc(row.profession || winner.profession || "Class unavailable") + "</small></span><strong>" +
          fmt(total) + "</strong>" + (rate != null ? "<small>" + esc(rateLabel) + " " + fmt(rate) + "</small>" : "") +
          (fightTime ? "<small>Fight Time " + humanDuration(Number(fightTime)) +
            (fights != null ? " · " + fmt(fights) + " fights" : "") + "</small>" : "") +
          "<p>" + esc(reason) + "</p></button>";
      }).join("") + "</div></section>";
  }
  function reportHeading(kicker) {
    var s = model.session || {};
    var title = esc(s.report_title || "Night report");
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
    "--on-accent:#fff}*{box-sizing:border-box}html,body{max-width:100%;overflow-x:hidden}body{margin:0;background:var(--bg);color:var(--text);",
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
    "grid-template-columns:1fr;gap:16px}.board{min-width:0;",
    "border:1px solid var(--line);background:var(--panel);border-radius:10px;overflow:hidden}",
    ".board h3{margin:0;padding:14px 16px;border-bottom:1px solid var(--line);",
    "font-size:15px}.board-note{margin:0;padding:10px 16px;border-bottom:1px solid var(--line-soft);color:var(--muted);font-size:12px;line-height:1.45}.table-wrap{width:100%;overflow-x:auto;overflow-y:hidden}table{width:100%;border-collapse:collapse;table-layout:fixed}",
    ".col-rank{width:5%}.col-player{width:25%}.col-class{width:16%}.col-total{width:13%}",
    ".col-rate{width:14%}.col-time{width:18%}.col-fights{width:9%}",
    ".poison-rank{width:5%}.poison-player{width:29%}.poison-class{width:20%}.poison-apps{width:15%}.poison-rate{width:16%}.poison-output{width:15%}",
    ".score-rank{width:6%}.score-entry{width:76%}.score-result{width:18%}",
    ".fight-col-index{width:5%}.fight-col-time{width:10%}.fight-col-duration{width:15%}.fight-col-count{width:7.5%}.fight-col-damage{width:20%}.fight-col-log{width:15%}.fights-table.has-fight-logs .fight-col-index{width:4%}.fights-table.has-fight-logs .fight-col-time{width:8%}.fights-table.has-fight-logs .fight-col-duration{width:13%}.fights-table.has-fight-logs .fight-col-count{width:6%}.fights-table.has-fight-logs .fight-col-damage{width:18%}.fights-table th,.fights-table td{padding:9px 8px;white-space:nowrap;overflow-wrap:normal}.fights-table td{font-size:12px}.fight-legend{display:flex;gap:14px;flex-wrap:wrap;padding:9px 16px;border-bottom:1px solid var(--line-soft);color:var(--muted);font-size:11px}.fight-legend span:before{content:'';display:inline-block;width:9px;height:9px;margin-right:6px;border-radius:2px;background:var(--accent-2)}.fight-legend .good:before{background:var(--good)}.fight-legend .bad:before{background:var(--bad)}.fights-table tbody tr{box-shadow:inset 3px 0 transparent}.fights-table tbody tr.fight-outcome-good{background:color-mix(in srgb,var(--good) 8%,transparent);box-shadow:inset 3px 0 var(--good)}.fights-table tbody tr.fight-outcome-bad{background:color-mix(in srgb,var(--bad) 9%,transparent);box-shadow:inset 3px 0 var(--bad)}.fights-table tbody tr.fight-outcome-mixed{background:color-mix(in srgb,var(--accent-2) 5%,transparent);box-shadow:inset 3px 0 var(--accent-2)}",
    ".skill-rank{width:5%}.skill-name{width:43%}.skill-value{width:14%}.skill-share{width:10%}.skill-damage-table .skill-rank{width:5%}.skill-damage-table .skill-name{width:31%}.skill-damage-table .skill-value{width:13.5%}.skill-damage-table .skill-share{width:10%}.skill-damage-table th{white-space:normal;line-height:1.2}.skill-coverage{display:flex;justify-content:space-between;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line-soft);color:var(--muted)}.skill-coverage b{color:var(--text)}",
    "th,td{padding:8px 11px;border-bottom:1px solid var(--line-soft);text-align:left;",
    "white-space:normal;overflow-wrap:break-word}th{color:var(--muted);font-size:11px;text-transform:uppercase;",
    "white-space:nowrap;overflow-wrap:normal}.number{white-space:nowrap;overflow-wrap:normal}",
    "letter-spacing:.06em}th.number{text-align:right}th button{appearance:none;border:0!important;background:none!important;box-shadow:none!important;color:inherit;font:inherit;",
    "font-weight:800;text-transform:inherit;cursor:pointer;padding:0}th[aria-sort=ascending] button:after{content:' ↑';color:var(--accent)}th[aria-sort=descending] button:after{content:' ↓';color:var(--accent)}.board-actions{padding:9px 12px;",
    "border-top:1px solid var(--line-soft)}.board-actions button{border:1px solid var(--line);",
    "border-radius:6px;background:var(--panel-2);color:var(--text);padding:6px 9px;cursor:pointer}",
    "td small{display:block;color:var(--faint)}.rank,.number{",
    "text-align:right;font-variant-numeric:tabular-nums}.profession{color:var(--text)}",
    ".profession-inline,.score-player,.mvp-winner{display:inline-flex;align-items:center;gap:8px;min-width:0}.profession-inline .profession-glyph,.score-player .profession-glyph,.mvp-winner .profession-glyph{display:inline-grid;place-items:center;flex:0 0 24px;width:24px;height:24px;margin:0}.score-player{width:100%}.score-player>span:last-child{min-width:0;overflow:hidden}.score-player b,.score-player small,.mvp-winner b,.mvp-winner small{display:block}.score-player b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.score-player small{color:var(--muted);white-space:normal;overflow-wrap:normal}.high-score-table th,.high-score-table td{padding-top:10px;padding-bottom:10px}.high-score-table .rank{vertical-align:top;padding-top:13px}.poison-player-cell b,.poison-player-cell small{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.poison-table .profession-inline{max-width:100%;white-space:nowrap}",
    ".empty{padding:24px;border:1px dashed var(--line);border-radius:10px;color:var(--muted)}",
    "button:focus-visible,[tabindex]:focus-visible{outline:2px solid var(--accent);outline-offset:2px}",
    "@media(max-width:850px){.kpis{grid-template-columns:repeat(3,1fr)}",
    ".boards{grid-template-columns:1fr}}@media(max-width:760px){.skill-player-board .table-wrap{overflow:visible}.skill-damage-table,.skill-damage-table tbody{display:block;width:100%;min-width:0}.skill-damage-table colgroup,.skill-damage-table thead{display:none}.skill-damage-table tbody tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 12px;padding:12px;border-bottom:1px solid var(--line-soft)}.skill-damage-table tbody tr[hidden]{display:none}.skill-damage-table td{display:flex;justify-content:space-between;gap:8px;min-width:0;padding:7px 0;border-bottom:1px dotted var(--line-soft);white-space:normal;overflow-wrap:anywhere}.skill-damage-table td:before{content:attr(data-label);color:var(--faint);font-size:10px;font-weight:700;letter-spacing:.04em;text-align:left;text-transform:uppercase}.skill-damage-table td[data-label=\"#\"]{grid-column:1/-1;justify-content:flex-start}.skill-damage-table td[data-label=Skill]{grid-column:1/-1;justify-content:flex-start;font-size:14px}.skill-damage-table td[data-label=Skill]:before{content:none}}@media(max-width:520px){",
    ".wrap{padding:18px 12px 40px}.kpis{grid-template-columns:repeat(2,1fr)}",
    ".hero{padding:22px 18px}.table-wrap{overflow:visible}",
    "table[data-board-table],table[data-board-table] tbody{display:block;width:100%}",
    "table[data-board-table] colgroup,table[data-board-table] thead{display:none}",
    "table[data-board-table] tbody tr{display:grid;grid-template-columns:32px minmax(0,1fr);gap:4px 10px;padding:14px 12px;border-bottom:1px solid var(--line-soft);background:transparent}",
    "table[data-board-table] td{display:block;min-width:0;padding:0;border:0}table[data-board-table] .rank{grid-column:1;grid-row:1/3;padding-top:2px;text-align:left;color:var(--faint)}",
    "table[data-board-table] .player-cell{grid-column:2;font-size:15px}table[data-board-table] .player-cell b,table[data-board-table] .player-cell small{overflow-wrap:anywhere}",
    "table[data-board-table] .class-cell{grid-column:2;color:var(--muted)}table[data-board-table] .number{grid-column:2;display:flex;justify-content:space-between;gap:12px;margin-top:4px;padding-top:7px;border-top:1px dotted var(--line-soft);text-align:right}",
    "table[data-board-table] .number:before{content:attr(data-label);color:var(--faint);font-size:10px;font-weight:700;letter-spacing:.06em;text-align:left;text-transform:uppercase}",
    ".fights-table,.fights-table tbody{display:block;width:100%}.fights-table colgroup,.fights-table thead{display:none}.fights-table tbody tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 14px;margin:0;padding:12px;border-bottom:1px solid var(--line-soft)}.fights-table td{display:flex;justify-content:space-between;gap:10px;min-width:0;padding:7px 0;border-bottom:1px dotted var(--line-soft);text-align:right;white-space:normal}.fights-table td:before{content:attr(data-label);color:var(--faint);font-size:10px;font-weight:700;letter-spacing:.05em;text-align:left;text-transform:uppercase}.fights-table td[data-label=Fight],.fights-table td[data-label=Time],.fights-table td[data-label=Duration]{grid-column:1/-1}.fights-table td[data-label=Fight]{font-size:14px;font-weight:800}.history-table{min-width:560px}}"
  ].join("");

  function renderSimple() {
    var boards = allBoards().slice(0, 8);
    var body = "<div class=\"simple-briefing\">" +
      reportHeading("Nightly briefing · essential results") +
      "<p class=\"brief-deck\">A quick, readable pass through the night’s most useful " +
      "squad results. Open Pro when you want comparisons, charts, and evidence.</p>" +
      totalsCards(true) +
      (boards.length ? "<section class=\"boards\">" +
        boards.map(function (b) { return boardCard(b, 10); }).join("") +
        "</section>" : "<p class=\"empty\">No headline boards were found. " +
        "Classic still contains every source table.</p>") +
      "<p class=\"foot\">Simple intentionally skips the fight-by-fight wall. " +
      "Choose Pro for the full tactical review or Classic for the untouched source.</p></div>";
    var simpleCss = [
      ".simple-briefing{--brief-accent:var(--accent-2);max-width:1120px;margin:0 auto;counter-reset:brief-board}",
      ".simple-briefing .hero{position:relative;padding:34px 38px 32px;border:0;border-left:6px solid var(--brief-accent);",
      "border-radius:0;background:var(--surface)}.simple-briefing .hero:after{content:'';position:absolute;left:38px;right:38px;bottom:0;height:1px;background:var(--line)}",
      ".simple-briefing .eyebrow{color:var(--brief-accent);letter-spacing:.22em}",
      ".simple-briefing .hero h1{max-width:820px;margin:11px 0 10px;font:500 clamp(34px,5vw,58px)/1.02 Georgia,'Times New Roman',serif;letter-spacing:-.025em}",
      ".brief-deck{max-width:720px;margin:20px 0 30px;padding-left:22px;border-left:2px solid var(--line);color:var(--muted);font:18px/1.55 Georgia,'Times New Roman',serif}",
      ".simple-briefing .kpis{grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;margin:0 0 42px;padding:1px;background:var(--line)}",
      ".simple-briefing .kpi,.simple-briefing .kpi[class*=metric-]{min-height:92px;padding:17px 20px;border:0;border-radius:0;background:var(--surface);cursor:default}",
      ".simple-briefing .kpi strong{color:var(--brief-accent);font:600 27px/1 Georgia,'Times New Roman',serif}",
      ".simple-briefing .kpi span{display:block;margin-top:9px;letter-spacing:.035em}",
      ".simple-briefing .boards{gap:38px}.simple-briefing .board{counter-increment:brief-board;border:0;border-radius:0;background:transparent;overflow:visible}",
      ".simple-briefing .board h3{display:flex;gap:14px;align-items:baseline;padding:0 0 12px;border-bottom:2px solid var(--text);font:600 22px/1.2 Georgia,'Times New Roman',serif}",
      ".simple-briefing .board h3:before{content:counter(brief-board,decimal-leading-zero);color:var(--brief-accent);font:800 11px/1 Segoe UI,system-ui,sans-serif;letter-spacing:.12em}",
      ".simple-briefing .table-wrap{border-bottom:1px solid var(--line)}.simple-briefing th{padding-top:11px;padding-bottom:11px;color:var(--faint);background:transparent}",
      ".simple-briefing td{padding-top:11px;padding-bottom:11px}.simple-briefing tbody tr:nth-child(even){background:color-mix(in srgb,var(--surface) 62%,transparent)}",
      ".simple-briefing .board-actions{padding:11px 0 0;border:0}.simple-briefing .board-actions button{border-radius:0;border-color:var(--line);background:transparent}",
      ".simple-briefing .foot{margin:44px 0 0;padding:20px 0;border-top:1px solid var(--line);color:var(--muted);font-family:Georgia,'Times New Roman',serif}",
      "@media(max-width:850px){.simple-briefing .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.simple-briefing .hero{padding:28px 26px}.simple-briefing .hero:after{left:26px;right:26px}}",
      "@media(max-width:520px){.simple-briefing .kpis{grid-template-columns:1fr}.brief-deck{font-size:16px}.simple-briefing .hero h1{font-size:36px}",
      ".simple-briefing .table-wrap{overflow:visible;border-bottom:0}.simple-briefing table[data-board-table],.simple-briefing table[data-board-table] tbody{display:block;width:100%}",
      ".simple-briefing table[data-board-table] colgroup,.simple-briefing table[data-board-table] thead{display:none}",
      ".simple-briefing table[data-board-table] tbody tr{display:grid;grid-template-columns:32px minmax(0,1fr);gap:4px 10px;padding:14px 0;border-bottom:1px solid var(--line-soft);background:transparent}",
      ".simple-briefing table[data-board-table] td{display:block;min-width:0;padding:0;border:0}.simple-briefing table[data-board-table] .rank{grid-column:1;grid-row:1/3;padding-top:2px;text-align:left;color:var(--faint)}",
      ".simple-briefing table[data-board-table] .player-cell{grid-column:2;font-size:15px}.simple-briefing table[data-board-table] .player-cell b,.simple-briefing table[data-board-table] .player-cell small{overflow-wrap:anywhere}",
      ".simple-briefing table[data-board-table] .class-cell{grid-column:2;color:var(--muted)}.simple-briefing table[data-board-table] .number{grid-column:2;display:flex;justify-content:space-between;gap:12px;margin-top:4px;padding-top:7px;border-top:1px dotted var(--line-soft);text-align:right}",
      ".simple-briefing table[data-board-table] .number:before{content:attr(data-label);color:var(--faint);font-size:10px;font-weight:700;letter-spacing:.06em;text-align:left;text-transform:uppercase}}"
    ].join("");
    return "<!doctype html><html><head><meta charset=\"utf-8\">" +
      "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
      "<title>Simple report</title><style>" + commonCss +
      simpleCss + "</style></head><body class=\"simple-report\"><main class=\"wrap\">" +
      body + "</main></body></html>";
  }

  function fightDurationMs(value) {
    var text=String(value || "");
    var minutes=Number((text.match(/(\d+)m/) || [])[1] || 0);
    var seconds=Number((text.match(/(\d+)s/) || [])[1] || 0);
    var millis=Number((text.match(/(\d+)ms/) || [])[1] || 0);
    return minutes*60000+seconds*1000+millis;
  }
  function fightOutcome(fight) {
    var score=0,kills=Number(fight.kills || 0),deaths=Number(fight.ally_deaths || 0);
    var downs=Number(fight.downs || 0),ourDowns=Number(fight.ally_downs || 0);
    var dealt=Number(fight.damage_out || 0),taken=Number(fight.damage_in || 0);
    if (kills > deaths) score+=1; else if (deaths > kills) score-=1;
    if (downs > ourDowns) score+=1; else if (ourDowns > downs) score-=1;
    if (dealt > taken*1.15) score+=1; else if (taken > dealt*1.5) score-=1;
    return score >= 2 ? "good" : score <= -2 ? "bad" : "mixed";
  }
  function fightsTable(showAll) {
    var fights = model.fights || [];
    if (!fights.length) return "<p class=\"empty\">No fight rows were found.</p>";
    var hasFightLogs = fights.some(function (fight) {
      return fight.report_url || fight.log_url;
    });
    return "<article class=\"board fights-board\"><h3>Fight Summaries</h3>" +
      "<div class=\"fight-legend\"><span class=\"good\">Strong fight</span><span class=\"mixed\">Mixed / inconclusive</span><span class=\"bad\">Got smashed</span></div>" +
      "<div class=\"table-panel table-wrap\"><table class=\"fights-table" + (hasFightLogs ? " has-fight-logs" : "") + "\" data-initial-limit=\"5\"><colgroup>" +
      "<col class=\"fight-col-index\"><col class=\"fight-col-time\"><col class=\"fight-col-duration\"><col class=\"fight-col-count\"><col class=\"fight-col-count\"><col class=\"fight-col-count\"><col class=\"fight-col-count\"><col class=\"fight-col-damage\"><col class=\"fight-col-damage\">" +
      (hasFightLogs ? "<col class=\"fight-col-log\">" : "") + "</colgroup><thead><tr>" +
      "<th class=\"number\"><button type=\"button\" data-sort-key=\"index\">#</button></th>" +
      "<th><button type=\"button\" data-sort-key=\"time\">Time</button></th>" +
      "<th><button type=\"button\" data-sort-key=\"duration\">Duration</button></th>" +
      "<th class=\"number\"><button type=\"button\" data-sort-key=\"squad\">Squad</button></th>" +
      "<th class=\"number\"><button type=\"button\" data-sort-key=\"enemy\">Enemy</button></th>" +
      "<th class=\"number\"><button type=\"button\" data-sort-key=\"downs\">Downs</button></th>" +
      "<th class=\"number\"><button type=\"button\" data-sort-key=\"kills\">Kills</button></th>" +
      "<th class=\"number\" title=\"Damage dealt\"><button type=\"button\" data-sort-key=\"damage-out\">Dmg Out</button></th>" +
      "<th class=\"number\" title=\"Damage received\"><button type=\"button\" data-sort-key=\"damage-in\">Dmg In</button></th>" +
      (hasFightLogs ? "<th>Fight log</th>" : "") +
      "</tr></thead><tbody>" + fights.map(function (f,index) {
        var reportUrl = f.report_url || f.log_url,outcome=fightOutcome(f);
        return "<tr class=\"fight-outcome-" + outcome + (index >= 5 && !showAll ? " board-extra" : "") + "\"" +
          " data-index=\"" + esc(f.index || 0) + "\" data-time=\"" + esc(f.index || 0) +
          "\" data-duration=\"" + esc(fightDurationMs(f.duration)) + "\" data-squad=\"" + esc(f.squad || 0) +
          "\" data-enemy=\"" + esc(f.enemy || 0) + "\" data-downs=\"" + esc(f.downs || 0) +
          "\" data-kills=\"" + esc(f.kills || 0) + "\" data-damage-out=\"" + esc(f.damage_out || 0) +
          "\" data-damage-in=\"" + esc(f.damage_in || 0) + "\" data-outcome=\"" + outcome + "\"" +
          drillAttrs("fight-row", "Fight " + (f.index || "—"),
          "Squad " + fmt(f.squad) + " vs " + fmt(f.enemy) + " · " +
          fmt(f.downs) + " downs · " + fmt(f.kills) + " kills",
          "Observed fight summary · " + (f.time_label || "time unavailable")) +
          (index >= 5 && !showAll ? " hidden" : "") +
          "><td class=\"number\" data-label=\"Fight\">" + fmt(f.index) + "</td><td data-label=\"Time\">" +
          "<span title=\"" + esc(f.time_label || "Time unavailable") + "\">" +
          esc(fightClock(f.time_label) || "—") + "</span></td><td data-label=\"Duration\">" + fmt(f.duration) + "</td>" +
          "<td class=\"number\" data-label=\"Squad\">" + fmt(f.squad) + "</td>" +
          "<td class=\"number\" data-label=\"Enemy\">" + fmt(f.enemy) + "</td>" +
          "<td class=\"number\" data-label=\"Downs\">" + fmt(f.downs) + "</td>" +
          "<td class=\"number\" data-label=\"Kills\">" + fmt(f.kills) + "</td>" +
          "<td class=\"number\" data-label=\"Damage Out\">" + fmt(f.damage_out) + "</td>" +
          "<td class=\"number\" data-label=\"Damage In\">" + fmt(f.damage_in) + "</td>" +
          (hasFightLogs ? "<td data-label=\"Fight Log\">" + (reportUrl ? "<a class=\"fight-log-link\" href=\"" +
            esc(reportUrl) + "\" target=\"_blank\" rel=\"noopener noreferrer\" " +
            "onclick=\"event.stopPropagation()\">Open Fight Log</a>" : "") + "</td>" : "") +
          "</tr>";
      }).join("") + "</tbody></table></div>" + (!showAll && fights.length > 5 ? "<footer class=\"board-actions\"><button type=\"button\" data-expand-board aria-expanded=\"false\">Expand all " + fights.length + "</button></footer>" : "") + "</article>";
  }
  function poisonTable() {
    var rows = (model.poison || []).slice().sort(function (a, b) {
      return (b.apps_per_min || 0) - (a.apps_per_min || 0);
    });
    if (!rows.length) return "<p class=\"empty\">No poison coverage data was found.</p>";
    var initialLimit=5;
    return "<article class=\"board poison-board\"><h3>Poison Applications</h3>" +
      "<div class=\"table-wrap\"><table class=\"poison-table\" data-board-table><colgroup>" +
      "<col class=\"poison-rank\"><col class=\"poison-player\"><col class=\"poison-class\">" +
      "<col class=\"poison-apps\"><col class=\"poison-rate\"><col class=\"poison-output\"></colgroup>" +
      "<thead><tr><th>#</th><th>Player</th><th>Class</th>" +
      "<th class=\"number\"><button type=\"button\" data-sort-key=\"total\">Applications</button></th>" +
      "<th class=\"number\" aria-sort=\"descending\"><button type=\"button\" data-sort-key=\"rate\">Apps / min</button></th>" +
      "<th class=\"number\">Poison / sec</th></tr></thead><tbody>" + rows.map(function (r, i) {
        return "<tr" + drillAttrs("condition-row", r.name || "Poison output",
          fmt(r.apps) + " applications · " + fmt(r.apps_per_min) + " per minute",
          "Observed poison output; relic trigger attribution unavailable") +
          " data-total=\"" + esc(r.apps || 0) + "\" data-rate=\"" + esc(r.apps_per_min || 0) + "\"" +
          (i >= initialLimit ? " class=\"board-extra poison-extra\" hidden" : "") +
          "><td class=\"number\">" + (i + 1) + "</td><td class=\"poison-player-cell\"><b>" +
          esc(r.name) + "</b><small>" + esc(r.account) + "</small></td><td>" +
          professionInline(r.prof || r.profession || "Unknown") +
          "</td><td class=\"number\">" + fmt(r.apps) +
          "</td><td class=\"number\">" + fmt(r.apps_per_min) +
          "</td><td class=\"number\">" + fmt(r.output) + "</td></tr>";
      }).join("") + "</tbody></table></div>" + (rows.length > initialLimit ?
        "<footer class=\"board-actions\"><button type=\"button\" data-expand-board aria-expanded=\"false\">Expand all " +
        rows.length + "</button></footer>" : "") + "</article>";
  }
  function squadCards() {
    var squads = model.squad_composition && model.squad_composition.squads || [];
    if (!squads.length) return "";
    return sectionHead("Observed squad", "Squad Composition by Party",
      "Party numbers come directly from the exported squad table. Open a fight, then select a player for tonight’s full profile.") +
      "<div class=\"squad-fights\">" + squads.map(function (s,index) {
        var groups={};(s.players || []).forEach(function(player){var party=Number(player.party || 0);
          if (!party) party=Math.floor((groups.__ungrouped_count || 0)/5)+1;
          groups.__ungrouped_count=(groups.__ungrouped_count || 0)+(!player.party ? 1 : 0);
          (groups[party] || (groups[party]=[])).push(player);});delete groups.__ungrouped_count;
        var parties=Object.keys(groups).map(Number).sort(function(a,b){return a-b;});
        return "<details class=\"squad-fight\"" + (index === 0 ? " open" : "") + "><summary><b>Fight " +
          fmt(s.fight) + "</b><span>" + fmt((s.players || []).length) + " players · " + fmt(parties.length) +
          " parties</span></summary><div class=\"our-party-list\">" + parties.map(function(party){
            return "<div class=\"our-party-row\"><b>Party " + fmt(party) + "</b><div class=\"our-party-members\">" +
              groups[party].map(function(player){var hover=(player.name || "Player") + " · " +
                (player.profession || "Class unavailable") + " · Party " + party;
                return "<button type=\"button\" class=\"our-party-player\" title=\"" + esc(hover) + "\"" +
                  drillAttrs("session-player",player.name || "Player",hover,
                    "Observed squad composition · Fight " + s.fight + " · Party " + party) + ">" +
                  professionGlyph(player.profession || "Unknown") + "<span>" + esc(player.name || "Player") +
                  "</span></button>";}).join("") + "</div></div>";}).join("") + "</div></details>";
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
  function tableBySource(sourceKey) {
    return (model.stat_tables || []).find(function(board) {
      return String(board.source_key || "") === String(sourceKey || "");
    }) || null;
  }
  function derivedMetricBoard(label, sourceKey, totalKey, rateKey, rateLabel, perMinute) {
    var source=tableBySource(sourceKey);
    if (!source) return null;
    var rows=(source.rows || []).map(function(row) {
      var metrics=row.metrics || {}, hasTotal=metrics[totalKey] != null;
      var hasRate=rateKey && metrics[rateKey] != null;
      if (!hasTotal && !hasRate) return null;
      var total=hasTotal ? Number(metrics[totalKey]) : null;
      var participation=Number(row.participation_time || metrics.fighttime || metrics.activetime || 0);
      var rate=hasRate ? Number(metrics[rateKey]) :
        (total != null && participation ? total / participation * (perMinute ? 60 : 1) : null);
      return Object.assign({},row,{total:total,rate:rate,value:rate != null ? rate : total,
        participation_time:participation || null,
        fight_count:row.fight_count != null ? row.fight_count : metrics.numfights});
    }).filter(Boolean);
    if (!rows.length) return null;
    return {stat:label,source_key:sourceKey,source_tiddler:source.source_tiddler,
      scope:"session",value_label:rateLabel,rows:rows,metric:{label:label,total_label:label,
        rate_label:rateLabel,rate_unit:perMinute ? "per_minute" : "per_second"}};
  }
  function derivedDamageBoard(label, totalKey, rateKey, rateLabel) {
    return derivedMetricBoard(label,"Damage",totalKey,rateKey,rateLabel,false);
  }
  function metricBoardBars(board, tone) {
    if (!board || !(board.rows || []).length) return "";
    var rows=(board.rows || []).slice().sort(function(a,b){
      return weightedRowValue(b)-weightedRowValue(a);
    }).slice(0,5);
    var max=Math.max.apply(null,rows.map(function(row){return Math.abs(weightedRowValue(row));}).concat([1]));
    var rateLabel=board.metric && board.metric.rate_label || board.value_label || titleCase(board.stat);
    return "<div class=\"metric-chart-grid\"><div class=\"bar-card tone-" + esc(tone || "accent") +
      "\"><div class=\"chart-title\"><b>" + esc(board.stat) + "</b><span>" + esc(rateLabel) +
      " · highest first</span></div>" + rows.map(function(row){
        var value=weightedRowValue(row),width=Math.max(2,Math.abs(value)/max*100);
        return "<button type=\"button\" class=\"metric-row\"" + drillAttrs("session-player",
          row.name || board.stat, rateLabel + " " + fmt(value) +
          (row.total != null ? " · Total " + fmt(row.total) : ""),
          "Selected night · " + (board.source_tiddler || board.stat)) +
          ">" + playerBarLabel(row) + "<i><em style=\"width:" +
          width.toFixed(1) + "%\"></em></i><b>" + fmt(value) + "</b></button>";
      }).join("") + "</div></div>";
  }
  function metricBoardView(board, tone, includeTable) {
    if (!board) return "<p class=\"empty\">This metric was not exported for the selected night.</p>";
    return metricBoardBars(board,tone) + (includeTable === false ? "" :
      "<div class=\"boards\">" + boardCard(board,100) + "</div>");
  }
  function powerDamageView() {
    var board=derivedDamageBoard("Power Damage","targetpower","targetpowerps","Power DPS");
    return metricBoardView(board,"power",true) + highScoreGridExact(["Highest 1s Burst Damage"]);
  }
  function conditionDamageView() {
    var board=derivedDamageBoard("Condition Damage","targetcondition","targetconditionps","Condition DPS");
    var applications=tableBySource("Conditions-Out");
    return metricBoardView(board,"condition",true) + poisonContext() + poisonTable() +
      (applications ? sectionHead("Applications", "Conditions Applied", "Application counts are separate from Condition Damage.") +
        "<div class=\"boards\">" + boardCard(applications,100) + "</div>" : "");
  }
  function damageCompositionView() {
    var source=tableBySource("Damage");
    if (!source) return "<p class=\"empty\">No player Damage table was exported.</p>";
    var rows=(source.rows || []).map(function(row){var metrics=row.metrics || {};
      return {row:row,total:Number(metrics.targetdamageps || 0),power:Number(metrics.targetpowerps || 0),
        condition:Number(metrics.targetconditionps || 0)};}).sort(function(a,b){return b.total-a.total;}).slice(0,5);
    var max=Math.max.apply(null,rows.map(function(item){return item.total;}).concat([1]));
    return "<article class=\"bar-card damage-composition-card\"><div class=\"chart-title\"><b>Total DPS · Power + Condition</b>" +
      "<span>Sorted by total DPS · colored segments show the damage profile</span></div><div class=\"damage-composition-legend\"><span class=\"power\">Power DPS</span><span class=\"condition\">Condition DPS</span><span>Total DPS</span></div>" +
      rows.map(function(item){var row=item.row,totalWidth=Math.max(2,item.total/max*100),powerShare=item.total ? item.power/item.total*100 : 0,
        conditionShare=item.total ? item.condition/item.total*100 : 0;
        return "<button type=\"button\" class=\"damage-composition-row\"" + drillAttrs("session-player",row.name || "Player",
          "Total DPS " + fmt(item.total) + " · Power DPS " + fmt(item.power) + " · Condition DPS " + fmt(item.condition),
          "Selected night · Damage to enemy players") + "><span class=\"damage-player\">" + professionInline(row.profession || "Unknown") +
          "<span><b>" + esc(row.name || "Player") + "</b><small>Power " + fmt(item.power) + " · Condition " + fmt(item.condition) +
          "</small></span></span><span class=\"damage-total-track\"><i style=\"width:" + totalWidth.toFixed(1) + "%\"><em class=\"power\" style=\"width:" +
          Math.max(0,powerShare).toFixed(1) + "%\"></em><em class=\"condition\" style=\"width:" + Math.max(0,conditionShare).toFixed(1) +
          "%\"></em></i></span><strong>" + fmt(item.total) + " DPS</strong></button>";}).join("") + "</article>";
  }
  function fightImpactBoards(includeTables) {
    var boards=[derivedMetricBoard("Down-Contribution Damage","Offensive-Summary","downcontribution",null,"Down Contribution / sec",false),
      derivedMetricBoard("Enemy Downs","Offensive-Summary","downed",null,"Enemy Downs / min",true),
      derivedMetricBoard("Enemy Kills","Offensive-Summary","killed",null,"Enemy Kills / min",true)].filter(Boolean);
    return boards.length ? boards.map(function(board){return metricBoardView(board,"danger",includeTables);}).join("") :
      "<p class=\"empty\">No fight-conversion player table was exported.</p>";
  }
  function supportMetric(label,key,rateLabel) {
    return derivedMetricBoard(label,"Support-Summary",key,null,rateLabel,true);
  }
  function offensiveMetric(label,key,rateLabel) {
    return derivedMetricBoard(label,"Offensive-Summary",key,null,rateLabel,true);
  }
  function supportOverviewView() {
    var boards=[supportMetric("Condition Cleanses","condicleanse","Cleanses / min"),
      supportMetric("Boons Removed","boonstrips","Boons Removed / min"),
      offensiveMetric("Crowd Control","appliedcrowdcontrol","Crowd Control / min"),
      supportMetric("Resurrects","resurrects","Resurrects / min")].filter(Boolean);
    return boards.length ? "<div class=\"curated-metric-stack\">" + boards.map(function(board){
      return metricBoardBars(board,"support");}).join("") + "</div>" :
      "<p class=\"empty\">No support summary was exported for this night.</p>";
  }
  function cleansesView() {
    return metricBoardView(supportMetric("Condition Cleanses","condicleanse","Cleanses / min"),"support",true);
  }
  function stripsAndControlView() {
    var strips=supportMetric("Boons Removed","boonstrips","Boons Removed / min");
    var control=offensiveMetric("Crowd Control","appliedcrowdcontrol","Crowd Control / min");
    return metricBoardView(strips,"control",true) + metricBoardView(control,"control",true);
  }
  function resurrectView() {
    var resurrects=supportMetric("Resurrects","resurrects","Resurrects / min");
    var combat=tableBySource("Combat-Resurrect");
    return metricBoardView(resurrects,"heal",true) +
      (combat ? sectionHead("Resurrection sustain", "Combat-Resurrection Healing",
        "Healing delivered by resurrection-related skills; this is not an overall support score.") +
        metricBoardView(combat,"heal",true) : "");
  }
  function healMetric(label,totalKey,rateKey,rateLabel) {
    return derivedMetricBoard(label,"Heal-Stats",totalKey,rateKey,rateLabel,false);
  }
  function healingOverviewView() {
    var boards=[healMetric("Healing","healing","healingps","Healing / sec"),
      healMetric("Barrier","barrier","barrierps","Barrier / sec"),
      healMetric("Downed-Ally Healing","downedhealing","downedhealingps","Downed Healing / sec")].filter(Boolean);
    return boards.length ? "<div class=\"curated-metric-stack\">" + boards.map(function(board){
      return metricBoardBars(board,"heal");}).join("") + "</div>" :
      "<p class=\"empty\">No healing statistics were exported for this night.</p>";
  }
  function healingAndBarrierView() {
    return metricBoardView(healMetric("Healing","healing","healingps","Healing / sec"),"heal",true) +
      metricBoardView(healMetric("Barrier","barrier","barrierps","Barrier / sec"),"support",true);
  }
  function healingProfilesView() {
    var source=tableBySource("Heal-Stats"), grouped={};
    (source && source.rows || []).forEach(function(row){
      var profession=row.profession || "Unknown",metrics=row.metrics || {},time=Number(row.participation_time || metrics.fighttime || 0);
      var item=grouped[profession] || (grouped[profession]={profession:profession,time:0,healing:0,barrier:0,players:0});
      item.time += time; item.healing += Number(metrics.healing || 0); item.barrier += Number(metrics.barrier || 0); item.players += 1;
    });
    var rows=Object.keys(grouped).map(function(key){var item=grouped[key];return {name:key,profession:key,
      total:item.healing,rate:item.time ? item.healing/item.time : 0,participation_time:item.time,
      fight_count:item.players,metrics:{healing:item.healing,healingps:item.time ? item.healing/item.time : 0,
        barrier:item.barrier,barrierps:item.time ? item.barrier/item.time : 0,players:item.players}};});
    var board=rows.length ? {stat:"Healing by Profession",source_key:"Heal-Stats",source_tiddler:source.source_tiddler,
      value_label:"Healing / sec",metric:{total_label:"Healing",rate_label:"Healing / sec"},rows:rows} : null;
    return metricBoardView(board,"heal",true);
  }
  function weightedRowValue(row) {
    if (row.rate != null) return Number(row.rate);
    if (row.participation_weighted_rate != null) return Number(row.participation_weighted_rate);
    if (row.value_per_minute != null) return Number(row.value_per_minute);
    if (row.per_minute != null) return Number(row.per_minute);
    return Number(row.value);
  }
  function sourceBoardGrid() {
    var boards = allBoards();
    var historical=(model.leaderboards || []).filter(function(board){return (board.rows || []).length;});
    if (!boards.length && !historical.length) return "<p class=\"empty\">No source tables were available.</p>";
    return "<div class=\"detail-counts\"><span><b>" +
      fmt(historical.length) + "</b> historical leaderboards</span><span><b>" +
      fmt(boards.length) + "</b> tonight’s stat tables</span><span><b>" +
      fmt((model.high_scores && model.high_scores.blocks || []).length) +
      "</b> high-score blocks</span><span><b>" + fmt((model.poison || []).length) +
      "</b> poison rows</span></div><div class=\"tonight-source-tables\">" + sectionHead("Selected night", "Tonight’s Source Tables",
        "Only players present in the selected night’s exported session tables appear here.") + "<div class=\"boards\">" +
      boards.map(function (board) { return boardCard(board, 100); }).join("") +
      "</div></div>" + (historical.length ? "<div class=\"historical-source-tables\">" + sectionHead("Historical · not tonight",
        "Historical Leaderboards", "Cross-raid averages are context only; these players did not necessarily participate tonight.") +
        longTermLeaderboardGrid() + "</div>" : "");
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
    series=series.filter(function(s){
      if(s.key === "kills" || s.key === "downs") return true;
      return fights.some(function(f){return f[s.key] != null;});
    });
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
            String(value), "Source: Overview fight summary") +
          "><title>Fight " + esc(f.index || fightIndex + 1) + " · " + s.label +
          ": " + value + "</title></rect>";
      }).join("");
    }).join("");
    return "<div class=\"chart-card outcome-chart\"><div class=\"chart-title\"><b>Fight outcomes</b>" +
      "<span>Select a bar for fight details</span></div>" +
      "<div class=\"chart-legend\">" + series.map(function(s){return "<span class=\"key-" +
      s.cls + "\">" + s.label + "</span>";}).join("") + "</div><div class=\"svg-scroll\"><svg " +
      "viewBox=\"0 0 " + width + " " + height + "\" role=\"img\" " +
      "aria-label=\"Kills, enemy downs, our downs, and our deaths by fight\"><line x1=\"15\" y1=\"" +
      baseY + "\" x2=\"" + (width - 15) + "\" y2=\"" + baseY +
      "\" class=\"axis\"></line>" + bars + "</svg></div></div>";
  }
  function fightPulse() { return outcomeChart(); }
  function metricBars(terms, title, tone, limit) {
    var chartLimit=Math.min(5,limit || 5);
    var chartBoards=boardsFor(terms).map(function(board){
      var rows=(board.rows || []).map(function(row){return {row:row,weighted:weightedRowValue(row)};})
        .filter(function(item){return Number.isFinite(item.weighted);})
        .sort(function(a,b){return b.weighted-a.weighted;}).slice(0,chartLimit);
      return {board:board,rows:rows};
    }).filter(function(item){return item.rows.length;}).slice(0,4);
    if (!chartBoards.length) return "<p class=\"empty\">No chartable values were exported.</p>";
    return "<div class=\"metric-chart-grid\">" + chartBoards.map(function(group){
      var board=group.board, rows=group.rows;
      var max=Math.max.apply(null,rows.map(function(item){return Math.abs(item.weighted);}).concat([1]));
      var rateLabel=(board.metric && board.metric.rate_label) || board.value_label || titleCase(board.stat);
      var heading=titleCase(board.stat);
      return "<div class=\"bar-card tone-" + esc(tone || "accent") + "\"><div class=\"chart-title\"><b>" +
        esc(heading) + "</b><span>" + esc(rateLabel) + " · highest first</span></div>" + rows.map(function(item){
          var row=item.row,value=item.weighted,width=Math.max(2,Math.abs(value)/max*100);
          return "<button type=\"button\" class=\"metric-row\"" + drillAttrs("session-player",
            row.name || heading, rateLabel + " " + fmt(value) +
            (row.total != null ? " · Total " + fmt(row.total) : ""),
            "Source: " + (board.source_tiddler || titleCase(board.stat))) +
            ">" + playerBarLabel(row) + "<i><em style=\"width:" +
            width.toFixed(1) + "%\"></em></i><b>" + fmt(value) + "</b></button>";
        }).join("") + "</div>";
    }).join("") + "</div>";
  }
  function boonEconomyContext() {
    var uptime=(model.stat_tables || []).find(function(board){return board.source_key === "Uptimes" || board.stat === "Uptimes";});
    var stability=(model.stat_tables || []).find(function(board){return board.stat === "Stability Generation";});
    var support=(model.stat_tables || []).find(function(board){return board.stat === "Support - Summary";});
    var pressure=model.enemy_intel && model.enemy_intel.session_pressure || {};
    if (!uptime && !stability && !support) return "";
    function metricSum(board,key){return (board && board.rows || []).reduce(function(sum,row){return sum+(Number(row.metrics && row.metrics[key])||0);},0);}
    function weightedUptime(key){var total=0,time=0;(uptime && uptime.rows || []).forEach(function(row){
      var seconds=Number(row.participation_time || row.metrics && row.metrics.fighttime || 0), value=Number(row.metrics && row.metrics[key]);
      if (seconds && Number.isFinite(value)){total += value*seconds;time += seconds;}});return time ? total/time : null;}
    var stabilityTotal=(stability && stability.rows || []).reduce(function(sum,row){return sum+(Number(row.total)||0);},0);
    var stabilityTime=(stability && stability.rows || []).reduce(function(sum,row){return sum+(Number(row.participation_time)||0);},0);
    var outgoingRemoval=metricSum(support,"boonstrips");
    var incoming=(pressure.incoming_strips || [])[0] || {}, incomingRemoval=Number(incoming.count || 0);
    var combatSeconds=Number(pressure.damage_profile && pressure.damage_profile.combat_seconds || 0);
    var uptimes=["stability","protection","aegis","resolution","resistance"].map(function(key){return {key:key,value:weightedUptime(key)};})
      .filter(function(row){return row.value != null;});
    return "<article class=\"boon-economy\"><div class=\"chart-title\"><b>Boon Economy</b><span>Generation · uptime · removal</span></div>" +
      "<div class=\"economy-kpis\"><div><span>Stability generated</span><b>" + fmt(stabilityTotal) +
      "</b><small>" + fmt(combatSeconds ? stabilityTotal/combatSeconds : (stabilityTime ? stabilityTotal/stabilityTime : 0)) + "/sec squad</small></div>" +
      "<div><span>Boon removals by us</span><b>" + fmt(outgoingRemoval) + "</b><small>" +
      fmt(combatSeconds ? outgoingRemoval*60/combatSeconds : 0) + "/min</small></div>" +
      "<div><span>Boon removals received</span><b>" + fmt(incomingRemoval) + "</b><small>" +
      fmt(Number(incoming.rate_per_combat_minute || (combatSeconds ? incomingRemoval*60/combatSeconds : 0))) + "/min</small></div></div>" +
      "<div class=\"economy-uptimes\">" + uptimes.map(function(row){return "<span><b>" + esc(titleCase(row.key)) +
        " " + fmt(row.value) + "%</b><small>weighted uptime</small></span>";}).join("") + "</div>" +
      "<p>Uptime is the result after generation, boon duration, deaths, and enemy removal. These values stay separate; no combined score is invented.</p></article>";
  }
  function boonGenerationCharts() {
    var boards=(model.stat_tables || []).filter(function(board){return /generation/i.test(String(board.stat || ""));});
    if (!boards.length) return "";
    return "<section class=\"boon-generation\"><div class=\"section-head\"><div><span class=\"eyebrow\">What We Did Well · By Profession</span>" +
      "<h2>Boon generation</h2></div><p>Weighted by each player’s fight time; total generation remains visible.</p></div>" +
      boards.map(function(board){
        var professions={};
        (board.rows || []).forEach(function(row){var profession=row.profession || "Unknown";
          var item=professions[profession] || {profession:profession,total:0,time:0,players:0};
          item.total += Number(row.total != null ? row.total : row.metrics && row.metrics.totalgen || 0);
          item.time += Number(row.participation_time || row.metrics && row.metrics.fighttime || 0);
          item.players += 1; professions[profession]=item;});
        var rows=Object.keys(professions).map(function(key){var item=professions[key];
          item.rate=item.time ? item.total/item.time : 0; return item;})
          .sort(function(a,b){return b.rate-a.rate;});
        var max=Math.max.apply(null,rows.map(function(row){return row.rate;}).concat([1]));
        var rateLabel=board.metric && board.metric.rate_label || "Generation / sec";
        return "<article class=\"bar-card generation-card\"><div class=\"chart-title\"><b>" +
          esc(titleCase(board.stat)) + " by Profession</b><span>" + esc(rateLabel) + " · highest first</span></div>" +
          rows.map(function(row){return "<div class=\"generation-row\"><span>" + professionInline(row.profession) +
            "</span><i><em style=\"width:" + Math.max(2,row.rate/max*100).toFixed(1) + "%\"></em></i>" +
            "<b>" + fmt(row.rate) + "/s</b><small>" + fmt(row.total) + " total · " + fmt(row.players) +
            " player" + (row.players === 1 ? "" : "s") + "</small></div>";}).join("") + "</article>";
      }).join("") + "</section>";
  }
  function boonUptimeCharts(keys, title) {
    var board=(model.stat_tables || []).find(function(item){return item.source_key === "Uptimes" || item.stat === "Uptimes";});
    if (!board) return "";
    var cards=(keys || []).map(function(key){
      var rows=(board.rows || []).map(function(row){return {name:row.name,account:row.account,
        profession:row.profession,value:Number(row.metrics && row.metrics[key]),
        participation:row.participation_time,fights:row.fight_count};})
        .filter(function(row){return Number.isFinite(row.value);})
        .sort(function(a,b){return b.value-a.value;}).slice(0,5);
      if (!rows.length) return "";
      var max=Math.max.apply(null,rows.map(function(row){return row.value;}).concat([1]));
      return "<article class=\"bar-card boon-card\"><div class=\"chart-title\"><b>" +
        esc(titleCase(key)) + " Uptime</b><span>Top 5 · participation-weighted</span></div>" +
        rows.map(function(row){return "<div class=\"boon-row\"><span class=\"boon-player\">" +
          professionGlyph(row.profession || "Unknown") + "<span><b>" + esc(row.name || "Player") +
          "</b><small>" + esc(row.profession || "Class unavailable") + " · " +
          humanDuration(Number(row.participation || 0)) + " · " + fmt(row.fights) + " fights</small></span></span>" +
          "<i><em style=\"width:" + Math.max(2,row.value/max*100).toFixed(1) + "%\"></em></i><strong>" +
          fmt(row.value) + "%</strong></div>";}).join("") + "</article>";
    }).filter(Boolean).join("");
    return cards ? "<section class=\"boon-uptimes\"><div class=\"section-head\"><div><span class=\"eyebrow\">" +
      esc(title) + "</span><h2>Uptime leaders</h2></div><p>Stability is shown first; each chart is sorted by uptime.</p></div>" +
      "<div class=\"boon-grid\">" + cards + "</div></section>" : "";
  }
  function conditionHeatmap(pressure) {
    pressure = pressure || {};
    var normalized = pressure.condition_profile && pressure.condition_profile.normalized || [];
    var damaging = /^(bleeding|burning|confusion|poison|torment)$/i;
    var seen = {};
    var sourceRows = normalized.length ? normalized : (pressure.conditions_in || []).filter(function(row){
      return damaging.test(String(row.effect || row.name || row.condition || ""));
    });
    var rows = sourceRows.filter(function(row) {
        var key=String(row.effect || row.name || row.condition || "").toLowerCase();
        if (!key || seen[key]) return false;
        seen[key]=true; return true;
      }).slice(0, 18);
    if (!rows.length) return "<p class=\"empty\">No condition heatmap values were exported.</p>";
    var max = Math.max.apply(null, rows.map(function(row){return Number(row.uptime_percent || row.count || row.value || 0);}).concat([1]));
    return "<div class=\"heatmap\" aria-label=\"Incoming condition heatmap\">" + rows.map(function(row){
      var label=row.effect || row.name || row.condition || "Condition";
      var value=Number(row.uptime_percent || row.count || row.value || 0);
      var level=Math.max(.18,value/max);
      return "<button type=\"button\" class=\"heat-cell\" style=\"--heat:" + level.toFixed(2) + "\"" +
        drillAttrs("heatmap-cell", label + " · " + fmt(value) + "%",
          "Average uptime across squad active time", "Source: " +
          (row.source || pressure.condition_profile && pressure.condition_profile.source || "Conditions-In") +
          " · Session-wide enemy pressure") +
        "><b>" + esc(label) + "</b><span>" + fmt(value) +
        (row.uptime_percent != null ? "% uptime" : "") + "</span></button>";
    }).join("") + "</div>";
  }
  function pressureBars(rows, title, tone, metric) {
    rows = (rows || []).slice().sort(function(a,b) {
      function amount(row){return Number(metric && row[metric] != null ? row[metric] :
        row.damage || row.count || row.uptime_percent || row.value || 0);}
      return amount(b)-amount(a);
    }).slice(0, 10);
    if (!rows.length) return "<p class=\"empty\">No observed values exported for this metric.</p>";
    var values=rows.map(function(row){return Number(metric && row[metric] != null ? row[metric] :
      row.damage || row.count || row.uptime_percent || row.value || 0);});
    var max=Math.max.apply(null, values.concat([1]));
    return "<div class=\"bar-card tone-" + esc(tone || "enemy") + "\"><div class=\"chart-title\"><b>" +
      esc(title) + "</b><span>Across all modeled fights</span></div>" + rows.map(function(row,index){
        var label=row.skill || row.effect || row.name || "Entry", value=values[index];
        var detail = (row.damage != null ? fmt(value) + " damage" :
          row.uptime_percent != null ? fmt(value) + "% average uptime" : fmt(value) + " events") +
          (metric === "connected_hits" ? " connected hits · " + fmt(row.damage) + " damage" : "") +
          (row.total_casts > 0 ? " · " + fmt(row.total_casts) + " recorded casts" : "") +
          (row.rate_per_combat_minute != null ? " · " + fmt(row.rate_per_combat_minute) + " per minute" : "") +
          (row.percent != null ? " · " + fmt(row.percent) + "% of incoming damage" : "");
        return "<button type=\"button\" class=\"metric-row pressure-row\"" + drillAttrs("chart-bar", label,
          detail, "Source: " + (row.source || readableLabel(row.source_scope || "session report")) +
          " · Session-wide enemy pressure") + "><span class=\"bar-label\" title=\"" + esc(label) + "\">" + esc(label) +
          "</span><i><em style=\"width:" + Math.max(2,value/max*100).toFixed(1) + "%\"></em></i><b>" +
          fmt(value) + "</b></button>";
      }).join("") + "</div>";
  }
  function enemySkillPressureTable(pressure) {
    var rows=(pressure && pressure.top_damage_skills || []).slice().sort(function(a,b){
      return Number(b.damage || 0)-Number(a.damage || 0);
    });
    if (!rows.length) return "";
    var limit=10;
    return "<article class=\"board enemy-skill-board\"><div class=\"chart-title\"><b>Enemy Skill Pressure</b>" +
      "<span>Damage first · sort by hits or casts</span></div><p class=\"skill-table-note\">Connected hits are the best frequency signal for pulsing fields. A missing cast count is shown as —, not zero.</p>" +
      "<div class=\"table-wrap\"><table class=\"enemy-skill-table\" data-board-table data-initial-limit=\"" + limit + "\">" +
      "<colgroup><col class=\"skill-rank\"><col class=\"skill-name\"><col class=\"skill-damage\"><col class=\"skill-share\"><col class=\"skill-hits\"><col class=\"skill-casts\"><col class=\"skill-per-hit\"></colgroup>" +
      "<thead><tr><th>#</th><th>Skill</th><th class=\"number\" aria-sort=\"descending\"><button type=\"button\" data-sort-key=\"damage\">Damage</button></th>" +
      "<th class=\"number\"><button type=\"button\" data-sort-key=\"share\">Share</button></th>" +
      "<th class=\"number\"><button type=\"button\" data-sort-key=\"hits\">Connected Hits</button></th>" +
      "<th class=\"number\"><button type=\"button\" data-sort-key=\"casts\">Cast Count</button></th>" +
      "<th class=\"number\"><button type=\"button\" data-sort-key=\"perhit\">Damage / Hit</button></th></tr></thead><tbody>" +
      rows.map(function(row,index){
        var damage=Number(row.damage || 0),hits=Number(row.connected_hits || 0),casts=Number(row.total_casts || 0);
        var perHit=hits ? damage/hits : 0;
        return "<tr data-damage=\"" + damage + "\" data-share=\"" + Number(row.percent || 0) +
          "\" data-hits=\"" + hits + "\" data-casts=\"" + casts + "\" data-perhit=\"" + perHit + "\"" +
          (index >= limit ? " class=\"board-extra\" hidden" : "") + "><td class=\"number\" data-rank-cell>" +
          fmt(index+1) + "</td><td title=\"" + esc(row.skill || "Unknown skill") + "\"><b>" +
          esc(row.skill || "Unknown skill") + "</b></td><td class=\"number\">" + fmt(damage) +
          "</td><td class=\"number\">" + fmt(row.percent) + "%</td><td class=\"number\">" + fmt(hits) +
          "</td><td class=\"number\">" + (casts > 0 ? fmt(casts) : "—") +
          "</td><td class=\"number\">" + fmt(perHit) + "</td></tr>";
      }).join("") + "</tbody></table></div>" + (rows.length > limit ?
        "<footer class=\"board-actions\"><button type=\"button\" data-expand-board aria-expanded=\"false\">Expand all " +
        rows.length + "</button></footer>" : "") + "</article>";
  }
  function stripPressureComparison(pressure) {
    pressure=pressure || {};
    var incoming=(pressure.incoming_strips || [])[0] || {};
    var support=(model.stat_tables || []).find(function(board){return board.stat === "Support - Summary";});
    var contributors=(support && support.rows || []).map(function(row){
      var total=Number(row.metrics && row.metrics.boonstrips || 0),seconds=Number(row.participation_time || row.metrics && row.metrics.fighttime || 0);
      return {name:row.name,account:row.account,profession:row.profession,total:total,rate:seconds ? total/seconds*60 : 0};
    }).filter(function(row){return row.total > 0;}).sort(function(a,b){return b.total-a.total;});
    var ours=contributors.reduce(function(sum,row){return sum+row.total;},0),enemy=Number(incoming.count || 0);
    if (!ours && !enemy) return "";
    var combatSeconds=Number(pressure.damage_profile && pressure.damage_profile.combat_seconds || 0);
    var ourRate=combatSeconds ? ours/combatSeconds*60 : 0;
    var enemyRate=Number(incoming.rate_per_combat_minute || (combatSeconds ? enemy/combatSeconds*60 : 0));
    var max=Math.max(ours,enemy,1);
    return "<article class=\"strip-pressure-card\"><div class=\"chart-title\"><b>Boon Removal · Both Directions</b>" +
      "<span>Whole night · squad combat time</span></div><div class=\"strip-scoreline\"><div><span>Our Boon Removal</span><strong>" +
      fmt(ours) + "</strong><b>" + fmt(ourRate) + " / combat min</b><i><em class=\"ours\" style=\"width:" +
      (ours/max*100).toFixed(1) + "%\"></em></i></div><div><span>Incoming Boon Removal</span><strong>" +
      fmt(enemy) + "</strong><b>" + fmt(enemyRate) + " / combat min</b><i><em class=\"enemy\" style=\"width:" +
      (enemy/max*100).toFixed(1) + "%\"></em></i></div></div>" +
      (contributors.length ? "<h4>Top boon-removal contributors from our squad</h4><div class=\"strip-contributors\">" +
        contributors.slice(0,5).map(function(row){return "<button type=\"button\"" +
          drillAttrs("session-player",row.name,fmt(row.total)+" boons removed · "+fmt(row.rate)+" per active minute",
            "Support - Summary · outgoing boon removal") + ">" + playerBarLabel(row) +
          "<strong>" + fmt(row.total) + "</strong><small>" + fmt(row.rate) + " / active min</small></button>";}).join("") +
        "</div>" : "") + "<p>Incoming removal is observed from our defense records, but the source does not identify which enemy professions or skills removed each boon.</p></article>";
  }
  function damageProfileCard(profile) {
    if (!profile || profile.status !== "observed") return "";
    var power=Number(profile.direct_damage || 0), condi=Number(profile.condition_damage || 0);
    var total=Number(profile.total_incoming_damage || power + condi);
    var powerPct=Number(profile.direct_percent || 0), condiPct=Number(profile.condition_percent || 0);
    function segment(label, value, percent, rate, cls) {
      return "<button type=\"button\" class=\"damage-segment " + cls + "\" style=\"--share:" +
        Math.max(0,percent) + "%\"" + drillAttrs("damage-profile", label + " · " + fmt(percent) + "%",
          fmt(value) + " incoming damage · " + fmt(rate) + " aggregate squad damage/sec",
          "Source: " + (profile.source || "Defenses-Summary") + " · Session-wide enemy pressure") +
        "><span>" + esc(label) + "</span><strong>" + fmt(value) + "</strong><b>" + fmt(percent) + "%</b></button>";
    }
    return "<article class=\"damage-profile\"><div class=\"chart-title\"><b>Incoming Damage Profile</b>" +
      "<span>" + fmt(total) + " total · " + esc(profile.classification || "Observed") + "</span></div>" +
      "<div class=\"damage-stack\"><i class=\"power\" style=\"width:" + powerPct + "%\"></i>" +
      "<i class=\"condition\" style=\"width:" + condiPct + "%\"></i></div><div class=\"damage-split\">" +
      segment("Power Damage",power,powerPct,profile.direct_damage_per_second,"power") +
      segment("Condition Damage",condi,condiPct,profile.condition_damage_per_second,"condition") +
      "</div></article>";
  }
  function ourEnemyComparison(pressure, scope, fight) {
    var profile=pressure && pressure.damage_profile || {};
    var indexes=fight ? [Number(fight.index)] :
      (scope ? (scope.fight_indexes || []).map(Number) : null);
    var matchedFights=(model.fights || []).filter(function(row){
      return !indexes || indexes.indexOf(Number(row.index)) >= 0;
    });
    var fights=matchedFights.length;
    var intelFights=model.enemy_intel && model.enemy_intel.fights || [];
    var enemyPlayerFights=intelFights.filter(function(row){
      if (fight && Number(row.index) !== Number(fight.index)) return false;
      if (scope && String(row.color || "").toLowerCase() !==
          String(scope.color || "").toLowerCase()) return false;
      return !indexes || indexes.indexOf(Number(row.index)) >= 0;
    }).reduce(function(sum,row){return sum+(Number(row.enemy_count)||0);},0);
    if (!enemyPlayerFights) enemyPlayerFights=matchedFights.reduce(function(sum,row){
      return sum+(Number(row.enemy)||0);
    },0);
    function table(stat) {
      return (model.stat_tables || []).find(function(board){return board.stat === stat;});
    }
    function sumMetric(stat,key) {
      var board=table(stat); if (!board) return null;
      return (board.rows || []).reduce(function(sum,row){
        return sum + (Number(row.metrics && row.metrics[key]) || 0);
      },0);
    }
    function sumTotal(stat) {
      var board=table(stat); if (!board) return null;
      return (board.rows || []).reduce(function(sum,row){
        return sum + (Number(row.total != null ? row.total : row.value) || 0);
      },0);
    }
    function fightSum(key) {
      return matchedFights.reduce(function(sum,row){return sum+(Number(row[key])||0);},0);
    }
    function shortValue(value) {
      value=Number(value || 0); var abs=Math.abs(value);
      if (abs >= 1000000) return (value/1000000).toFixed(abs >= 10000000 ? 1 : 2).replace(/\.0+$/,"") + "m";
      if (abs >= 1000) return (value/1000).toFixed(abs >= 100000 ? 0 : 1).replace(/\.0$/,"") + "k";
      return fmt(value);
    }
    function normalizedValue(value) {
      value=Number(value || 0)/(enemyPlayerFights || 1);
      if (Math.abs(value) >= 1000) return shortValue(value);
      if (Math.abs(value) >= 10) return value.toFixed(1).replace(/\.0$/,'');
      return value.toFixed(2).replace(/0+$/,'').replace(/\.$/,'');
    }
    var enemyStrips=Number((pressure.incoming_strips || [])[0] && pressure.incoming_strips[0].count || 0);
    var enemyCc=Number((pressure.control_profile || [])[0] && pressure.control_profile[0].count || 0);
    var enemyPulls=(pressure.pulls || []).reduce(function(sum,row){return sum+(Number(row.count)||0);},0);
    var scopeLabel=fight ? "Fight " + fmt(fight.index) :
      (scope ? readableLabel(scope.label || scope.color || "selected opponent") : "All opponents");
    var scopeRef=fight ? "fight:" + Number(fight.index) : (scope ? "scope:" + String(scope.id || scope.color || "") : "all");
    var groups=[
      {title:"Fight Output",rows:[
        {label:"Damage dealt",ours:fightSum("damage_out"),enemy:fightSum("damage_in"),unit:"damage",drillKey:"damage"},
        {label:"Downs secured",ours:fightSum("downs"),enemy:fightSum("ally_downs"),unit:"downs",drillKey:"downs"},
        {label:"Kills secured",ours:fightSum("kills"),enemy:fightSum("ally_deaths"),unit:"kills",drillKey:"kills"}
      ]}
    ];
    if (!scope && !fight) groups.push({title:"Pressure Tools",rows:[
        {label:"Boon removal",ours:sumMetric("Support - Summary","boonstrips"),enemy:enemyStrips,unit:"boons removed"},
        {label:"Crowd control",ours:sumMetric("Offensive - Summary","appliedcrowdcontrol"),enemy:enemyCc,unit:"events",context:true},
        {label:"Pull-skill connected hits",ours:sumTotal("Outgoing Pulls"),enemy:enemyPulls,unit:"damage-hit proxy"}
      ]});
    function rowHtml(row) {
      if (row.ours == null || row.enemy == null) return "";
      var ours=Number(row.ours || 0),enemy=Number(row.enemy || 0),max=Math.max(ours,enemy,1);
      var tag=row.drillKey ? "button" : "div";
      return "<" + tag + (row.drillKey ? " type=\"button\"" + drillAttrs("comparison-row",row.label + " · " + scopeLabel,
        "Matched-fight evidence for " + row.label,"Observed fight summaries · exact selected opponent scope",scopeRef + "|" + row.drillKey) : "") +
        " class=\"duel-row\"><b>" + esc(row.label) + (row.context ? "<small>directional context</small>" : "") +
        "</b><span class=\"duel-track\"><i class=\"duel-fill ours\" style=\"width:" +
        Math.max(1,ours/max*100).toFixed(1) + "%\"></i><i class=\"duel-fill enemy\" style=\"width:" +
        Math.max(1,enemy/max*100).toFixed(1) + "%\"></i></span><span class=\"duel-values duel-total\"><em>Our " +
        shortValue(ours) + "</em><em>Enemy " + shortValue(enemy) + "</em></span>" +
        "<span class=\"duel-values duel-normalized\"><em>Our " + normalizedValue(ours) +
        "</em><em>Enemy " + normalizedValue(enemy) + "</em></span></" + tag + ">";
    }
    var rendered=groups.map(function(group){
      var rows=group.rows.map(rowHtml).filter(Boolean).join("");
      return rows ? "<section><h4>" + esc(group.title) + "</h4>" + rows + "</section>" : "";
    }).filter(Boolean).join("");
    if (!rendered) return "";
    return "<article class=\"duel-card\" data-scope-section=\"fight-output\"><div class=\"chart-title duel-heading\"><b>Our Squad vs Enemy</b>" +
      "<span class=\"duel-mode\"><button type=\"button\" class=\"selected\" aria-pressed=\"true\" data-duel-mode=\"total\">Totals</button>" +
      "<button type=\"button\" aria-pressed=\"false\" data-duel-mode=\"normalized\">Per enemy / fight</button></span><span>" +
      esc(scopeLabel) + " · " + fmt(fights) + " matched fight" + (fights === 1 ? "" : "s") +
      "</span></div><div class=\"duel-legend\"><span class=\"ours\">Our squad</span>" +
      "<span class=\"enemy\">Enemy</span></div><p>Each row shares one scale. Crowd control is directional " +
      "context because outgoing applications and received events are not identical measurements.</p>" +
      "<div class=\"duel-grid\">" + rendered + "</div><p class=\"normalization-note\"><b>Normalized denominator:</b> " +
      fmt(enemyPlayerFights) + " observed enemy player-fight appearances. Allied squads fighting beside us " +
      "are not fully represented, so this measures pressure involving our logged squad—not total battlefield output.</p></article>";
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
    return renderHighScoreBlocks(blocks);
  }
  function highScoreGridExact(captions) {
    var wanted=(captions || []).map(function(value){return String(value).toLowerCase();});
    var blocks=(model.high_scores && model.high_scores.blocks || []).filter(function(block){
      return !wanted.length || wanted.indexOf(String(block.caption || "").toLowerCase()) >= 0;
    });
    return renderHighScoreBlocks(blocks);
  }
  function renderHighScoreBlocks(blocks) {
    if (!blocks.length) return "<p class=\"empty\">No matching high-score blocks were exported.</p>";
    return "<div class=\"boards high-score-grid\">" + blocks.map(function (block) {
      var scoreRows=(block.rows || []).slice(0,100);
      var rows = scoreRows.map(function (row, i) {
        var context=(row.fight != null ? "Fight " + row.fight : "") +
          ((row.details || []).length ? " · " + row.details.join(" · ") : "");
        return "<tr data-score=\"" + esc(row.score == null ? "" : row.score) + "\"" +
          (i >= 5 ? " class=\"board-extra\" hidden" : "") + "><td class=\"rank\">" +
          (i + 1) + "</td><td><span class=\"score-player\">" +
          professionGlyph(row.profession || "Unknown") + "<span><b>" + esc(row.name || "Player") +
          "</b><small>" + esc(row.profession || "Class unavailable") +
          (context ? " · " + esc(context) : "") + "</small></span></span></td><td class=\"number\">" +
          fmt(row.score) + "</td></tr>";
      }).join("");
      return "<article class=\"board\"><h3>" + esc(block.caption || "High score") +
        "</h3><div class=\"table-wrap\"><table class=\"high-score-table\" data-board-table>" +
        "<colgroup><col class=\"score-rank\"><col class=\"score-entry\"><col class=\"score-result\"></colgroup>" +
        "<thead><tr><th>#</th><th>Player / Fight / Skill</th><th class=\"number\" aria-sort=\"descending\"><button type=\"button\" data-sort-key=\"score\">Result</button></th></tr></thead><tbody>" + rows +
        "</tbody></table></div>" + (scoreRows.length > 5 ? "<footer class=\"board-actions\"><button type=\"button\" data-expand-board aria-expanded=\"false\">Expand all " + scoreRows.length + "</button></footer>" : "") + "</article>";
    }).join("") + "</div>";
  }
  function playerSkillDamageView() {
    var source=model.player_skill_damage || [],players=Array.isArray(source) ? source : (source.players || []);
    if (!players.length) return "<p class=\"empty\">No qualifying per-player Damage by Skill tables were exported. Missing players are not zero; the Classic export applies participation and DPS qualification filters.</p>";
    return "<aside class=\"method-note skill-damage-note\"><b>How to read Down Contribution</b><span>Damage dealt from 90% health through the down on enemies whose down led to a death. It is separate from damage dealt after the enemy is already downed.</span><span>Barrier damage by outgoing skill was not retained in this Classic export; unavailable does not mean zero. GW2EI exposes barrier damage separately from health damage when the source includes it.</span></aside>" +
      "<div class=\"skill-player-grid\">" + players.map(function(player){
      var skills=(player.skills || []).slice().sort(function(a,b){return Number(b.damage || 0)-Number(a.damage || 0);});
      var rows=skills.map(function(skill,index){return "<tr" + (index >= 5 ? " class=\"board-extra\" hidden" : "") +
        "><td class=\"rank\" data-label=\"#\">" + (index+1) + "</td><td data-label=\"Skill\"><b>" + esc(skill.skill || "Unknown skill") +
        "</b></td><td class=\"number\" data-label=\"Damage\">" + fmt(skill.damage) + "</td><td class=\"number\" data-label=\"Down Contribution\">" +
        fmt(skill.down_contribution) + "</td><td class=\"number\" data-label=\"Hits\">" + fmt(skill.hits) +
        "</td><td class=\"number\" data-label=\"Damage / Hit\">" + fmt(skill.damage_per_hit) +
        "</td><td class=\"number\" data-label=\"Share\">" + fmt(skill.percent_of_total) + "%</td></tr>";}).join("");
      return "<article class=\"board skill-player-board\"><h3><span class=\"score-player\">" +
        professionGlyph(player.profession || "Unknown") + "<span><b>" + esc(player.name || "Player") +
        "</b><small>" + esc(player.profession || "Class unavailable") + " · " + esc(player.account || "") +
        "</small></span></span></h3><div class=\"skill-coverage\"><b>" + fmt(player.total_damage) +
        " damage represented</b><span>Per-player table exported by Classic; absence does not mean zero.</span></div>" +
        "<div class=\"table-wrap\"><table class=\"skill-damage-table\"><colgroup><col class=\"skill-rank\"><col class=\"skill-name\"><col class=\"skill-value\"><col class=\"skill-value\"><col class=\"skill-value\"><col class=\"skill-value\"><col class=\"skill-share\"></colgroup>" +
        "<thead><tr><th>#</th><th>Skill</th><th class=\"number\">Damage</th><th class=\"number\">Down Contribution</th><th class=\"number\">Hits</th><th class=\"number\">Damage / Hit</th><th class=\"number\">Share</th></tr></thead><tbody>" + rows +
        "</tbody></table></div>" + (skills.length > 5 ? "<footer class=\"board-actions\"><button type=\"button\" data-expand-board aria-expanded=\"false\">Expand all " + skills.length + "</button></footer>" : "") + "</article>";
    }).join("") + "</div>";
  }
  function resurrectionSkillView() {
    var source=tableBySource("Combat-Resurrect");
    if (!source) return "<p class=\"empty\">No combat-resurrection skill table was exported.</p>";
    var ignored={prof:1,fighttime:1,activetime:1,numfights:1,count:1},totals={};
    (source.rows || []).forEach(function(row){Object.keys(row.metrics || {}).forEach(function(key){
      if (!ignored[key] && Number.isFinite(Number(row.metrics[key]))) totals[key]=(totals[key] || 0)+Number(row.metrics[key]);
    });});
    var rows=Object.keys(totals).map(function(key){return {key:key,value:totals[key]};}).sort(function(a,b){return b.value-a.value;});
    if (!rows.length) return "<p class=\"empty\">The report contains resurrection totals but no skill-level fields.</p>";
    return "<article class=\"board resurrection-skills\"><h3>Combat-Resurrection Healing by Skill</h3><div class=\"table-wrap\"><table><colgroup><col style=\"width:72%\"><col style=\"width:28%\"></colgroup><thead><tr><th>Skill</th><th class=\"number\">Healing</th></tr></thead><tbody>" + rows.map(function(row,index){return "<tr" + (index >= 5 ? " class=\"board-extra\" hidden" : "") + "><td>" + esc(readableLabel(row.key)) + "</td><td class=\"number\">" + fmt(row.value) + "</td></tr>";}).join("") + "</tbody></table></div>" + (rows.length > 5 ? "<footer class=\"board-actions\"><button type=\"button\" data-expand-board aria-expanded=\"false\">Expand all " + rows.length + "</button></footer>" : "") + "</article>";
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
      var content="<b>" + esc(label) + "</b>" + (value == null || value === "" ? "" : " · " + fmt(value));
      if (typeof row === "string") return "<span class=\"chip-static\">" + content + "</span>";
      var shown={}; shown[labelKey]=true; shown[valueKey]=true;
      ["profession","effect","name","skill","count","damage","uptime_percent","evidence","source_scope"].forEach(function(key){shown[key]=true;});
      var hasMore=Object.keys(row).some(function(key){return !shown[key] && row[key] != null;});
      return hasMore ? "<button type=\"button\"" + drillAttrs("pressure", label,
        value == null || value === "" ? "" : String(value),
        readableLabel(row.evidence || "reported or inferred") + " · " +
          readableLabel(row.source_scope || "current report scope")) + ">" + content + "</button>" :
        "<span class=\"chip-static\">" + content + "</span>";
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
  function enemyScopeButtons() {
    var coverage = model.enemy_intel && model.enemy_intel.coverage || {};
    var modeled = coverage.reported_fights == null ?
      (coverage.modeled_fights == null ? 0 : coverage.modeled_fights) : coverage.reported_fights;
    var buttons = ["<button type=\"button\" class=\"enemy-scope-button selected\" " +
      "data-enemy-scope=\"all\" data-enemy-color=\"all\" role=\"tab\" aria-selected=\"true\">" +
      "<span>Full night</span><b>All opponents</b><small>" + fmt(modeled) +
      " modeled fights · compare every opponent</small></button>"];
    enemyScopes().forEach(function (scope) {
      var id = String(scope.id || scope.color || "opponent");
      var color = String(scope.color || scope.label || "opponent").toLowerCase();
      var fights = (scope.fight_indexes || []).length;
      var average = scope.aggregate && scope.aggregate.enemy_size_avg;
      buttons.push("<button type=\"button\" class=\"enemy-scope-button\" data-enemy-scope=\"" +
        esc(id) + "\" data-enemy-color=\"" + esc(color) +
        "\" role=\"tab\" aria-selected=\"false\"><span>Opponent scope</span><b>" +
        esc(scope.label || scope.color || "Opponent") + "</b><small>" + fmt(fights) +
        " matched fight" + (fights === 1 ? "" : "s") +
        (average == null ? "" : " · " + fmt(average) + " average enemies") +
        "</small></button>");
    });
    return "<section class=\"enemy-scope-shell\"><div class=\"enemy-scope-heading\">" +
      "<div><span class=\"eyebrow\">Choose what to analyze</span>" +
      "<h3>Switch opponent view</h3></div><p>Compare the whole night, then open a color for its composition and individual fights.</p></div>" +
      "<div class=\"enemy-scope-switcher\" role=\"tablist\" aria-label=\"Opponent view\">" +
      buttons.join("") + "</div></section>";
  }
  function pressurePanels(scope, fight) {
    var intel = model.enemy_intel || {};
    var pressure = scope && scope.aggregate || intel.session_pressure || {};
    var sessionOnly = pressure.pressure_scope === "session_only" ||
      pressure.source_scope === "session_only" || (scope && !(scope.aggregate || {}).top_damage_skills);
    if (scope && sessionOnly) pressure = intel.session_pressure || pressure;
    var ccRows = pressure.control_profile || pressure.cc || [];
    var pullRows = pressure.pulls || [];
    var stripRows = (pressure.incoming_strips || []).concat(pressure.strips_in || [])
      .concat(pressure.generalized_incoming_strips || []);
    var comparison=ourEnemyComparison(pressure, scope, fight);
    if (scope) return comparison +
      "<p class=\"scope-note scope-limit\" data-scope-section=\"pressure-coverage\"><b>Pressure tools are not color-separated by the source log.</b> " +
      "Incoming skills, conditions, boon removal, crowd control, and pulls are therefore shown only " +
      "under All opponents instead of repeating the same session total for every color.</p>";
    return "<p class=\"scope-note\"><b>Session-wide enemy pressure.</b> The source does not " +
      "attribute these totals by enemy color or profession.</p>" + comparison +
      damageProfileCard(pressure.damage_profile) +
      stripPressureComparison(pressure) +
      "<div class=\"intel-visuals\">" +
      pressureBars(pressure.top_damage_skills, "Top Incoming Skill Damage", "damage") +
      pressureBars(pressure.top_damage_skills, "Most Connected Enemy Skill Hits", "control", "connected_hits") +
      conditionHeatmap(pressure) +
      pressureBars(stripRows, "Incoming Boon Removal", "strip") +
      pressureBars(ccRows, "Received Crowd Control", "control") +
      pressureBars(pullRows, "Connected Pulls", "control") + "</div>" +
      enemySkillPressureTable(pressure);
  }
  function compositionComparison(scope, fight) {
    scope=scope || {};
    var indexes=fight ? [Number(fight.index)] : (scope.fight_indexes || []).map(Number);
    var squads=(model.squad_composition && model.squad_composition.squads || []).filter(function(squad){
      return indexes.indexOf(Number(squad.fight)) >= 0;
    });
    if (!squads.length) return "";
    var ourCounts={}, ourSize=0;
    squads.forEach(function(squad){
      var players=squad.players || []; ourSize += players.length;
      players.forEach(function(player){var profession=player.profession || "Unknown";
        ourCounts[profession]=(ourCounts[profession] || 0)+1;});
    });
    var ourFights=squads.length, ourAvg={};
    Object.keys(ourCounts).forEach(function(profession){ourAvg[profession]=ourCounts[profession]/ourFights;});
    var enemyRows=fight ? (fight.professions || []) : ((scope.aggregate || {}).professions || []);
    var enemyFights=fight ? 1 : Math.max(1,(scope.fight_indexes || []).length), enemyAvg={};
    enemyRows.forEach(function(row){enemyAvg[row.profession || "Unknown"]=Number(
      row.avg_per_fight != null ? row.avg_per_fight : Number(row.count || 0)/enemyFights);});
    var names=Array.from(new Set(Object.keys(ourAvg).concat(Object.keys(enemyAvg))));
    names.sort(function(a,b){
      return (enemyAvg[b]||0)-(enemyAvg[a]||0) || (ourAvg[b]||0)-(ourAvg[a]||0) || a.localeCompare(b);
    });
    names=names.slice(0,12);
    var max=Math.max.apply(null,names.reduce(function(values,name){return values.concat([ourAvg[name]||0,enemyAvg[name]||0]);},[1]));
    var enemySize=Number(fight ? fight.enemy_count : (scope.aggregate || {}).enemy_size_avg || 0);
    var enemyObserved=enemyRows.reduce(function(sum,row){return sum+Number(row.avg_per_fight != null ? row.avg_per_fight : row.count || 0);},0);
    var label=fight ? "Fight " + fight.index : readableLabel(scope.label || scope.color || "Enemy");
    return "<article class=\"comp-compare\" data-scope-section=\"profession-comparison\"><div class=\"chart-title\"><b>Composition · Our Squad vs " +
      esc(label) + "</b><span>" + fmt(indexes.length) + " matched fight" + (indexes.length === 1 ? "" : "s") +
      "</span></div><div class=\"comp-size-line\"><span><b>Our squad " + fmt(ourSize/ourFights) +
      "</b> average players</span><span><b>Enemy " + fmt(enemySize) + "</b> average players · " +
      fmt(enemyObserved) + " professions identified</span></div><div class=\"comp-legend\"><span>Profession</span>" +
      "<span>Our avg / fight</span><span>Enemy avg / fight</span></div><div class=\"comp-rows\">" + names.map(function(name){
        var ours=ourAvg[name]||0, enemy=enemyAvg[name]||0;
        var ref=(fight ? "fight:" + Number(fight.index) : "scope:" + String(scope.id || scope.color || "")) + "|" + name;
        return "<button type=\"button\" class=\"comp-row\"" + drillAttrs("composition-profession",name + " · " + label,
          fmt(ours) + " our average per fight · " + fmt(enemy) + " enemy average per fight",
          fmt(indexes.length) + " matched fights · profession snapshots, not unique players",ref) + "><div>" + professionInline(name) + "</div><span class=\"comp-bar ours\">" +
          "<i style=\"width:" + Math.max(ours ? 2 : 0,ours/max*100).toFixed(1) + "%\"></i><b>" + fmt(ours) +
          "</b></span><span class=\"comp-bar enemy\"><i style=\"width:" + Math.max(enemy ? 2 : 0,enemy/max*100).toFixed(1) +
          "%\"></i><b>" + fmt(enemy) + "</b></span></button>";
      }).join("") + "</div><p>Values are average profession sightings per matched fight—not unique players or ratings. Our roster is observed. Enemy profession counts are observed; enemy subgroup placement and roles remain estimated.</p></article>";
  }
  function scopeSummary(scope) {
    var a = scope && scope.aggregate || {};
    return "<div class=\"intel-kpis\"><div><strong>" + fmt(a.enemy_size_avg) +
      "</strong><span>average enemy size</span></div><div><strong>" +
      fmt(a.enemy_size_max) + "</strong><span>largest observed</span></div><div><strong>" +
      fmt((scope && scope.fight_indexes || []).length) + "</strong><span>fight snapshots</span></div></div>" +
      scopeRoadmap(scope) + compositionComparison(scope, null) + allFightsCompositionView(scope) + pressurePanels(scope, null);
  }
  function scopeRoadmap(scope) {
    var label=readableLabel(scope && (scope.label || scope.color) || "Opponent");
    var groupCount=Math.max(1,Math.ceil(Number(scope && scope.aggregate && scope.aggregate.enemy_size_avg || 0)/5));
    function jump(target,title,detail) {
      return "<button type=\"button\" data-scope-target=\"" + target + "\"><b>" + title +
        "</b><small>" + detail + "</small></button>";
    }
    return "<section class=\"scope-roadmap\"><div class=\"scope-roadmap-head\"><div><span class=\"eyebrow\">More Intel Below</span>" +
      "<h3>Explore " + esc(label) + "</h3></div><p>Use these shortcuts or keep scrolling—every detailed section remains open below.</p></div>" +
      "<div class=\"scope-roadmap-actions\">" +
      jump("profession-comparison","Profession Comparison","Enemy frequency first · our squad beside it") +
      jump("estimated-subgroups","Estimated Subgroups",fmt(groupCount)+" five-player rows · roles and evidence") +
      jump("fight-output","Fight Output","Damage, downs, and kills on matched fights") +
      jump("pressure-coverage","Pressure Coverage","What is session-wide versus color-specific") +
      "</div><div class=\"scope-mini-wrap\"><b>Estimated subgroup preview</b>" +
      "<span>Class icons only · select a row for the full breakdown</span><div class=\"scope-mini-parties\"></div></div></section>";
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
    return "<div class=\"comparison-banner\"><b>All fights composition.</b> " +
      "The representative group below averages every observed enemy snapshot; use the color cards for Green and Red differences.</div>" +
      allFightsCompositionView() +
      (comparisons.length ? "<div class=\"comparison-grid\">" + comparisons.map(function (item) {
        return "<article class=\"intel-card color-card\" data-color=\"" +
          esc(String(item.color || item.label || "unknown").toLowerCase()) + "\"><h3>" +
          esc(item.label || item.color || "Opponent") + "</h3><p><b>" +
          fmt(item.enemy_size_avg) + "</b> average · <b>" + fmt(item.fight_count || item.composition_snapshots) +
          "</b> fights</p>" + chips(item.top_professions || item.professions, "profession", "avg_per_fight", 8) +
          "</article>";
      }).join("") + "</div>" : "<p class=\"empty\">No color comparison was available.</p>") +
      scopes.map(function(scope){return compositionComparison(scope,null);}).join("") +
      pressurePanels(null);
  }
  function professionRoleCandidate(profession,validation) {
    var profile=(validation && validation.professions || {})[profession] || {},signals=[];
    function add(role,level,evidence,source) {
      if (!role) return;
      var existing=signals.find(function(item){return item.role===role;});
      if (existing) {existing.evidence=existing.evidence.concat(evidence || []);return;}
      signals.push({role:role,level:level || "Likely",evidence:evidence || [],source_scope:source || "night_wide_profession_evidence"});
    }
    (profile.roles || []).forEach(function(item){
      var evidence=Array.isArray(item.evidence) ? item.evidence : [item.evidence || "Role-specific output observed"];
      add(item.role,item.level,evidence,item.source_scope);
    });
    (profile.traits || []).forEach(function(item){
      var tags=(item.roles || []).map(function(role){return String(role).toLowerCase();});
      var detail="Proven trait "+item.trait+" triggered "+item.observed_skill+" in "+fmt(item.actor_appearances)+" enemy appearance(s)";
      if (tags.indexOf("healing")>=0 && tags.indexOf("damage")<0 && tags.indexOf("control")<0) add("Support / Healing","Likely",[detail],"night_wide_proven_trait_proc");
      else if (tags.length===1 && tags[0]==="damage") add("DPS","Likely",[detail],"night_wide_proven_trait_proc");
      else if (tags.length===1 && tags[0]==="control") add("Crowd Control","Likely",[detail],"night_wide_proven_trait_proc");
    });
    (profile.consumables || []).forEach(function(item){
      var tags=(item.roles || []).map(function(role){return String(role).toLowerCase();});
      var detail="Observed "+item.classification+" "+item.name+" in "+fmt(item.actor_appearances)+" enemy appearance(s)";
      if (tags.indexOf("healing")>=0 && tags.indexOf("damage")<0 && tags.indexOf("control")<0) add("Support / Healing","Likely",[detail],"night_wide_observed_consumable");
      else if (tags.length===1 && tags[0]==="support") add("Boon Support","Likely",[detail],"night_wide_observed_consumable");
      else if (tags.length===1 && tags[0]==="damage") add("DPS","Likely",[detail],"night_wide_observed_consumable");
    });
    var priority={"Support / Healing":0,"Boon Support":1,"DPS / Support":2,"DPS":3,"Power DPS":3,"Condition DPS":3,"Boon Strip":4,"Crowd Control":5};
    signals.sort(function(a,b){return (priority[a.role] == null ? 99 : priority[a.role])-(priority[b.role] == null ? 99 : priority[b.role]);});
    return signals[0] || null;
  }
  function allFightsCompositionView(scope) {
    var allFights=model.enemy_intel && model.enemy_intel.fights || [];
    var indexes=scope && (scope.fight_indexes || []).map(Number);
    var scopeColor=String(scope && scope.color || "").toLowerCase();
    var fights=allFights.filter(function(fight){
      if (!scope) return true;
      if (scopeColor && String(fight.color || "").toLowerCase() !== scopeColor) return false;
      return !indexes.length || indexes.indexOf(Number(fight.index)) >= 0;
    });
    if (!fights.length) return "";
    var validation=model.enemy_intel && model.enemy_intel.role_validation || {};
    var actorAppearances=Number(validation.enemy_actor_appearances || 0);
    var scopeLabel=scope ? readableLabel(scope.label || scope.color || "Selected opponent") : "Enemy";
    var scopeRef=scope ? "scope:" + String(scope.id || scope.color || "") : "all";
    var compositionTitle=scope ? "Estimated " + scopeLabel + " Group Composition" :
      "Estimated Enemy Group Composition";
    var professions={}, roleVotes={}, totalEnemies=0;
    fights.forEach(function(fight){
      totalEnemies += Number(fight.enemy_count || 0);
      (fight.professions || []).forEach(function(row){
        var profession=row.profession || "Unknown";
        professions[profession]=(professions[profession] || 0)+Number(row.count || 0);
      });
      (fight.estimated_subgroups || []).forEach(function(group){
        (group.members || []).forEach(function(member){
          var profession=member.profession || "Unknown", role=member.role || member.inferred_role || "Unknown";
          roleVotes[profession]=roleVotes[profession] || {};
          roleVotes[profession][role]=(roleVotes[profession][role] || 0)+1;
        });
      });
    });
    var snapshots=fights.length, target=Math.max(1,Math.round(totalEnemies/snapshots));
    var rows=Object.keys(professions).map(function(profession){
      var candidate=professionRoleCandidate(profession,validation),votes=roleVotes[profession] || {};
      var role=candidate ? candidate.role : Object.keys(votes).sort(function(a,b){return votes[b]-votes[a];})[0] || "Unknown";
      var average=professions[profession]/snapshots;
      return {profession:profession,count:professions[profession],avg_per_fight:average,role:role,
        role_inference:candidate ? {qualifier:candidate.level,roles:[candidate],evidence:candidate.evidence,
          source_scope:candidate.source_scope,limitation:"Profession-level candidate; enemy identity and color are not retained."} : {},
        slots:Math.floor(average),fraction:average-Math.floor(average)};
    }).sort(function(a,b){return b.count-a.count;});
    var assigned=rows.reduce(function(sum,row){return sum+row.slots;},0), fractionOrder=rows.slice().sort(function(a,b){return b.fraction-a.fraction;});
    for (var add=0;assigned<target && fractionOrder.length;add++,assigned++) fractionOrder[add % fractionOrder.length].slots++;
    var members=[];
    rows.forEach(function(row){for(var i=0;i<row.slots;i++) members.push({profession:row.profession,role:row.role,role_inference:row.role_inference,evidence:"inferred"});});
    members.sort(function(a,b){var order={heal:0,support:1,hybrid:2,dps:3,unknown:4};return order[roleClass(a.role)]-order[roleClass(b.role)];});
    var partyCount=Math.ceil(target/5), parties=[];
    for(var p=0;p<partyCount;p++) parties.push({party:p+1,members:[],confidence:{level:"low",score:.45,basis:"nightly profession average; placement and roles inferred"}});
    members.forEach(function(member){
      var kind=roleClass(member.role), candidates=parties.filter(function(party){return party.members.length<5;});
      candidates.sort(function(a,b){
        var aKind=a.members.filter(function(item){return roleClass(item.role)===kind;}).length;
        var bKind=b.members.filter(function(item){return roleClass(item.role)===kind;}).length;
        return aKind-bKind || a.members.length-b.members.length || a.party-b.party;
      });
      if(candidates[0]) candidates[0].members.push(member);
    });
    var maxAverage=Math.max.apply(null,rows.map(function(row){return row.avg_per_fight;}).concat([1]));
    var professionBars="<div class=\"bar-card tone-support\"><div class=\"chart-title\"><b>Profession Frequency</b><span>Average per observed fight</span></div>" +
      rows.slice(0,10).map(function(row){return "<button type=\"button\" class=\"metric-row profession-row\"" +
        drillAttrs("composition-profession",row.profession + " · " + scopeLabel,fmt(row.avg_per_fight)+" average per fight · "+fmt(row.count)+" observed across "+snapshots+" snapshots",
          "Observed profession counts; role and representative placement are estimated",scopeRef + "|" + row.profession) +
        "><span class=\"bar-label\">"+professionInline(row.profession)+"</span><i><em style=\"width:"+
        Math.max(2,row.avg_per_fight/maxAverage*100).toFixed(1)+"%\"></em></i><b>"+fmt(row.avg_per_fight)+"</b></button>";}).join("")+"</div>";
    var roleCounts={}; members.forEach(function(member){var label=roleDisplay(member.role).replace(/^Likely /,"");roleCounts[label]=(roleCounts[label]||0)+1;});
    var roleRows=Object.keys(roleCounts).sort(function(a,b){return roleCounts[b]-roleCounts[a];}).map(function(label){return {name:label,count:roleCounts[label]};});
    return "<section class=\"all-fights-comp\" data-scope-section=\"estimated-subgroups\"><div class=\"section-head\"><div><span class=\"eyebrow\">" +
      (scope ? esc(scopeLabel) + " opponents · " + fmt(snapshots) + " matched fights" : "All modeled fights") +
      "</span><h2>" + esc(compositionTitle) + "</h2></div>" +
      "<p>Representative average group from "+snapshots+" enemy snapshots. Profession frequency is observed; Subgroup placement and roles are estimated.</p></div>"+
      "<p class=\"role-validation-note\"><b>Role check:</b> " +
      (scope ? "Role labels are profession-level role candidates from the night’s per-enemy DPS, skill casts, proven trait procs, and exposed consumables. Enemy healing totals still cannot be measured." :
       actorAppearances ? fmt(actorAppearances)+" detailed enemy appearances were checked using per-enemy DPS and skill casts. " +
        "Enemy healing totals still cannot be measured." :
        "This recovered report did not retain per-enemy DPS or skill rotations, so its role labels stay Estimated.") + "</p>"+
      (scope ? enemyBuildEvidenceView(validation,rows.map(function(row){return row.profession;}),scopeLabel) : "<div class=\"intel-kpis\"><div><strong>"+fmt(snapshots)+"</strong><span>enemy snapshots</span></div><div><strong>"+
      fmt(totalEnemies/snapshots)+"</strong><span>average group size</span></div><div><strong>"+fmt(totalEnemies)+"</strong><span>observed enemy slots</span></div></div>"+
      enemyBuildEvidenceView(validation)) +
      "<div class=\"intel-visuals\">"+professionBars+"<article class=\"intel-card\"><h3>Representative Role Mix</h3>"+
      chips(roleRows,"name","count",12)+"</article></div>"+partyGrid({estimated_subgroups:parties})+
      "<p class=\"scope-note\">This summarizes " + (scope ? esc(scopeLabel) + " matched fights" : "the night") +
      "; it does not claim every enemy group used the same composition.</p></section>";
  }
  function enemyBuildEvidenceView(validation,selectedProfessions,scopeLabel) {
    var allowed=Array.isArray(selectedProfessions) ? new Set(selectedProfessions) : null;
    var rows=Object.keys(validation.professions || {}).map(function(profession){
      return {profession:profession,profile:validation.professions[profession]};
    }).filter(function(row){return (!allowed || allowed.has(row.profession)) && ((row.profile.traits || []).length || (row.profile.consumables || []).length);});
    if (!rows.length) return "<section class=\"enemy-build-evidence\"><div class=\"section-head\"><div><span class=\"eyebrow\">Observable build fingerprints</span><h2>Enemy Build Evidence</h2></div></div><p class=\"empty\">No unique trait procs or enemy food / utility buffs were exposed for this report. That means unknown, not unequipped.</p></section>";
    var hasConsumables=rows.some(function(row){return (row.profile.consumables || []).length;});
    var evidenceTitle=hasConsumables ? "Enemy Traits & Consumables" : "Observed Enemy Trait Procs";
    return "<section class=\"enemy-build-evidence\"><div class=\"section-head\"><div><span class=\"eyebrow\">Observable build fingerprints</span><h2>"+esc(evidenceTitle)+"</h2></div><p>"+(scopeLabel ? "Night-wide exact evidence for professions observed in "+esc(scopeLabel)+". Enemy identity and color are not retained, so this narrows by profession—not by a proven team member." : "Exact evidence from detailed enemy targets across the night. It is profession-level because enemy identity is not retained, and absence means unknown—not unequipped.")+"</p></div><div class=\"intel-grid\">"+
      rows.map(function(row){var traits=row.profile.traits || [],consumables=row.profile.consumables || [];
        return "<article class=\"intel-card\"><h3>"+professionInline(row.profession)+"</h3>"+
          (traits.length ? "<h4>Proven major-trait procs</h4><div class=\"build-evidence-list\">"+traits.map(function(item){return "<div><b>"+esc(item.trait)+"</b><span>"+esc(item.specialization)+" · "+esc((item.roles || []).join(" / "))+"</span><small>"+esc(item.observed_skill)+" observed · "+fmt(item.actor_appearances)+" enemy appearance(s)</small><p>"+esc(item.trait_description || item.evidence_skill_description || "")+"</p></div>";}).join("")+"</div>" : "")+
          (consumables.length ? "<h4>Observed food / utility buffs</h4><div class=\"build-evidence-list\">"+consumables.map(function(item){return "<div><b>"+esc(item.name)+"</b><span>"+esc(item.classification)+((item.roles || []).length ? " · "+esc(item.roles.join(" / ")) : "")+"</span><small>Seen in "+fmt(item.actor_appearances)+" enemy appearance(s)</small></div>";}).join("")+"</div>" : "")+"</article>";
      }).join("")+"</div>"+(!hasConsumables ? "<p class=\"accuracy\"><b>Enemy food / utility:</b> none was exposed in target buff data for this night, so it remains unknown.</p>" : "")+"<p class=\"accuracy\"><b>Proof rule:</b> a trait appears only when Elite Insights marked the observed skill as a trait proc and the official GW2 API maps that skill to one unique major trait. Enemy food/oil appears only when its Nourishment or Enhancement buff was present in target buff data.</p></section>";
  }
  function partyGrid(fight) {
    var groups = fight && fight.estimated_subgroups || [];
    if (!groups.length) return "<p class=\"empty\">No estimated Subgroup reconstruction was available.</p>";
    var hasUnknown=groups.some(function(group){return Number(group.unknown_slots || 0)>0 || (group.members || []).some(function(member){return /unknown/i.test(String(member.profession || ""));});});
    var hasOpen=groups.some(function(group){return Number(group.open_slots || 0)>0 || (group.members || []).length<5;});
    var confidenceLabels=groups.map(function(group){return confidenceLabel(group.confidence);});
    var confidenceVaries=confidenceLabels.some(function(label){return label!==confidenceLabels[0];});
    var observed=Number(fight && fight.observed_profession_count), enemyCount=Number(fight && fight.enemy_count);
    var coverage=(enemyCount>0 && Number.isFinite(observed)) ? Math.round(observed/enemyCount*100) : null;
    return "<div class=\"evidence-key\"><span><i class=\"observed\"></i>Observed profession</span>" +
      "<span><i class=\"inferred\"></i>Inferred placement</span>" +
      (hasUnknown ? "<span><i class=\"unknown\"></i>Unknown profession</span>" : "") +
      (hasOpen ? "<span><i class=\"unknown\"></i>Open slot</span>" : "") +
      (coverage != null ? "<span><b>Profession coverage " + coverage + "%</b></span>" : "") +
      "<span><b>Subgroup placement estimated</b></span></div>" +
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
          var inference=member.role_inference || {}, roleKind=roleClass(role),
            roleText=roleDisplay(role,inference), evidence=slot.state;
          var roleEvidence=(inference.roles || []).map(function(item){
            return item.level + " " + item.role + ": " + (item.evidence || []).join("; ");
          }).concat(inference.build_evidence || []).join(" · ") || (inference.evidence || []).join("; ") ||
            "No role-specific skill or output was retained for this enemy.";
          var detail="Slot " + (slotIndex + 1) + " · " + roleText +
            " · " + (evidence === "observed" ? "observed profession" :
              evidence === "open" ? "unoccupied capacity" : "inferred from observed frequency and output");
          var drillable=evidence !== "open" && evidence !== "unknown" &&
            roleEvidence.indexOf("No role-specific skill or output") !== 0;
          var tag=drillable ? "button" : "div";
          return "<" + tag + (drillable ? " type=\"button\"" : "") + " class=\"party-slot " + esc(evidence) + "\"" +
            (drillable ? drillAttrs("party-slot", "Subgroup " + partyNumber + " · " + profession, detail,
              roleEvidence) : "") +
            " aria-label=\"Subgroup " + esc(partyNumber) + " slot " + (slotIndex + 1) +
            ": " + esc(profession) + ", " + esc(roleText) + "\">" +
            professionGlyph(profession) + "<span class=\"slot-copy\"><b>" + esc(profession) +
            "</b><small>" + esc(evidence === "observed" ? "Observed class" :
              evidence === "open" ? "Open" : "Estimated slot") + "</small></span>" +
            "<span class=\"role-badge role-" + roleKind + "\">" +
            roleGlyph(role)+esc(roleText) + "</span></" + tag + ">";
        }).join("");
        return "<article class=\"party\"><header><b>Subgroup " +
          fmt(partyNumber) + "</b>" + (confidenceVaries ? "<span>" +
          esc(confidenceLabel(party.confidence)) + " confidence</span>" : "") + "</header>" +
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
      enemyCoverage() + enemyScopeButtons() +
      "<select id=\"enemy-color\" class=\"enemy-scope-select\" aria-label=\"Opponent scope\">" +
      "<option value=\"all\">All opponents</option>" + options + "</select>" +
      "<div class=\"intel-controls\"><label>Selected opponent detail<select id=\"enemy-detail\" disabled>" +
      "<option value=\"summary\">Choose an opponent above</option></select></label></div>" +
      "<div id=\"enemy-panel\">" + comparisonView() + "</div>" +
      (ai ? "<article class=\"ai-read\"><span class=\"eyebrow\">Optional analysis</span>" +
        "<h2>AI Enemy Read</h2><p>" + esc(ai.summary || ai.text || ai) +
        "</p><small>Embedded when the report was generated. Opening this file makes no model call.</small></article>" : "");
  }
  function comparisonPlayerKey(row) {
    return String(row && (row.account || row.name) || "").replace(/^:/,"").trim().toLowerCase();
  }
  function comparisonBaseLabel(player) {
    return titleCase(professionBase(player && player.profession));
  }
  function comparisonEliteLabel(player) {
    var base=comparisonBaseLabel(player),profession=String(player && player.profession || "Unknown");
    return base.toLowerCase() === profession.toLowerCase() ? "Core" : profession;
  }
  function comparisonPlayers() {
    var players={};
    function ensure(row) {
      row=row || {}; var key=comparisonPlayerKey(row);
      if (!key) return null;
      var player=players[key] || {key:key,name:row.name || (row.names || [])[0] || row.account,
        account:String(row.account || "").replace(/^:/,""),profession:row.profession || (row.professions || [])[0] || "Unknown",tables:{}};
      if (!player.name && row.name) player.name=row.name;
      if ((!player.profession || player.profession === "Unknown") && row.profession) player.profession=row.profession;
      players[key]=player; return player;
    }
    var skillSource=model.player_skill_damage || [],skillPlayers=Array.isArray(skillSource) ? skillSource : (skillSource.players || []);
    var evidencePlayers=((model.player_skill_evidence || {}).players || []);
    evidencePlayers.forEach(function(row){
      var player=ensure({account:row.account,name:(row.names || [])[0],profession:(row.professions || [])[0]});
      if (player) player.evidence=row;
    });
    skillPlayers.forEach(function(row){var player=ensure(row);if(player) player.skillDamage=row;});
    function existing(row) {
      var direct=players[comparisonPlayerKey(row)];
      if (direct) return direct;
      var name=String(row && row.name || "").trim().toLowerCase();
      return Object.keys(players).map(function(key){return players[key];}).find(function(player){
        return String(player.name || "").trim().toLowerCase() === name;
      });
    }
    (model.stat_tables || []).forEach(function(table){(table.rows || []).forEach(function(row){
      var player=existing(row); if (player) player.tables[table.source_key]=row;
    });});
    return Object.keys(players).map(function(key){return players[key];}).filter(function(player){
      return player.name && (player.skillDamage || player.evidence);
    }).sort(function(a,b){
      return professionBase(a.profession).localeCompare(professionBase(b.profession)) ||
        comparisonEliteLabel(a).localeCompare(comparisonEliteLabel(b)) ||
        String(a.name).localeCompare(String(b.name));
    });
  }
  function comparisonMetric(player,source,key) {
    var row=player && player.tables && player.tables[source];
    if (!row) return null;
    if (key === "__total") return row.total != null ? Number(row.total) : Number(row.value);
    var value=row.metrics && row.metrics[key];
    return value == null || value === "" ? null : Number(value);
  }
  function comparisonMetricHtml(first,second) {
    var sections=[
      ["Damage",[["Damage","targetdamage","Enemy-player damage","number",true],["Damage","targetdamageps","DPS","number",true],["Damage","targetpowerps","Power DPS","number",true],["Damage","targetconditionps","Condition DPS","number",true]]],
      ["Fight impact",[["Offensive-Summary","downed","Enemy downs","number",true],["Offensive-Summary","killed","Enemy kills","number",true],["Offensive-Summary","downcontribution","Down-contribution damage","number",true],["Offensive-Summary","appliedcrowdcontrol","Crowd control","number",true],["Offensive-Summary","interrupts","Interrupts","number",true]]],
      ["Healing & support",[["Heal-Stats","healing","Healing","number",true],["Heal-Stats","healingps","Healing / sec","number",true],["Heal-Stats","barrier","Barrier","number",true],["Support-Summary","condicleanse","Allied cleanses","number",true],["Support-Summary","boonstrips","Enemy boons removed","number",true],["Support-Summary","resurrects","Resurrects","number",true]]],
      ["Boons & activity",[["Uptimes","stability","Stability uptime","percent",true],["Uptimes","protection","Protection uptime","percent",true],["Uptimes","might","Might uptime","percent",true],["Attendance","activetime","Active fight time","duration",false],["Attendance","numfights","Fights attended","number",false]]]
    ];
    return sections.map(function(section){
      var rows=section[1].map(function(def){
        var a=comparisonMetric(first,def[0],def[1]),b=comparisonMetric(second,def[0],def[1]);
        if (a == null && b == null) return "";
        function display(value){return value == null ? "—" : def[3] === "percent" ? fmt(value)+"%" : def[3] === "duration" ? humanDuration(value) : fmt(value);}
        var edge="Context only",aClass="",bClass="";
        if (def[4] && a != null && b != null) {
          if (a === b) edge="Even";
          else {
            var winner=a>b ? first : second,high=Math.max(a,b),low=Math.min(a,b);
            edge=esc(winner.name)+" +"+fmt(low ? (high-low)/low*100 : 100)+"%";
            if (a>b) aClass=" compare-lead"; else bClass=" compare-lead";
          }
        }
        return "<div class=\"compare-stat\"><span class=\"compare-metric-label\">"+esc(def[2])+"</span><b class=\"compare-player-a"+aClass+"\">"+display(a)+"</b><b class=\"compare-player-b"+bClass+"\">"+display(b)+"</b><small class=\"compare-edge\">"+edge+"</small></div>";
      }).filter(Boolean).join("");
      return rows ? "<article class=\"compare-metric-group\"><h3>"+esc(section[0])+"</h3><div class=\"compare-stat compare-stat-head\"><span class=\"compare-metric-label\">Metric</span><b class=\"compare-player-a\">"+esc(first.name)+"</b><b class=\"compare-player-b\">"+esc(second.name)+"</b><small class=\"compare-edge\">Edge</small></div>"+rows+"</article>" : "";
    }).join("");
  }
  function skillChartPoint(cx,cy,r,angle) {
    var radians=(angle-90)*Math.PI/180;
    return {x:cx+r*Math.cos(radians),y:cy+r*Math.sin(radians)};
  }
  function skillChartPath(cx,cy,outer,inner,start,end) {
    if (end-start >= 359.999) end=start+359.999;
    var a=skillChartPoint(cx,cy,outer,start),b=skillChartPoint(cx,cy,outer,end);
    var c=skillChartPoint(cx,cy,inner,end),d=skillChartPoint(cx,cy,inner,start);
    var large=end-start>180 ? 1 : 0;
    return "M "+a.x.toFixed(2)+" "+a.y.toFixed(2)+" A "+outer+" "+outer+" 0 "+large+" 1 "+b.x.toFixed(2)+" "+b.y.toFixed(2)+" L "+c.x.toFixed(2)+" "+c.y.toFixed(2)+" A "+inner+" "+inner+" 0 "+large+" 0 "+d.x.toFixed(2)+" "+d.y.toFixed(2)+" Z";
  }
  function skillChartValue(row,valueKey) {
    return Number(row && row[valueKey] || 0);
  }
  function skillChartSvg(rows,total,large,interactive,valueKey,unitLabel) {
    valueKey=valueKey || "damage";unitLabel=unitLabel || "damage";
    var width=large ? 720 : 390,height=large ? 520 : 290,cx=large ? 270 : 145,cy=large ? 255 : 145;
    var outer=large ? 165 : 92,inner=large ? 80 : 44,labelRadius=large ? 215 : 119;
    var cursor=0,colors=["var(--accent)","var(--good)","var(--purple)","var(--accent-2)","var(--bad)","#58a6ff","#d29922","var(--faint)"];
    var slices=rows.map(function(row,index){
      var value=skillChartValue(row,valueKey),start=cursor,end=cursor+value/total*360;cursor=end;
      var middle=(start+end)/2,label=skillChartPoint(cx,cy,labelRadius,middle),right=label.x>=cx;
      var percent=value/total*100,short=String(row.skill || "Unknown skill");
      if (short.length>(large?24:14)) short=short.slice(0,large?22:12)+"…";
      return "<g class=\"skill-chart-slice\" data-skill-slice=\""+index+"\""+(interactive?" tabindex=\"0\" role=\"button\" aria-label=\""+esc((row.skill || "Unknown skill")+" "+percent.toFixed(1)+" percent; open chart details")+"\"":"")+"><path d=\""+skillChartPath(cx,cy,outer,inner,start,end)+"\" fill=\""+colors[index%colors.length]+"\"><title>"+esc((row.skill || "Unknown skill")+": "+fmt(value)+" "+unitLabel+" · "+percent.toFixed(1)+"%")+"</title></path><line x1=\""+skillChartPoint(cx,cy,outer+3,middle).x.toFixed(1)+"\" y1=\""+skillChartPoint(cx,cy,outer+3,middle).y.toFixed(1)+"\" x2=\""+label.x.toFixed(1)+"\" y2=\""+label.y.toFixed(1)+"\"></line><text class=\"skill-chart-label\" x=\""+label.x.toFixed(1)+"\" y=\""+(label.y-3).toFixed(1)+"\" text-anchor=\""+(right?"start":"end")+"\"><tspan>"+esc(short)+"</tspan><tspan x=\""+label.x.toFixed(1)+"\" dy=\"12\">"+percent.toFixed(1)+"%</tspan></text></g>";
    }).join("");
    return "<svg class=\"skill-chart-svg"+(large?" large":"")+"\" viewBox=\"0 0 "+width+" "+height+"\" aria-label=\"Labeled "+esc(unitLabel)+" share pie chart\">"+slices+"<text class=\"skill-chart-total\" x=\""+cx+"\" y=\""+(cy-2)+"\" text-anchor=\"middle\">"+fmt(total)+"</text><text class=\"skill-chart-total-sub\" x=\""+cx+"\" y=\""+(cy+16)+"\" text-anchor=\"middle\">"+esc(unitLabel)+"</text></svg>";
  }
  function skillSharePie(skills,title,valueKey,unitLabel) {
    valueKey=valueKey || "damage";unitLabel=unitLabel || "damage";
    var rows=(skills || []).filter(function(row){return skillChartValue(row,valueKey)>0;})
      .slice().sort(function(a,b){return skillChartValue(b,valueKey)-skillChartValue(a,valueKey);});
    if (!rows.length) return "";
    var total=rows.reduce(function(sum,row){return sum+skillChartValue(row,valueKey);},0),top=rows.slice(0,7);
    var represented=top.reduce(function(sum,row){return sum+skillChartValue(row,valueKey);},0);
    if (represented < total) {var other={skill:"Other skills"};other[valueKey]=total-represented;top.push(other);}
    var payload=encodeURIComponent(JSON.stringify(top));
    return "<div class=\"skill-share\"><div class=\"skill-pie-visual\" data-skill-chart data-chart-title=\""+esc(title)+"\" data-chart-total=\""+total+"\" data-chart-value-key=\""+esc(valueKey)+"\" data-chart-unit=\""+esc(unitLabel)+"\" data-chart-skills=\""+esc(payload)+"\">"+skillChartSvg(top,total,false,true,valueKey,unitLabel)+"<button type=\"button\" class=\"open-skill-chart\">Open larger labeled chart</button></div><div><h4>"+esc(title)+"</h4><p class=\"skill-chart-help\">Select any labeled slice for a larger breakdown.</p><div class=\"skill-pie-legend\">"+top.map(function(row,index){return "<button type=\"button\" data-skill-slice=\""+index+"\"><b>"+esc(row.skill || "Unknown skill")+"</b><small>"+fmt(skillChartValue(row,valueKey)/total*100)+"%</small></button>";}).join("")+"</div></div></div>";
  }
  function comparisonSkillPanel(player) {
    var skills=player.skillDamage && player.skillDamage.skills || [];
    if (!skills.length) {
      var casts=Object.keys(player.evidence && player.evidence.skill_casts || {}).map(function(skill){
        return {skill:skill,count:Number(player.evidence.skill_casts[skill] || 0)};
      }).filter(function(row){return row.count>0;}).sort(function(a,b){return b.count-a.count;});
      if (!casts.length) return "<article class=\"compare-player-detail\"><h3>"+esc(player.name)+" · Skills</h3><p class=\"muted\">No detailed skill evidence is available for this squad player.</p></article>";
      var totalCasts=casts.reduce(function(total,row){return total+row.count;},0);
      return "<article class=\"compare-player-detail\"><h3>"+esc(player.name)+" · Skills</h3><p class=\"compare-skill-basis\">Cast share is usage frequency, not damage output.</p>"+skillSharePie(casts,"Cast share by skill","count","casts")+"<div class=\"compare-skill-list\">"+
        casts.slice(0,12).map(function(row,index){var share=totalCasts ? row.count/totalCasts*100 : 0;return "<div><span>"+(index+1)+". "+esc(row.skill)+"</span><b>"+fmt(row.count)+" casts<small>"+fmt(share)+"%</small></b></div>";}).join("")+"</div></article>";
    }
    var ordered=skills.slice().sort(function(a,b){return Number(b.damage || 0)-Number(a.damage || 0);});
    return "<article class=\"compare-player-detail\"><h3>"+esc(player.name)+" · Skills</h3><p class=\"compare-skill-basis\">Damage share uses exported skill damage totals.</p>"+skillSharePie(ordered,"Damage share by skill")+"<div class=\"compare-skill-list\">"+ordered.slice(0,12).map(function(skill,index){return "<div><span>"+(index+1)+". "+esc(skill.skill)+"<small>"+fmt(skill.hits)+" hits · "+fmt(skill.down_contribution)+" down contribution</small></span><b>"+fmt(skill.damage)+"<small>"+fmt(skill.percent_of_total)+"%</small></b></div>";}).join("")+"</div></article>";
  }
  function comparisonEvidencePanel(player) {
    var evidence=player.evidence;
    if (!evidence) return "<article class=\"compare-player-detail\"><h3>"+esc(player.name)+" · Weapons / Rotation</h3><p class=\"muted\">Detailed weapon and rotation evidence was not retained in this older combined report. New reports preserve it from Elite Insights JSON.</p></article>";
    var casts=Object.keys(evidence.skill_casts || {}).map(function(skill){return {skill:skill,count:Number(evidence.skill_casts[skill] || 0)};}).sort(function(a,b){return b.count-a.count;}).slice(0,12);
    var links=Object.keys(evidence.rotation_links || {}).map(function(link){return {link:link,count:Number(evidence.rotation_links[link] || 0)};}).sort(function(a,b){return b.count-a.count;}).slice(0,8);
    var consumables=evidence.consumables || [],traits=evidence.traits || [],roleSignal=evidence.consumable_role_signal;
    return "<article class=\"compare-player-detail\"><h3>"+esc(player.name)+" · Build Evidence</h3><p><b>Observed weapons:</b> "+esc((evidence.weapons || []).join(" · ") || "Not exposed")+"</p>"+
      (consumables.length ? "<h4>Exact food / utility</h4><div class=\"build-evidence-list\">"+consumables.map(function(item){return "<div><b>"+esc(item.name)+"</b><span>"+esc(item.classification)+((item.roles || []).length ? " · "+esc(item.roles.join(" / ")) : "")+"</span></div>";}).join("")+"</div>" : "")+
      (roleSignal ? "<p class=\"evidence-signal\"><b>Strong "+esc(roleSignal.role)+" signal:</b> food and utility independently match.</p>" : "")+
      (traits.length ? "<h4>Proven major traits</h4><div class=\"build-evidence-list\">"+traits.map(function(item){return "<div><b>"+esc(item.trait)+"</b><span>"+esc(item.specialization)+" · "+esc((item.roles || []).join(" / "))+"</span><small>Triggered "+esc(item.observed_skill)+"</small><p>"+esc(item.trait_description || item.evidence_skill_description || "")+"</p></div>";}).join("")+"</div>" : "")+
      (casts.length ? "<h4>Most-used casts</h4><div class=\"chips\">"+casts.map(function(row){return "<span><b>"+esc(row.skill)+"</b> · "+fmt(row.count)+"</span>";}).join("")+"</div>" : "")+
      (links.length ? "<h4>Common cast transitions</h4><div class=\"chips\">"+links.map(function(row){return "<span><b>"+esc(row.link)+"</b> · "+fmt(row.count)+"</span>";}).join("")+"</div>" : "")+"</article>";
  }
  function renderPlayerComparison(firstKey,secondKey) {
    var players=comparisonPlayers(),first=players.find(function(player){return player.key===firstKey;}) || players[0];
    var second=players.find(function(player){return player.key===secondKey;}) || players[1] || players[0];
    if (!first || !second) return "<p class=\"empty\">At least two exported squad players are required.</p>";
    var same=first.profession === second.profession;
    function playerCard(player){return "<div class=\"compare-player-card\">"+professionGlyph(player.profession)+"<div class=\"compare-player-copy\"><span>"+esc(player.profession)+"</span><b>"+esc(player.name)+"</b><small>"+esc(player.account || "Account unavailable")+"</small></div></div>";}
    return "<div class=\"compare-head\">"+playerCard(first)+"<span class=\""+(same?"same-profession":"different-profession")+"\">"+(same?"Same profession · direct build comparison":"Different professions · role context matters")+"</span>"+playerCard(second)+"</div>"+
      comparisonMetricHtml(first,second)+"<div class=\"compare-detail-grid\">"+comparisonSkillPanel(first)+comparisonSkillPanel(second)+comparisonEvidencePanel(first)+comparisonEvidencePanel(second)+"</div>";
  }
  function playerComparisonView() {
    var players=comparisonPlayers();
    if (players.length < 2) return "<p class=\"empty\">At least two exported squad players are required for comparison.</p>";
    var first=players[0],second=players[1];
    for (var i=0;i<players.length;i+=1) {var peer=players.slice(i+1).find(function(player){return player.profession===players[i].profession;});if(peer){first=players[i];second=peer;break;}}
    function options(selected){return players.map(function(player){var base=comparisonBaseLabel(player),elite=comparisonEliteLabel(player);return "<option value=\""+esc(player.key)+"\""+(player.key===selected?" selected":"")+">"+esc(base+" · "+elite+" · "+player.name)+"</option>";}).join("");}
    return sectionHead("Squad learning", "Compare two players", "Only squad players with detailed skill or build evidence are listed. Same-profession comparisons are strongest.")+
      "<div class=\"compare-controls\"><label>Player A<select id=\"compare-player-a\">"+options(first.key)+"</select></label><label>Player B<select id=\"compare-player-b\">"+options(second.key)+"</select></label><label class=\"compare-check\"><input type=\"checkbox\" id=\"compare-same-profession\"> Same profession only</label></div><div id=\"player-comparison-results\">"+renderPlayerComparison(first.key,second.key)+"</div>";
  }
  function renderSparky() {
    var boards = allBoards();
    var hasHistoricalLeaderboards=(model.leaderboards || []).some(function(board){return (board.rows || []).length;});
    var overview = subnav("overview", [["summary","Night Summary"],["timeline","Fight Timeline"]]) +
      subpanel("overview", "summary", enemyCoverage() +
        sectionHead("Fight review", "Outcome and high-impact findings", "Fight conversion, losses, incoming damage, and standout players without the table hunt.") +
        nightMvpCards() + outcomeChart() +
        boardGrid([], 8, "No overview boards were found."), false) +
      subpanel("overview", "timeline", sectionHead("Fight by fight", "Night timeline", "Follow momentum and open the detailed rows below.") +
        fightsTable(true), true);
    var damageBoard=derivedDamageBoard("Damage to Enemy Players","targetdamage","targetdamageps","DPS");
    var dps = subnav("dps", [["overview","Overview"],["direct","Power Damage"],["conditions","Condition Damage"],["skills","Skills"],["pressure","Fight Impact"]]) +
      subpanel("dps", "overview", sectionHead("Damage", "DPS overview", "Total DPS split into Power and Condition Damage, followed by burst, down contribution, enemy downs, and kills.") + damageCompositionView() + "<div class=\"boards\">" + (damageBoard ? boardCard(damageBoard,100) : "") + "</div>" + fightImpactBoards(false) + highScoreGridExact(["Highest 1s Burst Damage","Highest Outgoing Skill Damage","Damage per Second"]), false) +
      subpanel("dps", "direct", sectionHead("DPS", "Power Damage", "Power Damage to enemy players and one-second burst records.") + powerDamageView(), true) +
      subpanel("dps", "conditions", sectionHead("DPS", "Condition Damage", "Condition Damage is separate from condition applications and uptime.") + conditionDamageView(), true) +
      subpanel("dps", "skills", sectionHead("Execution", "Damage by Skill", "Per-player skill damage for players whose tables were exported; missing players are not zero.") + playerSkillDamageView() + highScoreGridExact(["Highest Outgoing Skill Damage"]), true) +
      subpanel("dps", "pressure", sectionHead("Conversion", "Fight Impact", "Down-contribution damage, enemy downs, and enemy kills with totals and participation-normalized rates.") + fightImpactBoards(true) + highScoreGridExact(["Down Contrib per Second","Downs per Second","Kills per Second"]), true);
    var support = subnav("support", [["overview","Overview"],["cleanses","Cleanses"],["strips","Boon Strips & Crowd Control"],["boons","Stability / Boons"],["res","Resurrects"]]) +
      subpanel("support", "overview", sectionHead("Squad utility", "Support overview", "Separate leaderboards for cleanses, Boons Removed, Crowd Control, and revives—no combined score.") + supportOverviewView(), false) +
      subpanel("support", "cleanses", sectionHead("Condition management", "Condition Cleanses", "Total cleanses and participation-normalized cleanses per minute.") + cleansesView(), true) +
      subpanel("support", "strips", sectionHead("Disruption", "Boons Removed & Crowd Control", "Outgoing boon removal and Crowd Control are distinct measurements.") + stripsAndControlView(), true) +
      subpanel("support", "boons", boonEconomyContext() + boonGenerationCharts() +
        boonUptimeCharts(["stability","protection","aegis","resolution","resistance","regeneration","vigor"], "Defensive Boons") +
        boonUptimeCharts(["might","fury","quickness","alacrity"], "Offensive Boons") +
        metricBars(["stability generation"], "Stability Generation", "support", 10) +
        boardGrid(["stability generation"], 20), true) +
      subpanel("support", "res", sectionHead("Recovery", "Resurrects", "Revive counts are separate from healing delivered by resurrection-related skills.") + resurrectView(), true);
    var healing = subnav("healing", [["overview","Overview"],["barrier","Healing & Barrier"],["profiles","By Profession"],["skills","Resurrection Skills"]]) +
      subpanel("healing", "overview", sectionHead("Sustain", "Healing overview", "A compact comparison of Healing, Barrier, and healing to downed allies.") + healingOverviewView(), false) +
      subpanel("healing", "barrier", sectionHead("Sustain detail", "Healing & Barrier", "Separate sortable player tables; totals and participation-normalized rates stay visible.") + healingAndBarrierView(), true) +
      subpanel("healing", "profiles", sectionHead("Composition", "Healing by Profession", "Combined player totals grouped by profession and normalized by combined participation time.") + healingProfilesView(), true) +
      subpanel("healing", "skills", sectionHead("Recovery skills", "Combat-Resurrection Healing by Skill", "Skill-level healing is available for resurrection-related skills; healing by target was not exported.") + resurrectionSkillView(), true);
    var scoreTabs=[["all","All"],["offense","Offense"],["support","Support"],["healing","Healing"],["defense","Defense"]]
      .concat(hasHistoricalLeaderboards ? [["leaderboards","Long-term Leaderboards"]] : []);
    var scores = subnav("scores", scoreTabs) +
      subpanel("scores", "all", highScoreGrid([]), false) +
      subpanel("scores", "offense", highScoreGridExact(["Highest 1s Burst Damage","Highest Outgoing Skill Damage","Damage per Second","Kills per Second","Downs per Second","Down Contrib per Second"]), true) +
      subpanel("scores", "support", highScoreGridExact(["Cleanses per Second","Strips per Second","Crowd Control-Out per Second"]), true) +
      subpanel("scores", "healing", highScoreGridExact(["Healing per Second","Barrier per Second"]), true) +
      subpanel("scores", "defense", highScoreGridExact(["Highest Incoming Skill Damage","Blocks per Second","Evades per Second","Dodges per Second","Invulned per Second","Crowd Control-In per Second"]), true) +
      (hasHistoricalLeaderboards ? subpanel("scores", "leaderboards", sectionHead("Historical context", "Long-term Leaderboards", "Averages across raids; never presented as tonight’s performance.") + longTermLeaderboardGrid(), true) : "");
    var compare = playerComparisonView();
    var details = subnav("details", [["fights","Fights"],["players","Players"],["attendance","Attendance"],["composition","Composition"],["tables","All Tables"]]) +
      subpanel("details", "fights", fightsTable(false), false) +
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
      [["overview","Overview"],["dps","DPS"],["support","Support"],["healing","Healing"],["compare","Player Compare"],
       ["scores","High Scores"],["enemy","Enemy Intel"],["details","Details / Fights"]].map(function (item, i) {
        return "<button type=\"button\" role=\"tab\" aria-selected=\"" +
          (i ? "false" : "true") + "\" class=\"" + (i ? "" : "selected") +
          "\" data-tab=\"" + item[0] + "\">" + item[1] + "</button>";
      }).join("") + "</nav>" +
      "<section data-section=\"overview\">" + overview + "</section>" +
      "<section data-section=\"dps\" hidden>" + dps + "</section>" +
      "<section data-section=\"support\" hidden>" + support + "</section>" +
      "<section data-section=\"healing\" hidden>" + healing + "</section>" +
      "<section data-section=\"compare\" hidden>" + compare + "</section>" +
      "<section data-section=\"scores\" hidden>" + scores + "</section>" +
      "<section data-section=\"enemy\" hidden>" + enemyIntelShell() + "</section>" +
      "<section data-section=\"details\" hidden>" + details +
      "<div class=\"source-callout\"><b>Need the untouched upstream report?</b>" +
      "<button id=\"open-classic\">Open Classic</button></div></section>" +
      "<dialog id=\"drilldown\" aria-labelledby=\"drill-title\"><div class=\"drill-head\">" +
      "<div><span class=\"eyebrow\" id=\"drill-kicker\">Detailed breakdown</span><h2 id=\"drill-title\">Details</h2></div>" +
      "<button type=\"button\" id=\"drill-close\" aria-label=\"Close details\">Close</button></div>" +
      "<div class=\"drill-body\"><div id=\"drill-detail\"></div><div class=\"drill-evidence\" " +
      "id=\"drill-evidence\"></div></div></dialog>" +
      "<dialog id=\"skill-chart-dialog\" aria-labelledby=\"skill-chart-title\"><div class=\"drill-head\"><div><span class=\"eyebrow\">Skill contribution</span><h2 id=\"skill-chart-title\">Skill share</h2></div><button type=\"button\" id=\"skill-chart-close\">Close</button></div><div class=\"skill-chart-dialog-body\"><div id=\"skill-chart-large\"></div><aside id=\"skill-chart-selection\" aria-live=\"polite\"></aside></div></dialog>";
    var extraCss = [
      ".session-strip{position:sticky;top:0;z-index:5;display:flex;gap:18px;align-items:center;",
      "padding:9px 14px;margin:-30px -24px 24px;background:var(--surface);border-bottom:1px solid var(--line);",
      "color:var(--muted);font-size:12px}.session-strip b{color:var(--text)}",
      ".tabs{position:sticky;top:36px;z-index:4;display:flex;flex-wrap:wrap;gap:4px;margin:24px 0 16px;",
      "padding:6px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}",
      ".tabs button,.subtabs button,.source-callout button{border:1px solid transparent;border-radius:7px;",
      "padding:9px 12px;background:transparent;color:var(--muted);font:inherit;font-weight:700;cursor:pointer}",
      ".tabs button:hover,.subtabs button:hover{color:var(--text);border-color:var(--line)}",
      ".tabs button.selected{background:var(--accent);color:var(--on-accent)}",
      ".subtabs{display:flex;flex-wrap:wrap;gap:6px;overflow:hidden;margin:0 0 20px;border-bottom:1px solid var(--line);padding:0 0 8px}",
      ".subtabs button.selected{color:var(--accent);background:var(--panel);border-color:var(--line)}",
      ".section-head{display:flex;align-items:end;justify-content:space-between;gap:20px;margin:28px 0 14px}",
      ".section-head h2{font-size:24px;margin:4px 0 0}.section-head p{color:var(--muted);max-width:520px;margin:0}",
      ".table-panel{border:1px solid var(--line);border-radius:10px;background:var(--panel)}",
      ".search{display:block;color:var(--muted);margin:12px 0}.search input,.intel-controls select{display:block;",
      "margin-top:5px;min-width:240px;border:1px solid var(--line);border-radius:7px;padding:9px 11px;",
      "background:var(--panel);color:var(--text);font:inherit}",
      ".enemy-scope-select{display:none}.enemy-scope-shell{margin:18px 0 20px;padding:17px;border:1px solid var(--line);border-radius:12px;background:linear-gradient(135deg,var(--panel),var(--panel-2))}.enemy-scope-heading{display:flex;align-items:end;justify-content:space-between;gap:18px;margin-bottom:13px}.enemy-scope-heading h3{margin:3px 0 0;font-size:20px}.enemy-scope-heading p{max-width:560px;margin:0;color:var(--muted);font-size:12px}.enemy-scope-switcher{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.enemy-scope-button{--scope-color:var(--accent);position:relative;min-width:0;min-height:105px;padding:15px 48px 14px 16px;border:1px solid var(--line);border-left:5px solid var(--scope-color);border-radius:9px;background:color-mix(in srgb,var(--scope-color) 6%,var(--panel));color:var(--text);font:inherit;text-align:left;cursor:pointer;transition:transform .14s ease,border-color .14s ease,background .14s ease}.enemy-scope-button[data-enemy-color=green]{--scope-color:#38d996}.enemy-scope-button[data-enemy-color=red]{--scope-color:#ff5a6f}.enemy-scope-button[data-enemy-color=blue]{--scope-color:#4ba8ff}.enemy-scope-button:hover{transform:translateY(-2px);border-color:var(--scope-color);background:color-mix(in srgb,var(--scope-color) 11%,var(--panel))}.enemy-scope-button.selected{border-color:var(--scope-color);background:color-mix(in srgb,var(--scope-color) 18%,var(--panel));box-shadow:inset 0 0 0 1px var(--scope-color)}.enemy-scope-button.selected:after{content:'VIEWING';position:absolute;top:13px;right:12px;padding:3px 6px;border-radius:4px;background:var(--scope-color);color:#020305;font-size:8px;font-weight:900;letter-spacing:.08em}.enemy-scope-button span,.enemy-scope-button b,.enemy-scope-button small{display:block;min-width:0}.enemy-scope-button span{color:var(--scope-color);font-size:9px;font-weight:900;letter-spacing:.1em;text-transform:uppercase}.enemy-scope-button b{margin:5px 0 7px;font-size:18px}.enemy-scope-button small{color:var(--muted);font-size:10px;line-height:1.35}.enemy-scope-button.selected small{color:var(--text)}",
      ".scope-roadmap{margin:14px 0 18px;padding:15px;border:1px solid var(--accent-2);border-radius:11px;background:linear-gradient(135deg,color-mix(in srgb,var(--accent-2) 8%,var(--panel)),var(--panel))}.scope-roadmap-head{display:flex;align-items:end;justify-content:space-between;gap:16px;margin-bottom:11px}.scope-roadmap-head h3{margin:3px 0 0;font-size:19px}.scope-roadmap-head p{max-width:500px;margin:0;color:var(--muted);font-size:11px}.scope-roadmap-actions{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px}.scope-roadmap-actions>button{min-width:0;padding:10px;border:1px solid var(--line);border-radius:7px;background:var(--panel-2);color:var(--text);font:inherit;text-align:left;cursor:pointer}.scope-roadmap-actions>button:hover{border-color:var(--accent)}.scope-roadmap-actions b,.scope-roadmap-actions small{display:block}.scope-roadmap-actions b{font-size:11px}.scope-roadmap-actions small{margin-top:3px;color:var(--muted);font-size:9px;line-height:1.25}.scope-mini-wrap{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;align-items:center;margin-top:11px;padding-top:10px;border-top:1px solid var(--line-soft)}.scope-mini-wrap>b{font-size:11px}.scope-mini-wrap>span{color:var(--muted);font-size:9px}.scope-mini-parties{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px;margin-top:5px}.scope-mini-party{display:flex;align-items:center;gap:4px;min-width:0;padding:5px 7px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--text);cursor:pointer}.scope-mini-party>span{margin-right:2px;color:var(--muted);font-size:9px;font-weight:800}.scope-mini-party .profession-glyph{flex:0 0 22px;width:22px;height:22px;margin:0}.scope-mini-party:hover{border-color:var(--accent-2)}",
      ".squad-grid,.intel-grid,.comparison-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}",
      ".squad-fights{display:grid;gap:10px}.squad-fight{border:1px solid var(--line);border-radius:9px;background:var(--panel);overflow:hidden}.squad-fight summary{display:flex;justify-content:space-between;gap:12px;padding:13px 15px;cursor:pointer}.squad-fight summary span{color:var(--muted);font-size:11px}.our-party-list{display:grid;gap:7px;padding:0 12px 12px}.our-party-row{display:grid;grid-template-columns:68px minmax(0,1fr);gap:9px;align-items:center;padding:8px;border:1px solid var(--line-soft);border-radius:7px;background:var(--panel-2)}.our-party-row>b{color:var(--muted);font-size:11px}.our-party-members{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px}.our-party-player{display:flex;align-items:center;gap:6px;min-width:0;padding:7px;border:1px solid var(--line-soft);border-radius:6px;background:var(--panel);color:var(--text);font:inherit;text-align:left;cursor:pointer}.our-party-player:hover{border-color:var(--accent)}.our-party-player .profession-glyph{flex:0 0 25px;width:25px;height:25px;margin:0}.our-party-player span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px}",
      ".squad,.intel-card,.chart-card,.ai-read{border:1px solid var(--line);border-radius:10px;padding:15px;background:var(--panel)}",
      ".squad h3,.intel-card h3{margin:0 0 10px}.squad span{display:block;color:var(--muted);font-size:12px}",
      ".spotlight{display:grid;grid-template-columns:1.6fr 1fr;gap:20px;padding:22px;border:1px solid var(--line);",
      "border-left:4px solid var(--good);border-radius:10px;background:var(--panel);margin:18px 0}",
      ".spotlight h2{font-size:28px;margin:5px 0}.spotlight p{color:var(--muted)}.spot-metrics{display:grid;gap:10px}",
      ".spot-metrics div,.intel-kpis div{padding:13px;background:var(--panel-2);border-radius:8px}",
      ".spot-metrics strong,.intel-kpis strong{display:block;font-size:22px;color:var(--accent)}",
      ".spot-metrics span,.intel-kpis span{color:var(--muted);font-size:12px}.accuracy{font-size:12px;color:var(--muted)}",
      ".chart-title{display:flex;justify-content:space-between;color:var(--muted)}.chart-title b{color:var(--text)}",
      ".svg-scroll{width:100%;overflow:hidden}.chart-card{min-width:0;margin:18px 0 30px}.chart-card svg{display:block;width:100%;max-width:100%;min-width:0;height:auto;max-height:210px}.axis{stroke:var(--line)}",
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
      ".tone-danger .metric-row em,.tone-strip .metric-row em{background:var(--series-deaths)}.bar-label{min-width:0;overflow:hidden}.player-bar-label{display:flex;align-items:center;gap:9px;text-align:left}.player-bar-label .profession-glyph{flex:0 0 27px;width:27px;height:27px;margin:0}.metric-player-text{display:block;min-width:0}.metric-player-text b,.metric-player-text small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.metric-player-text b{font-size:12px;line-height:1.2}.metric-player-text small{margin-top:2px;color:var(--muted);font-size:10px;line-height:1.15}",
      ".pressure-row .bar-label{white-space:nowrap;text-overflow:ellipsis}",
      ".profession-row .bar-label{white-space:nowrap;text-overflow:ellipsis}.profession-row .profession-inline{max-width:100%;overflow:hidden}",
      ".enemy-skill-board{margin:18px 0 30px}.enemy-skill-board>.chart-title{padding:14px 16px 8px}.skill-table-note{margin:0;padding:0 16px 12px;color:var(--muted);font-size:11px}.enemy-skill-table th,.enemy-skill-table td{padding-left:6px;padding-right:6px}.enemy-skill-table th{font-size:10px;letter-spacing:.02em}.enemy-skill-table .skill-rank{width:5%}.enemy-skill-table .skill-name{width:29%}.enemy-skill-table .skill-damage{width:14%}.enemy-skill-table .skill-share{width:8%}.enemy-skill-table .skill-hits{width:16%}.enemy-skill-table .skill-casts{width:13%}.enemy-skill-table .skill-per-hit{width:15%}.enemy-skill-table td:nth-child(2){white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.enemy-skill-table td:nth-child(2) b{font-size:12px}",
      ".strip-pressure-card{margin:18px 0 28px;padding:16px;border:1px solid var(--line);border-top:3px solid var(--series-deaths);border-radius:10px;background:var(--panel)}.strip-scoreline{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:13px 0 17px}.strip-scoreline>div{padding:13px;border:1px solid var(--line-soft);border-radius:8px;background:var(--panel-2)}.strip-scoreline span,.strip-scoreline strong,.strip-scoreline b{display:block}.strip-scoreline span{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.08em}.strip-scoreline strong{margin:3px 0;font-size:22px}.strip-scoreline b{color:var(--muted);font-size:10px}.strip-scoreline i{display:block;height:8px;margin-top:10px;border-radius:4px;background:var(--surface);overflow:hidden}.strip-scoreline em{display:block;height:100%}.strip-scoreline em.ours{background:var(--accent)}.strip-scoreline em.enemy{background:var(--bad)}.strip-pressure-card h4{margin:16px 0 8px;font-size:12px}.strip-pressure-card>p{margin:13px 0 0;color:var(--muted);font-size:11px}.strip-contributors{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:7px}.strip-contributors>button{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 10px;align-items:center;min-width:0;padding:9px;border:1px solid var(--line-soft);border-radius:7px;background:var(--panel-2);color:var(--text);font:inherit;text-align:left;cursor:pointer}.strip-contributors .player-bar-label{grid-row:1/3}.strip-contributors>button>strong{font-size:13px;text-align:right}.strip-contributors>button>small{color:var(--muted);font-size:9px;white-space:nowrap}",
      ".damage-composition-card{margin-bottom:16px}.damage-composition-legend{display:flex;gap:16px;padding:0 0 10px;color:var(--muted);font-size:10px;text-transform:uppercase}.damage-composition-legend span:before{content:'';display:inline-block;width:9px;height:9px;margin-right:5px;border-radius:2px;background:var(--muted)}.damage-composition-legend .power:before{background:var(--role-dps)}.damage-composition-legend .condition:before{background:var(--purple)}",
      ".damage-composition-row{display:grid;grid-template-columns:minmax(180px,260px) minmax(100px,1fr) minmax(82px,auto);align-items:center;gap:12px;width:100%;padding:10px 0;border:0;border-bottom:1px solid var(--line-soft);background:transparent;color:var(--text);font:inherit;text-align:left;cursor:pointer}.damage-composition-row:hover{background:var(--panel-2)}.damage-player{display:flex;align-items:center;gap:8px;min-width:0}.damage-player>span:last-child{min-width:0}.damage-player b,.damage-player small{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.damage-player small{color:var(--muted);font-size:10px}",
      ".damage-total-track{height:12px;background:var(--panel-2);border-radius:5px;overflow:hidden}.damage-total-track>i{display:flex;height:100%;border-radius:5px;overflow:hidden}.damage-total-track em{display:block;height:100%}.damage-total-track .power{background:var(--role-dps)}.damage-total-track .condition{background:var(--purple)}.damage-composition-row strong{font-variant-numeric:tabular-nums;white-space:nowrap}",
      ".boon-uptimes{margin:20px 0 28px}.boon-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.boon-card{margin:0}.boon-row{display:grid;grid-template-columns:minmax(150px,220px) minmax(80px,1fr) auto;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line-soft)}.boon-player{display:flex;align-items:center;gap:8px;min-width:0}.boon-player .profession-glyph{flex:0 0 25px;width:25px;height:25px;margin:0}.boon-player>span:last-child{min-width:0}.boon-player b,.boon-player small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.boon-player small{color:var(--muted);font-size:9px}.boon-row>i{height:8px;border-radius:3px;background:var(--panel-2);overflow:hidden}.boon-row>i em{display:block;height:100%;background:var(--role-support)}.boon-row strong{font-size:11px}",
      ".boon-generation{margin:20px 0 28px}.generation-card{margin:0}.generation-row{display:grid;grid-template-columns:minmax(155px,220px) minmax(100px,1fr) 70px 145px;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line-soft)}.generation-row>i{height:9px;border-radius:3px;background:var(--panel-2);overflow:hidden}.generation-row>i em{display:block;height:100%;background:var(--accent)}.generation-row>b{text-align:right;font-size:11px}.generation-row>small{color:var(--muted);font-size:10px;white-space:nowrap}",
      ".boon-economy{padding:16px;border:1px solid var(--line);border-top:3px solid var(--role-support);border-radius:10px;background:var(--panel);margin:20px 0}.boon-economy>p{margin:12px 0 0;color:var(--muted);font-size:11px}.economy-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:13px 0}.economy-kpis>div,.economy-uptimes span{padding:11px;border:1px solid var(--line-soft);border-radius:7px;background:var(--panel-2)}.economy-kpis span,.economy-kpis small,.economy-uptimes small{display:block;color:var(--muted);font-size:10px}.economy-kpis b{display:block;margin:3px 0;color:var(--accent);font-size:20px}.economy-uptimes{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px}.economy-uptimes b{display:block;font-size:11px}",
      ".heatmap{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:7px;padding:15px;border:1px solid var(--line);border-top:3px solid var(--purple);",
      "border-radius:10px;background:var(--panel);margin:16px 0 30px}.heat-cell{min-height:68px;padding:10px;border:1px solid color-mix(in srgb,var(--purple) 45%,var(--line));",
      "border-radius:7px;background:color-mix(in srgb,var(--purple) calc(var(--heat)*55%),var(--panel-2));color:var(--text);font:inherit;text-align:left;cursor:pointer}",
      ".heat-cell b,.heat-cell span{display:block}.heat-cell span{color:var(--muted);font-size:11px;margin-top:4px}.intel-visuals{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start;margin:16px 0 28px}",
      ".intel-visuals .bar-card,.intel-visuals .heatmap{margin:0}.inference-note{padding:10px;border-left:3px solid var(--accent-2);background:var(--panel-2);color:var(--muted)}",
      ".damage-profile{padding:16px;border:1px solid var(--line);border-top:3px solid var(--role-dps);border-radius:10px;background:var(--panel);margin:16px 0}",
      ".damage-stack{display:flex;width:100%;height:14px;margin:16px 0 10px;overflow:hidden;border-radius:4px;background:var(--panel-2)}",
      ".damage-stack i.power{background:var(--role-dps)}.damage-stack i.condition{background:var(--purple)}.damage-split{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}",
      ".duel-card{padding:16px;border:1px solid var(--line);border-top:3px solid var(--accent);border-radius:10px;background:var(--panel);margin:16px 0}.duel-card>p{margin:8px 0 14px;color:var(--muted);font-size:12px}",
      ".duel-heading{align-items:center;gap:10px;flex-wrap:wrap}.duel-heading>span:last-child{margin-left:auto}.duel-mode{display:inline-flex;padding:2px;border:1px solid var(--line);border-radius:6px;background:var(--panel-2)}.duel-mode button{border:0;border-radius:4px;padding:5px 8px;background:transparent;color:var(--muted);font:700 10px/1.2 Segoe UI,system-ui,sans-serif;cursor:pointer}.duel-mode button.selected{background:var(--accent);color:var(--on-accent)}",
      ".duel-legend{display:flex;gap:16px;margin-top:8px;color:var(--muted);font-size:12px}.duel-legend span:before{content:'';display:inline-block;width:18px;height:5px;margin-right:6px;border-radius:3px;vertical-align:middle}.duel-legend .ours:before{background:var(--accent)}.duel-legend .enemy:before{background:var(--bad)}",
      ".duel-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.duel-grid h4{margin:0 0 7px;font-size:13px}.duel-row{display:grid;grid-template-columns:120px minmax(90px,1fr) 150px;align-items:center;gap:10px;width:100%;padding:9px 0;border:0;border-bottom:1px solid var(--line-soft);border-radius:0;background:transparent;color:var(--text);font:inherit;text-align:left}.duel-row[tabindex]{cursor:pointer}.duel-row[tabindex]:hover{background:var(--panel-2)}.duel-row>b{font-size:12px}.duel-row small{display:block;color:var(--muted);font-size:9px;font-weight:500}.duel-track{display:grid;grid-template-rows:repeat(2,5px);gap:3px;min-width:0}.duel-fill{display:block;min-width:2px;border-radius:4px}.duel-fill.ours{background:var(--accent)}.duel-fill.enemy{background:var(--bad)}.duel-values{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:11px;white-space:nowrap}.duel-values em{font-style:normal}.duel-normalized{display:none}.duel-card.normalized .duel-total{display:none}.duel-card.normalized .duel-normalized{display:flex}.normalization-note{padding-top:11px;border-top:1px solid var(--line-soft)}.normalization-note b{color:var(--text)}",
      ".comp-compare{padding:16px;border:1px solid var(--line);border-top:3px solid var(--purple);border-radius:10px;background:var(--panel);margin:16px 0}.comp-compare>p{margin:12px 0 0;color:var(--muted);font-size:11px}.comp-size-line{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0}.comp-size-line span{padding:7px 9px;border:1px solid var(--line-soft);border-radius:6px;color:var(--muted);font-size:11px}.comp-size-line b{color:var(--text)}.comp-legend,.comp-row{display:grid;grid-template-columns:minmax(150px,1fr) minmax(120px,1fr) minmax(120px,1fr);gap:12px;align-items:center}.comp-legend{padding:7px 0;color:var(--muted);font-size:10px;text-transform:uppercase}.comp-row{width:100%;padding:7px 0;border:0;border-top:1px solid var(--line-soft);border-radius:0;background:transparent;color:var(--text);font:inherit;text-align:left;cursor:pointer}.comp-row:hover{background:var(--panel-2)}.comp-bar{position:relative;display:flex;align-items:center;justify-content:flex-end;min-width:0;height:18px;background:var(--panel-2);border-radius:4px;overflow:hidden}.comp-bar i{position:absolute;inset:0 auto 0 0;border-radius:4px;background:var(--accent)}.comp-bar.enemy i{background:var(--bad)}.comp-bar b{position:relative;padding:0 5px;font-size:10px}",
      ".damage-segment{min-width:0;padding:11px;border:1px solid var(--line-soft);border-left:4px solid var(--role-dps);border-radius:7px;background:var(--panel-2);color:var(--text);font:inherit;text-align:left;cursor:pointer}",
      ".damage-segment.condition{border-left-color:var(--purple)}.damage-segment span,.damage-segment strong,.damage-segment b{display:block}.damage-segment span{color:var(--muted);font-size:11px}.damage-segment strong{font-size:20px}.damage-segment b{color:var(--accent-2)}",
      ".coverage,.intel-controls,.intel-kpis{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.coverage span{padding:9px 12px;",
      "border:1px solid var(--line);border-radius:999px;color:var(--muted)}.coverage b{color:var(--text)}",
      ".intel-controls label{color:var(--muted);font-size:12px}.intel-kpis{display:grid;grid-template-columns:repeat(3,1fr)}",
      ".wide{margin:12px 0}.chips{display:flex;flex-wrap:wrap;gap:7px}.chips span{padding:6px 9px;background:var(--panel-2);",
      "border:1px solid var(--line-soft);border-radius:999px;color:var(--muted);font-size:12px}.chips b{color:var(--text)}",
      ".scope-note,.muted{color:var(--muted)}.comparison-banner{padding:13px;border-left:4px solid var(--accent-2);",
      ".role-validation-note{padding:12px 14px;border-left:4px solid var(--role-support);background:var(--panel);color:var(--muted)}.role-validation-note b{color:var(--text)}",
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
      ".role-badge{display:flex;align-items:center;justify-content:center;gap:3px;margin-top:5px;padding:2px 3px;border-radius:3px;background:var(--faint);color:#020305;font-size:8px;font-weight:900;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.role-glyph{display:inline-grid;place-items:center;flex:0 0 12px;width:12px;height:12px}.role-glyph svg{display:block;width:12px;height:12px;overflow:visible;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}.role-glyph .filled{fill:currentColor;stroke:none}",
      ".role-dps{background:var(--role-dps)}.role-heal{background:var(--role-heal)}.role-support{background:var(--role-support)}.role-hybrid{background:var(--role-hybrid)}",
      ".evidence-key{display:flex;gap:14px;color:var(--muted);font-size:12px;margin:12px 0}.evidence-key i{display:inline-block;width:8px;height:8px;",
      "border-radius:50%;margin-right:5px;background:var(--good)}.evidence-key i.inferred{background:var(--accent-2)}.evidence-key i.unknown{background:var(--faint)}",
      ".history-callout{display:flex;gap:10px;padding:12px;margin-bottom:14px;border-left:4px solid var(--accent-2);background:var(--panel);color:var(--muted)}.history-callout b{color:var(--text)}",
      ".mvp-section{margin:20px 0 30px}.mvp-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.mvp-card{min-width:0;padding:15px;border:1px solid var(--line);",
      "border-top:3px solid var(--role-support);border-radius:9px;background:var(--panel);color:var(--text);font:inherit;text-align:left;cursor:pointer}.mvp-card:nth-child(3n+1){border-top-color:var(--role-dps)}",
      ".mvp-card:nth-child(3n+2){border-top-color:var(--role-heal)}.mvp-card>span,.mvp-card small{display:block;color:var(--muted);font-size:11px}.mvp-winner{margin:5px 0}.mvp-winner b{font-size:16px;color:var(--text)}",
      ".mvp-card>strong{display:block;color:var(--accent);font-size:23px}.mvp-card p{color:var(--muted);font-size:11px;margin:8px 0 0}",
      ".compare-controls{display:grid;grid-template-columns:repeat(2,minmax(0,1fr)) auto;gap:12px;align-items:end;margin:14px 0 18px;padding:14px;border:1px solid var(--line);border-radius:10px;background:var(--panel)}.compare-controls label{display:grid;gap:5px;color:var(--muted);font-size:11px;font-weight:800}.compare-controls select{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:7px;background:var(--panel-2);color:var(--text);font:inherit}.compare-controls .compare-check{display:flex;align-items:center;gap:7px;padding:9px 0;color:var(--text);white-space:nowrap}.compare-head{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);gap:12px;align-items:center;margin-bottom:12px}.compare-head>div{display:grid;grid-template-columns:42px minmax(0,1fr);column-gap:9px;align-items:center;padding:12px;border:1px solid var(--line);border-radius:9px;background:var(--panel)}.compare-head>div:last-child{text-align:right;grid-template-columns:minmax(0,1fr) 42px}.compare-head>div:last-child .profession-glyph{grid-column:2;grid-row:1/3}.compare-head .profession-glyph{grid-row:1/3;width:38px;height:38px;margin:0}.compare-head b,.compare-head small{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.compare-head small{color:var(--muted);font-size:10px}.compare-head>span{padding:6px 8px;border-radius:999px;font-size:9px;font-weight:900;text-align:center}.same-profession{background:color-mix(in srgb,var(--good) 17%,var(--panel));color:var(--good)}.different-profession{background:var(--panel-2);color:var(--muted)}.compare-metric-group,.compare-player-detail{margin:10px 0;padding:13px;border:1px solid var(--line);border-radius:9px;background:var(--panel)}.compare-metric-group h3,.compare-player-detail h3{margin:0 0 8px;font-size:14px}.compare-skill-basis{min-height:16px;margin:0 0 4px;color:var(--muted);font-size:10px}.compare-stat{display:grid;grid-template-columns:minmax(140px,1fr) minmax(80px,.65fr) minmax(80px,.65fr) minmax(80px,.55fr);gap:9px;align-items:center;padding:7px;border-top:1px solid var(--line-soft)}.compare-stat>span,.compare-stat>small{color:var(--muted);font-size:10px}.compare-stat>b{font-size:12px}.compare-stat>b:nth-child(3){text-align:right}.compare-stat>small{text-align:right}.compare-stat-head{border-top:0}.compare-lead{color:var(--good)}.compare-detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.skill-share{display:grid;width:auto;grid-template-columns:150px minmax(0,1fr);gap:14px;align-items:center;margin:10px 0 14px}.skill-pie{display:grid;place-items:center;width:140px;height:140px;border-radius:50%}.skill-pie>span{display:grid;place-items:center;width:82px;height:82px;border-radius:50%;background:var(--panel);font-size:15px;font-weight:900}.skill-pie small{display:block;color:var(--muted);font-size:8px}.skill-share h4{margin:0 0 7px}.skill-pie-legend{display:grid;gap:4px}.skill-pie-legend>span{display:grid;grid-template-columns:8px minmax(0,1fr) auto;gap:6px;align-items:center;font-size:9px}.skill-pie-legend i{width:8px;height:8px;border-radius:2px}.skill-pie-legend small{color:var(--muted)}.compare-skill-list{display:grid;gap:4px}.compare-skill-list>div{display:flex;justify-content:space-between;gap:8px;padding:6px;border-top:1px solid var(--line-soft);font-size:10px}.compare-skill-list small{display:block;color:var(--muted)}.compare-skill-list b{text-align:right}",
      ".compare-player-card{display:grid;grid-template-columns:42px minmax(0,1fr);column-gap:9px;align-items:center;text-align:left}.compare-head>div.compare-player-card:last-child{grid-template-columns:42px minmax(0,1fr);text-align:left}.compare-head>div.compare-player-card>.profession-glyph,.compare-head>div.compare-player-card:last-child>.profession-glyph{grid-column:1;grid-row:1;width:38px;height:38px}.compare-player-copy{grid-column:2;min-width:0}.compare-player-copy>span,.compare-player-copy>b,.compare-player-copy>small{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.compare-player-copy>span{font-size:11px}.compare-player-copy>small{color:var(--muted);font-size:10px}.compare-stat>.compare-player-a,.compare-stat>.compare-player-b{text-align:right;font-variant-numeric:tabular-nums}.compare-stat>.compare-edge{text-align:right}",
      ".enemy-build-evidence{margin:18px 0}.build-evidence-list{display:grid;gap:6px;margin:7px 0 13px}.build-evidence-list>div{padding:9px;border-left:3px solid var(--accent-2);border-radius:4px;background:var(--panel-2)}.build-evidence-list b,.build-evidence-list span,.build-evidence-list small{display:block}.build-evidence-list span{margin-top:2px;color:var(--accent);font-size:10px;font-weight:800}.build-evidence-list small,.build-evidence-list p{color:var(--muted);font-size:9px}.build-evidence-list p{margin:5px 0 0;line-height:1.4}.evidence-signal{padding:8px;border-left:3px solid var(--good);background:color-mix(in srgb,var(--good) 8%,var(--panel-2));font-size:11px}",
      ".skill-pie-visual{min-width:0}.skill-chart-svg{display:block;width:100%;height:auto;overflow:visible}.skill-chart-slice path{stroke:var(--panel);stroke-width:2;transition:opacity .14s ease,transform .14s ease}.skill-chart-slice line{stroke:var(--muted);stroke-width:1;pointer-events:none}.skill-chart-label{fill:var(--text);font-size:8px;font-weight:800;pointer-events:none}.skill-chart-label tspan+ tspan{fill:var(--muted);font-size:7px}.skill-chart-total{fill:var(--text);font-size:12px;font-weight:900}.skill-chart-total-sub{fill:var(--muted);font-size:7px}.skill-chart-slice[role=button]{cursor:pointer;outline:none}.skill-chart-slice[role=button]:hover path,.skill-chart-slice[role=button]:focus path,.skill-chart-slice.selected path{opacity:.72;stroke:var(--text);stroke-width:4}.open-skill-chart{display:block;width:100%;margin-top:4px;padding:8px;border:1px solid var(--accent);border-radius:6px;background:color-mix(in srgb,var(--accent) 10%,var(--panel));color:var(--accent);font:inherit;font-size:10px;font-weight:900;cursor:pointer}.skill-chart-help{margin:0 0 8px;color:var(--muted);font-size:10px}.skill-pie-legend>button{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px;width:100%;padding:5px;border:1px solid transparent;border-radius:5px;background:transparent;color:var(--text);font:inherit;text-align:left;cursor:pointer}.skill-pie-legend>button:hover,.skill-pie-legend>button:focus{border-color:var(--accent);background:var(--panel-2)}.skill-pie-legend>button small{color:var(--muted)}dialog#skill-chart-dialog{width:min(1120px,calc(100vw - 24px));height:min(820px,calc(100vh - 24px));border:1px solid var(--line);border-top:4px solid var(--accent);border-radius:12px;padding:0;background:var(--panel);color:var(--text);overflow:hidden}dialog#skill-chart-dialog::backdrop{background:rgba(0,0,0,.82)}#skill-chart-dialog .drill-head button{padding:8px 12px;border:1px solid var(--line);border-radius:6px;background:var(--panel-2);color:var(--text);cursor:pointer}.skill-chart-dialog-body{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:16px;height:calc(100% - 76px);padding:16px;overflow:auto}.large-skill-chart{display:grid;grid-template-columns:minmax(0,1fr) 250px;gap:12px;align-items:center}.skill-chart-svg.large{min-height:560px}.skill-chart-svg.large .skill-chart-label{font-size:13px}.skill-chart-svg.large .skill-chart-label tspan+ tspan{font-size:11px}.skill-chart-svg.large .skill-chart-total{font-size:22px}.skill-chart-svg.large .skill-chart-total-sub{font-size:11px}.large-skill-legend{display:grid;gap:6px}.large-skill-legend button{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 8px;padding:10px;border:1px solid var(--line-soft);border-radius:7px;background:var(--panel-2);color:var(--text);font:inherit;text-align:left;cursor:pointer}.large-skill-legend button span,.large-skill-legend button small{color:var(--muted);font-size:10px}.large-skill-legend button small{grid-column:2}.large-skill-legend button.selected{border-color:var(--accent);box-shadow:inset 3px 0 var(--accent)}#skill-chart-selection{align-self:start;padding:16px;border:1px solid var(--line);border-radius:9px;background:var(--panel-2)}#skill-chart-selection h3{font-size:22px;margin:5px 0}#skill-chart-selection>b{color:var(--accent)}#skill-chart-selection p{color:var(--muted);font-size:11px}",
      ".ai-read{margin-top:18px;border-left:4px solid var(--purple)}.ai-read small{color:var(--muted)}",
      "dialog#drilldown{width:min(860px,calc(100vw - 28px));max-height:88vh;border:1px solid var(--line);border-top:4px solid var(--accent);border-radius:12px;padding:0;background:var(--panel);color:var(--text);overflow:hidden}",
      "dialog#drilldown::backdrop{background:rgba(0,0,0,.74)}.drill-head{display:flex;justify-content:space-between;gap:14px;padding:17px 18px;border-bottom:1px solid var(--line)}",
      ".drill-head h2{margin:0;font-size:20px}.drill-head button{border:1px solid var(--line);border-radius:6px;background:var(--panel-2);color:var(--text);cursor:pointer}.drill-body{padding:18px;max-height:calc(88vh - 82px);overflow-y:auto;overflow-x:hidden}",
      ".drill-lead{font-size:14px;color:var(--muted)}.drill-subhead{font-size:13px;margin:18px 0 8px}.drill-kpis,.drill-scoreline{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:14px}.drill-kpis>div,.drill-scoreline>div{padding:11px;border:1px solid var(--line-soft);border-radius:7px;background:var(--panel-2)}.drill-kpis span,.drill-scoreline span,.drill-scoreline small{display:block;color:var(--muted);font-size:10px}.drill-kpis b,.drill-scoreline b{display:block;margin-top:3px;font-size:17px}.drill-scoreline{grid-template-columns:repeat(2,minmax(0,1fr))}.drill-meta{color:var(--muted);font-size:12px}.drill-fights{display:grid;gap:5px}.drill-fight{display:grid;grid-template-columns:minmax(145px,1fr) minmax(120px,1fr) auto;gap:10px;align-items:center;padding:9px;border:1px solid var(--line-soft);border-radius:6px}.drill-fight small{display:block;color:var(--muted);font-size:10px}.drill-fight>span{color:var(--muted);font-size:11px}.drill-fight.metric i{height:7px;background:var(--panel-2);border-radius:3px;overflow:hidden}.drill-fight.metric i em{display:block;height:100%;background:var(--accent)}.drill-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin:12px 0}.drill-metrics>div{display:flex;justify-content:space-between;gap:10px;padding:9px;border-bottom:1px solid var(--line-soft)}.drill-metrics span{color:var(--muted);font-size:11px}.drill-player{display:flex;align-items:center;gap:12px;padding:10px;background:var(--panel-2);border-radius:7px}.drill-player .profession-glyph{width:34px;height:34px;margin:0}.drill-player b,.drill-player small{display:block}.drill-player small{color:var(--muted)}.fight-log-link{display:inline-block;padding:8px 10px;border-radius:6px;background:var(--accent);color:var(--on-accent);text-decoration:none;font-weight:800}.drill-evidence{padding:10px;border-left:3px solid var(--accent-2);background:var(--panel-2);color:var(--muted);margin-top:14px}",
      ".source-callout{display:flex;justify-content:space-between;align-items:center;padding:18px;margin-top:22px;border:1px solid var(--line);",
      "border-radius:10px;background:var(--panel)}.source-callout button{background:var(--accent);color:var(--on-accent)}",
      "@media(max-width:800px){.section-head,.spotlight{display:block}.enemy-scope-heading,.scope-roadmap-head{display:block}.enemy-scope-heading p,.scope-roadmap-head p{margin-top:7px}.scope-roadmap-actions{grid-template-columns:repeat(2,minmax(0,1fr))}.party-grid,.intel-visuals,.duel-grid,.boon-grid,.compare-detail-grid{grid-template-columns:1fr}.mvp-grid{grid-template-columns:repeat(2,1fr)}}",
      "@media(max-width:600px){.session-strip{margin:-18px -12px 18px;flex-wrap:wrap;overflow:hidden}.tabs{position:static}.enemy-skill-table .skill-share,.enemy-skill-table .skill-per-hit,.enemy-skill-table th:nth-child(4),.enemy-skill-table th:nth-child(7),.enemy-skill-table td:nth-child(4),.enemy-skill-table td:nth-child(7){display:none}.enemy-skill-table .skill-name{width:36%}.enemy-skill-table .skill-damage{width:20%}.enemy-skill-table .skill-hits{width:22%}.enemy-skill-table .skill-casts{width:17%}.strip-scoreline{grid-template-columns:1fr}.intel-grid,.comparison-grid,.squad-grid,.damage-split,",
      ".party-grid,.intel-kpis,.mvp-grid{grid-template-columns:1fr}.our-party-row{grid-template-columns:1fr}.our-party-members{grid-template-columns:repeat(5,minmax(0,1fr))}.our-party-player{justify-content:center}.our-party-player span{display:none}.economy-kpis{grid-template-columns:1fr}.economy-uptimes{grid-template-columns:repeat(2,minmax(0,1fr))}.duel-row{grid-template-columns:105px minmax(70px,1fr)}.duel-values{grid-column:1/-1;justify-content:flex-start}.comp-legend,.comp-row{grid-template-columns:minmax(112px,1fr) minmax(70px,1fr) minmax(70px,1fr);gap:6px}.generation-row{grid-template-columns:minmax(120px,1fr) minmax(70px,1fr) auto}.generation-row>small{grid-column:1/-1}.drill-kpis,.drill-scoreline{grid-template-columns:repeat(2,minmax(0,1fr))}.drill-fight,.drill-metrics{grid-template-columns:1fr}.search input,.intel-controls select{min-width:0;width:100%}.metric-row{grid-template-columns:minmax(90px,150px) 1fr auto}.compare-controls,.compare-head{grid-template-columns:1fr}.compare-head>span{justify-self:start}.compare-stat{grid-template-columns:minmax(105px,1fr) repeat(2,minmax(65px,.65fr))}.compare-stat>small{grid-column:1/-1}.skill-share{grid-template-columns:1fr}.skill-pie{margin:auto}}",
      "@media(max-width:600px){.compare-stat-head>.compare-edge{display:none}.compare-stat>.compare-edge{grid-column:1/-1}}",
      "@media(max-width:760px){.skill-share{grid-template-columns:1fr}.skill-chart-dialog-body,.large-skill-chart{grid-template-columns:1fr}.skill-chart-svg.large{min-height:0}.large-skill-legend{grid-template-columns:repeat(2,minmax(0,1fr))}#skill-chart-selection{order:-1}}",
      "@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}"
    ].join("");
    return "<!doctype html><html data-theme=\"" + esc(currentTheme) +
      "\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
      "<title>Sparky Pro report</title><style>" + commonCss + extraCss +
      "</style></head><body><main class=\"wrap\">" + body + "</main></body></html>";
  }
  function drillValue(key, value, units) {
    if (value == null || value === "") return "—";
    if (/fighttime|participation_time|duration/i.test(key) && Number.isFinite(Number(value))) return humanDuration(Number(value));
    var unit=units && units[key];
    return fmt(value) + (unit === "percent" ? "%" : unit ? " " + readableLabel(unit) : "");
  }
  function objectMetricsHtml(object) {
    object=object || {};
    var metrics=object.metrics || object.evidence && object.evidence.metrics || {};
    var units=object.metric_units || {};
    var rows=Object.keys(metrics).filter(function(key){return metrics[key] != null;}).map(function(key){
      return "<div><span>" + esc(readableLabel(key)) + "</span><b>" + esc(drillValue(key,metrics[key],units)) + "</b></div>";
    }).join("");
    return rows ? "<div class=\"drill-metrics\">" + rows + "</div>" : "";
  }
  function fightDrillHtml(fight) {
    if (!fight) return "";
    var reportUrl=fight.report_url || fight.log_url;
    return "<div class=\"drill-kpis\"><div><span>Enemy</span><b>" + fmt(fight.enemy) +
      "</b></div><div><span>Our squad</span><b>" + fmt(fight.squad) + "</b></div><div><span>Enemy downs</span><b>" +
      fmt(fight.downs) + "</b></div><div><span>Enemy kills</span><b>" + fmt(fight.kills) +
      "</b></div><div><span>Our downs</span><b>" + fmt(fight.ally_downs) +
      "</b></div><div><span>Our deaths</span><b>" + fmt(fight.ally_deaths) +
      "</b></div><div><span>K/D</span><b>" + fightKd(fight) +
      "</b></div><div><span>Down share</span><b>" + fightDownShare(fight) + "</b></div></div>" +
      "<div class=\"drill-scoreline\"><div><span>Damage dealt</span><b>" + fmt(fight.damage_out) +
      "</b></div><div><span>Damage received</span><b>" + fmt(fight.damage_in) +
      "</b></div><div><span>Barrier</span><b>" + fmt(fight.barrier_out) + "</b><small>" +
      fmt(fight.barrier_out_pct) + "% of incoming</small></div><div><span>Shielding</span><b>" +
      fmt(fight.shield_out) + "</b><small>" + fmt(fight.shield_out_pct) + "% of outgoing</small></div></div>" +
      "<p class=\"drill-meta\">" + esc(fightClock(fight.time_label) || fight.time_label || "Time unavailable") +
      " · " + esc(fight.duration || "Duration unavailable") + " · " + fmt(fight.allies) + " allied players outside squad</p>" +
      (reportUrl ? "<p><a class=\"fight-log-link\" href=\"" + esc(reportUrl) +
        "\" target=\"_blank\" rel=\"noopener\">Open Fight Log</a></p>" : "");
  }
  function summaryDrillHtml(key) {
    var fights=(model.fights || []).slice(), totals=model.totals || {};
    var map={downs:["Enemy downs","downs",totals.enemy_downs],kills:["Enemy kills","kills",totals.enemy_kills],
      ally_downs:["Our downs","ally_downs",totals.ally_downs],ally_deaths:["Our deaths","ally_deaths",totals.ally_deaths]};
    if (key === "fights") {
      return "<p class=\"drill-lead\">Every modeled encounter in this report, in chronological order.</p><div class=\"drill-fights\">" +
        fights.map(function(fight){return "<div class=\"drill-fight\"><div><b>Fight " + fmt(fight.index) +
          "</b><small>" + esc(fightClock(fight.time_label) || "") + " · " + esc(fight.duration || "") +
          "</small></div><span>K/D " + fightKd(fight) + " · " + fmt(fight.kills) + " kills / " +
          fmt(fight.ally_deaths) + " deaths</span><strong>Down share " + fightDownShare(fight) + " · " +
          fmt(fight.downs) + " enemy downs / " + fmt(fight.ally_downs) + " squad downs</strong></div>";}).join("") + "</div>";
    }
    if (key === "kdr") {
      return "<div class=\"drill-kpis\"><div><span>Enemy kills</span><b>" + fmt(totals.enemy_kills) +
        "</b></div><div><span>Our deaths</span><b>" + fmt(totals.ally_deaths) +
        "</b></div><div><span>Session K/D</span><b>" + fmt(totals.kdr) +
        "</b></div></div><p class=\"drill-lead\">K/D = enemy kills ÷ our deaths. Fight-by-fight scoreline:</p>" +
        "<div class=\"drill-fights\">" + fights.map(function(fight){return "<div class=\"drill-fight\"><div><b>Fight " +
          fmt(fight.index) + "</b><small>" + esc(fightClock(fight.time_label) || "") + "</small></div><span>" +
          "K/D " + fightKd(fight) + " · " + fmt(fight.kills) + " kills / " + fmt(fight.ally_deaths) +
          " deaths</span><strong>Down share " + fightDownShare(fight) + " · " + fmt(fight.downs) +
          " enemy downs / " + fmt(fight.ally_downs) + " squad downs</strong></div>";}).join("") + "</div>";
    }
    var config=map[key]; if (!config) return "";
    var field=config[1],total=Number(config[2] || 0),max=Math.max.apply(null,fights.map(function(f){return Number(f[field]||0);}).concat([1]));
    var ranked=fights.slice().sort(function(a,b){return Number(b[field]||0)-Number(a[field]||0);});
    return "<div class=\"drill-kpis\"><div><span>Session total</span><b>" + fmt(total) +
      "</b></div><div><span>Per fight</span><b>" + fmt(fights.length ? total/fights.length : 0) +
      "</b></div><div><span>Top fight</span><b>Fight " + fmt(ranked[0] && ranked[0].index) + " · " +
      fmt(ranked[0] && ranked[0][field]) + "</b></div></div><h3 class=\"drill-subhead\">All fights · highest first</h3>" +
      "<div class=\"drill-fights\">" + ranked.map(function(fight){var value=Number(fight[field]||0);return "<div class=\"drill-fight metric\"><div><b>Fight " +
        fmt(fight.index) + "</b><small>" + esc(fightClock(fight.time_label) || "") + " · " + fmt(fight.enemy) +
        " enemies</small></div><i><em style=\"width:" + Math.max(value ? 2 : 0,value/max*100).toFixed(1) +
        "%\"></em></i><strong>" + fmt(value) + "</strong></div>";}).join("") + "</div>";
  }
  function playerProfileHtml(title) {
    var requested=String(title || "").split(" · ")[0],matches=[];
    (model.stat_tables || []).forEach(function(board){(board.rows || []).forEach(function(row){
      if (String(row.name || "") === requested) matches.push({board:board,row:row});
    });});
    var poison=(model.poison || []).find(function(row){return String(row.name || "") === requested;});
    var skillSource=model.player_skill_damage || [],skillPlayers=Array.isArray(skillSource) ? skillSource : (skillSource.players || []);
    var skillPlayer=skillPlayers.find(function(row){return String(row.name || "") === requested;});
    var scoreRows=[];(model.high_scores && model.high_scores.blocks || []).forEach(function(block){
      (block.rows || []).forEach(function(row){if(String(row.name || "") === requested) scoreRows.push({caption:block.caption,row:row});});
    });
    if (!matches.length && !poison && !skillPlayer && !scoreRows.length) return "";
    var identity=(matches[0] && matches[0].row) || poison || skillPlayer || scoreRows[0].row;
    function sourceRow(key){var found=matches.find(function(item){return item.board.source_key === key;});return found && found.row;}
    function metricCards(heading,row,items) {
      if (!row) return ""; var metrics=row.metrics || {}, rendered=items.filter(function(item){return metrics[item[0]] != null;});
      if (!rendered.length) return "";
      return "<section class=\"profile-group\"><h3>" + esc(heading) + "</h3><div class=\"drill-metrics\">" + rendered.map(function(item){
        return "<div><span>" + esc(item[1]) + "</span><b>" + esc(drillValue(item[0],metrics[item[0]],row.metric_units || {})) + "</b></div>";
      }).join("") + "</div></section>";
    }
    var damage=sourceRow("Damage"),heal=sourceRow("Heal-Stats"),support=sourceRow("Support-Summary"),
      offense=sourceRow("Offensive-Summary"),uptime=sourceRow("Uptimes");
    var participation=Number(identity.participation_time || identity.fight_time || identity.metrics && identity.metrics.fighttime || 0);
    var fights=identity.fight_count != null ? identity.fight_count : identity.metrics && identity.metrics.numfights;
    var body="<div class=\"player-profile\" data-player-profile><p class=\"drill-lead\"><b>Tonight’s player snapshot</b> · all available selected-night tables for this player.</p>" +
      "<div class=\"drill-player\">" + professionInline(identity.profession || identity.prof || "Unknown") +
      "<div><b>" + esc(requested) + "</b><small>" + esc(identity.account || "Account unavailable") +
      (participation ? " · " + humanDuration(participation) : "") + (fights != null ? " · " + fmt(fights) + " fights" : "") +
      "</small></div></div>";
    body += metricCards("Damage",damage,[["targetdamage","Damage to Enemy Players"],["targetdamageps","DPS"],["targetpower","Power Damage"],["targetpowerps","Power DPS"],["targetcondition","Condition Damage"],["targetconditionps","Condition DPS"],["targetbreakbardamage","Breakbar Damage"]]);
    body += metricCards("Healing & Barrier",heal,[["healing","Healing"],["healingps","Healing / sec"],["barrier","Barrier"],["barrierps","Barrier / sec"],["downedhealing","Downed-Ally Healing"],["downedhealingps","Downed Healing / sec"]]);
    body += metricCards("Support",support,[["condicleanse","Allied Conditions Cleansed"],["condicleanseself","Self-Cleansed Conditions"],["condicleansetime","Condition Duration Removed"],["boonstrips","Enemy Boons Removed"],["boonstripstime","Enemy Boon Duration Removed"],["resurrects","Resurrects"],["resurrecttime","Resurrection Time"]]);
    body += metricCards("Fight Impact",offense,[["downcontribution","Down-Contribution Damage"],["downed","Enemy Downs"],["killed","Enemy Kills"],["againstdowneddamage","Damage to Downed Enemies"],["appliedcrowdcontrol","Crowd Control Applied"],["interrupts","Interrupts"]]);
    body += metricCards("Key Boon Uptime",uptime,[["stability","Stability Uptime"],["protection","Protection Uptime"],["aegis","Aegis Uptime"],["resolution","Resolution Uptime"],["resistance","Resistance Uptime"],["might","Might Uptime"]]);
    if (poison) body += "<section class=\"profile-group\"><h3>Poison Applications</h3><div class=\"drill-metrics\"><div><span>Applications</span><b>" + fmt(poison.apps) + "</b></div><div><span>Applications / min</span><b>" + fmt(poison.apps_per_min) + "</b></div><div><span>Applications / sec</span><b>" + fmt(poison.output) + "</b></div><div><span>Active Fight Time</span><b>" + humanDuration(Number(poison.fight_time || 0)) + "</b></div></div></section>";
    if (skillPlayer) body += "<section class=\"profile-group\"><h3>Damage by Skill</h3>" + skillSharePie(skillPlayer.skills || [],"Damage share by skill") + "<div class=\"drill-fights\">" + (skillPlayer.skills || []).slice(0,10).map(function(skill){return "<div class=\"drill-fight\"><div><b>" + esc(skill.skill) + "</b><small>" + fmt(skill.hits) + " hits · " + fmt(skill.damage_per_hit) + " damage/hit · " + fmt(skill.down_contribution) + " down contribution</small></div><span>" + fmt(skill.percent_of_total) + "% of represented damage</span><strong>" + fmt(skill.damage) + "</strong></div>";}).join("") + "</div></section>";
    if (scoreRows.length) body += "<section class=\"profile-group\"><h3>High Scores from This Night</h3><div class=\"drill-fights\">" + scoreRows.map(function(item){var row=item.row;return "<div class=\"drill-fight\"><div><b>" + esc(item.caption || "High Score") + "</b><small>" + (row.fight != null ? "Fight " + fmt(row.fight) : "Selected night") + ((row.details || []).length ? " · " + esc(row.details.join(" · ")) : "") + "</small></div><span></span><strong>" + fmt(row.score) + "</strong></div>";}).join("") + "</div></section>";
    var detailedPlayer=comparisonPlayers().find(function(player){return player.name===requested;});
    if (detailedPlayer && detailedPlayer.evidence) body += comparisonEvidencePanel(detailedPlayer);
    return body + "</div>";
  }
  function playerDrillHtml(title) { return playerProfileHtml(title); }
  function pressureDrillHtml(title) {
    var pressure=model.enemy_intel && model.enemy_intel.session_pressure || {};
    var label=String(title || "").split(" · ")[0], pools=[pressure.top_damage_skills,pressure.conditions_in,
      pressure.incoming_strips,pressure.control_profile,pressure.cc,pressure.pulls,
      pressure.condition_profile && pressure.condition_profile.normalized];
    var row=null;
    pools.some(function(pool){row=(pool || []).find(function(item){return String(item.skill || item.effect || item.name || "") === label;});return !!row;});
    if (row) return "<div class=\"drill-feature\"><b>" + esc(label) + "</b><p>All exported session fields for this entry:</p></div>" +
      objectMetricsHtml({metrics:row,metric_units:{uptime_percent:"percent",pressure_share_percent:"percent"}});
    if (/Power Damage|Condition Damage/i.test(label)) return objectMetricsHtml({metrics:pressure.damage_profile || {}});
    return "";
  }
  function professionDrillHtml(title) {
    var profession=String(title || "").split(" · ")[0], rows=[];
    (model.enemy_intel && model.enemy_intel.fights || []).forEach(function(fight){
      var found=(fight.professions || []).find(function(row){return row.profession === profession;});
      if (found) rows.push({fight:fight.index,color:fight.color,count:found.count,time:fight.time_label});
    });
    if (!rows.length) return "";
    return "<p class=\"drill-lead\">Observed profession sightings by fight and enemy color.</p><div class=\"drill-fights\">" +
      rows.map(function(row){return "<div class=\"drill-fight\"><div>" + professionInline(profession) +
        "</div><span>Fight " + fmt(row.fight) + " · " + esc(fightClock(row.time) || "") +
      "</span><strong>" + esc(readableLabel(row.color)) + " ×" + fmt(row.count) + "</strong></div>";}).join("") + "</div>";
  }
  function scopedFightIndexes(scopeRef) {
    if (scopeRef === "all") return null;
    if (String(scopeRef).indexOf("fight:") === 0) return [Number(String(scopeRef).slice(6))];
    var id=String(scopeRef).replace(/^scope:/,"").toLowerCase();
    var scope=enemyScopes().find(function(item){return String(item.id || item.color || "").toLowerCase() === id;});
    return scope ? (scope.fight_indexes || []).map(Number) : [];
  }
  function comparisonDrillHtml(ref) {
    var parts=String(ref || "").split("|"),indexes=scopedFightIndexes(parts[0]),metric=parts[1];
    var scopeId=String(parts[0] || "").replace(/^scope:/,"").toLowerCase();
    var selectedScope=String(parts[0] || "").indexOf("scope:") === 0 ? enemyScopes().find(function(item){
      return String(item.id || item.color || "").toLowerCase() === scopeId;
    }) : null;
    var scopeColor=String(selectedScope && selectedScope.color || "").toLowerCase();
    var config={damage:{label:"Damage dealt",ours:"damage_out",enemy:"damage_in",unit:"damage"},
      downs:{label:"Downs secured",ours:"downs",enemy:"ally_downs",unit:"players"},
      kills:{label:"Kills secured",ours:"kills",enemy:"ally_deaths",unit:"players"}}[metric];
    if (!config) return "";
    var fights=(model.fights || []).filter(function(fight){return !indexes || indexes.indexOf(Number(fight.index)) >= 0;});
    var ours=fights.reduce(function(sum,fight){return sum+Number(fight[config.ours] || 0);},0);
    var enemy=fights.reduce(function(sum,fight){return sum+Number(fight[config.enemy] || 0);},0);
    var intelFights=model.enemy_intel && model.enemy_intel.fights || [];
    var enemyByFight={};intelFights.forEach(function(item){
      if (scopeColor && String(item.color || "").toLowerCase() !== scopeColor) return;
      if (!indexes || indexes.indexOf(Number(item.index)) >= 0) enemyByFight[Number(item.index)]=(enemyByFight[Number(item.index)] || 0)+Number(item.enemy_count || 0);
    });
    var denominator=fights.reduce(function(sum,fight){return sum+Number(enemyByFight[Number(fight.index)] || fight.enemy || 0);},0);
    return "<p class=\"drill-lead\"><b>Matched-fight breakdown</b> · " + esc(config.label) +
      " uses the same fights and fields as the comparison chart.</p><div class=\"drill-kpis\"><div><span>Our total</span><b>" +
      fmt(ours) + "</b></div><div><span>Enemy total</span><b>" + fmt(enemy) +
      "</b></div><div><span>Observed enemy player-fights</span><b>" + fmt(denominator) +
      "</b></div></div><div class=\"drill-fights\">" + fights.map(function(fight){
        var oursValue=Number(fight[config.ours] || 0),enemyValue=Number(fight[config.enemy] || 0),enemies=Number(enemyByFight[Number(fight.index)] || fight.enemy || 0);
        return "<div class=\"drill-fight\"><div><b>Fight " + fmt(fight.index) + "</b><small>" +
          esc(fightClock(fight.time_label) || fight.time_label || "") + " · " + fmt(fight.squad) + " ours · " +
          fmt(enemies) + " enemy</small></div><span>Our " + fmt(oursValue) + " (" +
          fmt(enemies ? oursValue/enemies : 0) + "/enemy) · Enemy " + fmt(enemyValue) + " (" +
          fmt(enemies ? enemyValue/enemies : 0) + "/enemy)</span><strong>" + fmt(oursValue) + " vs " +
          fmt(enemyValue) + "</strong></div>";}).join("") + "</div>";
  }
  function compositionProfessionDrillHtml(ref) {
    var parts=String(ref || "").split("|"),indexes=scopedFightIndexes(parts[0]),profession=parts.slice(1).join("|");
    if (!profession) return "";
    var fights=(model.fights || []).filter(function(fight){return !indexes || indexes.indexOf(Number(fight.index)) >= 0;});
    var intel=model.enemy_intel && model.enemy_intel.fights || [],squads=model.squad_composition && model.squad_composition.squads || [];
    var rows=fights.map(function(fight){
      var squad=squads.find(function(item){return Number(item.fight) === Number(fight.index);});
      var ours=(squad && squad.players || []).filter(function(player){return player.profession === profession;}).length;
      var enemyFight=intel.find(function(item){return Number(item.index) === Number(fight.index);});
      var observed=(enemyFight && enemyFight.professions || []).find(function(item){return item.profession === profession;});
      return {fight:fight,ours:ours,enemy:Number(observed && observed.count || 0),color:enemyFight && enemyFight.color};
    });
    var oursTotal=rows.reduce(function(sum,row){return sum+row.ours;},0),enemyTotal=rows.reduce(function(sum,row){return sum+row.enemy;},0);
    return "<p class=\"drill-lead\">" + professionInline(profession) +
      " sightings in every matched fight. These are roster snapshots, not unique-player counts.</p><div class=\"drill-kpis\"><div><span>Our sightings</span><b>" +
      fmt(oursTotal) + "</b></div><div><span>Enemy sightings</span><b>" + fmt(enemyTotal) +
      "</b></div><div><span>Matched fights</span><b>" + fmt(rows.length) + "</b></div></div><div class=\"drill-fights\">" +
      rows.map(function(row){return "<div class=\"drill-fight\"><div><b>Fight " + fmt(row.fight.index) +
        "</b><small>" + esc(fightClock(row.fight.time_label) || "") + " · " + esc(readableLabel(row.color || "Opponent")) +
        "</small></div><span>Our squad " + fmt(row.ours) + " · Enemy " + fmt(row.enemy) +
        "</span><strong>" + fmt(row.ours) + " vs " + fmt(row.enemy) + "</strong></div>";}).join("") + "</div>";
  }
  function richDrillHtml(trigger) {
    var kind=trigger.getAttribute("data-drill-kind") || "", title=trigger.getAttribute("data-drill-title") || "";
    var ref=trigger.getAttribute("data-drill-ref") || "";
    if (kind.indexOf("summary-metric:") === 0) return summaryDrillHtml(kind.split(":")[1]);
    var fightMatch=title.match(/Fight\s+(\d+)/i);
    if ((kind === "fight-row" || kind === "chart-bar") && fightMatch) {
      return fightDrillHtml((model.fights || []).find(function(fight){return Number(fight.index) === Number(fightMatch[1]);}));
    }
    if (kind === "leaderboard-row" || kind === "session-player" || kind === "condition-row") return playerProfileHtml(title);
    if (kind === "chart-bar") return pressureDrillHtml(title) || playerProfileHtml(title);
    if (kind === "night-mvp") {
      var rows=Array.isArray(model.night_mvps) ? model.night_mvps : (model.night_mvps && model.night_mvps.categories || []);
      var mvp=rows.find(function(row){return title.indexOf(String(row.name || "")) >= 0;});
      return mvp ? playerProfileHtml(mvp.name) : "";
    }
    if (kind === "pressure" || kind === "heatmap-cell" || kind === "damage-profile") return pressureDrillHtml(title) || professionDrillHtml(title);
    if (kind === "all-fights-profession") return professionDrillHtml(title);
    if (kind === "comparison-row") return comparisonDrillHtml(ref);
    if (kind === "composition-profession") return compositionProfessionDrillHtml(ref);
    if (kind === "party-slot") return "<p class=\"drill-lead\">" + esc(trigger.getAttribute("data-drill-body") || "") + "</p>";
    return "";
  }
  function selectSkillChartSlice(doc,index) {
    var rows=doc.__skillChartRows || [],total=Number(doc.__skillChartTotal || 0),row=rows[index];
    var valueKey=doc.__skillChartValueKey || "damage",unitLabel=doc.__skillChartUnit || "damage";
    if (!row) return;
    Array.from(doc.querySelectorAll("#skill-chart-dialog [data-skill-slice],#skill-chart-dialog [data-skill-select]")).forEach(function(node){
      node.classList.toggle("selected",Number(node.getAttribute("data-skill-slice") || node.getAttribute("data-skill-select"))===Number(index));
    });
    var value=skillChartValue(row,valueKey),percent=total ? value/total*100 : 0;
    var detail=valueKey === "damage" ? fmt(row.hits || 0)+" connected hits"+(row.down_contribution != null ? " · "+fmt(row.down_contribution)+" down-contribution damage" : "") : "Cast share measures usage frequency, not damage output.";
    doc.getElementById("skill-chart-selection").innerHTML="<span class=\"eyebrow\">Selected slice</span><h3>"+esc(row.skill || "Unknown skill")+"</h3><b>"+fmt(value)+" "+esc(unitLabel)+" · "+percent.toFixed(1)+"%</b><p>"+detail+"</p>";
  }
  function openSkillChart(doc,source,index) {
    var dialog=doc.getElementById("skill-chart-dialog"),host=doc.getElementById("skill-chart-large");
    if (!dialog || !host || !source) return;
    var rows=[];try{rows=JSON.parse(decodeURIComponent(source.getAttribute("data-chart-skills") || ""));}catch(_error){}
    var total=Number(source.getAttribute("data-chart-total") || 0),title=source.getAttribute("data-chart-title") || "Skill share";
    var valueKey=source.getAttribute("data-chart-value-key") || "damage",unitLabel=source.getAttribute("data-chart-unit") || "damage";
    if (!rows.length || !total) return;
    doc.__skillChartRows=rows;doc.__skillChartTotal=total;doc.__skillChartValueKey=valueKey;doc.__skillChartUnit=unitLabel;
    doc.getElementById("skill-chart-title").textContent=title;
    host.innerHTML="<div class=\"large-skill-chart\">"+skillChartSvg(rows,total,true,true,valueKey,unitLabel)+"<div class=\"large-skill-legend\">"+rows.map(function(row,rowIndex){var value=skillChartValue(row,valueKey);return "<button type=\"button\" data-skill-select=\""+rowIndex+"\"><b>"+esc(row.skill || "Unknown skill")+"</b><span>"+fmt(value)+" "+esc(unitLabel)+"</span><small>"+(total ? (value/total*100).toFixed(1) : "0.0")+"%</small></button>";}).join("")+"</div></div>";
    selectSkillChartSlice(doc,Number(index || 0));
    var dialogBody=dialog.querySelector(".skill-chart-dialog-body");
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open","");
    if (dialogBody) dialogBody.scrollTop=0;
  }
  function openDrilldown(doc, trigger) {
    var dialog=doc.getElementById("drilldown");
    if (!dialog || !trigger) return;
    doc.getElementById("drill-title").textContent=trigger.getAttribute("data-drill-title") || "Details";
    var rich=richDrillHtml(trigger);
    doc.getElementById("drill-detail").innerHTML=rich || ("<p class=\"drill-lead\">" +
      esc(trigger.getAttribute("data-drill-body") || "No deeper breakdown was exported.") + "</p>");
    var kind=trigger.getAttribute("data-drill-kind") || "";
    var playerKinds=["leaderboard-row","session-player","condition-row","night-mvp"];
    var playerProfile=!!rich && (playerKinds.indexOf(kind) >= 0 || (kind === "chart-bar" && rich.indexOf("data-player-profile") >= 0));
    var evidenceNode=doc.getElementById("drill-evidence");
    evidenceNode.hidden=playerProfile;
    evidenceNode.textContent=playerProfile ? "" : (trigger.getAttribute("data-drill-evidence") || "Report evidence");
    var drillBody=dialog.querySelector(".drill-body");
    doc.__drillTrigger=trigger;
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
    if (drillBody) drillBody.scrollTop=0;
  }
  function wireInteractive(doc) {
    doc.addEventListener("click", function(event){
      var chartSlice=event.target.closest && event.target.closest("[data-skill-slice]");
      var chartSource=event.target.closest && event.target.closest("[data-skill-chart]");
      if (chartSlice && chartSource) {
        openSkillChart(doc,chartSource,chartSlice.getAttribute("data-skill-slice"));
        return;
      }
      var openChart=event.target.closest && event.target.closest(".open-skill-chart");
      if (openChart) {
        openSkillChart(doc,openChart.closest("[data-skill-chart]"),0);
        return;
      }
      var chartSelect=event.target.closest && event.target.closest("#skill-chart-dialog [data-skill-select],#skill-chart-dialog [data-skill-slice]");
      if (chartSelect) {
        selectSkillChartSlice(doc,chartSelect.getAttribute("data-skill-select") || chartSelect.getAttribute("data-skill-slice"));
        return;
      }
      var modeButton=event.target.closest("[data-duel-mode]");
      if (modeButton) {
        var card=modeButton.closest(".duel-card");
        var normalized=modeButton.getAttribute("data-duel-mode") === "normalized";
        card.classList.toggle("normalized", normalized);
        Array.from(card.querySelectorAll("[data-duel-mode]")).forEach(function(button){
          var selected=button === modeButton;
          button.classList.toggle("selected", selected);
          button.setAttribute("aria-pressed", selected ? "true" : "false");
        });
        return;
      }
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
      if ((event.key === "Enter" || event.key === " ") && event.target.matches("[data-skill-slice]")) {
        event.preventDefault(); event.target.dispatchEvent(new MouseEvent("click",{bubbles:true}));
      }
    });
    var drill=doc.getElementById("drilldown"), drillClose=doc.getElementById("drill-close");
    if (drillClose) drillClose.addEventListener("click", function(){drill.close();});
    if (drill) drill.addEventListener("close", function(){
      if (doc.__drillTrigger && typeof doc.__drillTrigger.focus === "function") doc.__drillTrigger.focus();
    });
    if (drill) drill.addEventListener("click",function(event){if(event.target===drill) drill.close();});
    var skillChartDialog=doc.getElementById("skill-chart-dialog"),skillChartClose=doc.getElementById("skill-chart-close");
    if (skillChartClose) skillChartClose.addEventListener("click",function(){skillChartDialog.close();});
    if (skillChartDialog) skillChartDialog.addEventListener("click",function(event){if(event.target===skillChartDialog) skillChartDialog.close();});
    doc.addEventListener("click", function(event){
      var button=event.target.closest && event.target.closest("[data-expand-board]");
      if (button) {
        var expanded=button.getAttribute("aria-expanded") === "true";
        var board=button.closest(".board"), table=board.querySelector("table");
        var limit=Number(table && table.getAttribute("data-initial-limit") || 5);
        Array.from(board.querySelectorAll("tbody tr")).forEach(function(row,index){
          row.hidden=expanded && index>=limit;
        });
        button.setAttribute("aria-expanded", expanded ? "false" : "true");
        button.textContent=expanded ? "Expand all " + board.querySelectorAll("tbody tr").length : "Collapse";
        return;
      }
      var sortButton=event.target.closest && event.target.closest("[data-sort-key]");
      if (sortButton) {
        var table=sortButton.closest("table"), tbody=table.querySelector("tbody"), key=sortButton.getAttribute("data-sort-key");
        var rows=Array.from(tbody.querySelectorAll("tr"));
        var values=rows.map(function(row){return Number(row.getAttribute("data-"+key)) || 0;});
        var currentlyDescending=values.every(function(value,index){return !index || value <= values[index-1];});
        var heading=sortButton.closest("th"),current=heading && heading.getAttribute("aria-sort");
        var direction=current === "ascending" ? "descending" :
          current === "descending" ? "ascending" : (currentlyDescending ? "ascending" : "descending");
        rows.sort(function(a,b){
          var first=Number(a.getAttribute("data-"+key)) || 0,second=Number(b.getAttribute("data-"+key)) || 0;
          return (first-second)*(direction === "ascending" ? 1 : -1);
        });
        Array.from(table.querySelectorAll("button[data-sort-key]")).forEach(function(button){
          button.removeAttribute("data-sort-direction");
          var th=button.closest("th"); if (th) th.setAttribute("aria-sort","none");
        });
        sortButton.setAttribute("data-sort-direction",direction);
        if (heading) heading.setAttribute("aria-sort",direction);
        var board=table.closest(".board"), expand=board && board.querySelector("[data-expand-board]");
        var expanded=expand && expand.getAttribute("aria-expanded") === "true";
        var limit=Number(table.getAttribute("data-initial-limit") || 5);
        rows.forEach(function(row,index){
          tbody.appendChild(row); row.hidden=!expanded && index>=limit;
          var rank=row.querySelector("[data-rank-cell]"); if (rank) rank.textContent=String(index+1);
        });
        return;
      }
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
    var compareA=doc.getElementById("compare-player-a"),compareB=doc.getElementById("compare-player-b");
    var compareSame=doc.getElementById("compare-same-profession"),compareResults=doc.getElementById("player-comparison-results");
    function refreshPlayerComparison(changed) {
      if (!compareA || !compareB || !compareResults) return;
      var players=comparisonPlayers(),first=players.find(function(player){return player.key===compareA.value;});
      Array.from(compareB.options).forEach(function(option){
        var player=players.find(function(item){return item.key===option.value;});
        option.disabled=option.value===compareA.value || !!(compareSame && compareSame.checked && first && player && player.profession!==first.profession);
      });
      if (!compareB.value || compareB.selectedOptions[0] && compareB.selectedOptions[0].disabled || compareA.value===compareB.value) {
        var replacement=Array.from(compareB.options).find(function(option){return !option.disabled;});
        if (replacement) compareB.value=replacement.value;
      }
      if (changed === compareB && compareA.value===compareB.value) {
        var swap=Array.from(compareA.options).find(function(option){return option.value!==compareB.value;});
        if (swap) compareA.value=swap.value;
      }
      compareResults.innerHTML=renderPlayerComparison(compareA.value,compareB.value);
    }
    if (compareA && compareB) {
      compareA.addEventListener("change",function(){refreshPlayerComparison(compareA);});
      compareB.addEventListener("change",function(){refreshPlayerComparison(compareB);});
      if (compareSame) compareSame.addEventListener("change",function(){refreshPlayerComparison(compareSame);});
      refreshPlayerComparison();
    }
    var colorSelect = doc.getElementById("enemy-color");
    var detailSelect = doc.getElementById("enemy-detail");
    var panel = doc.getElementById("enemy-panel");
    if (colorSelect) {
      colorSelect.setAttribute("aria-hidden", "true");
      colorSelect.setAttribute("tabindex", "-1");
    }
    function syncEnemyScopeButtons() {
      Array.from(doc.querySelectorAll("[data-enemy-scope]")).forEach(function (button) {
        var selected = colorSelect && button.getAttribute("data-enemy-scope") === colorSelect.value;
        button.classList.toggle("selected", selected);
        button.setAttribute("aria-selected", selected ? "true" : "false");
      });
    }
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
          String(fight.enemy_count || "?") + " enemies" +
          (fightClock(fight.time_label) ? " · " + fightClock(fight.time_label) : "");
        detailSelect.appendChild(option);
      });
    }
    function wireScopePanel() {
      if (!panel) return;
      Array.from(panel.querySelectorAll("[data-scope-target]")).forEach(function(button){
        button.addEventListener("click", function(){
          var target=panel.querySelector("[data-scope-section=\"" + button.getAttribute("data-scope-target") + "\"]");
          if (target) target.scrollIntoView({behavior:"smooth",block:"start"});
        });
      });
      var host=panel.querySelector(".scope-mini-parties");
      if (!host) return;
      Array.from(panel.querySelectorAll(".all-fights-comp .party")).forEach(function(party,index){
        var mini=doc.createElement("button"),label=doc.createElement("span");
        mini.type="button"; mini.className="scope-mini-party";
        label.textContent="G" + String(index+1); mini.appendChild(label);
        var names=[];
        Array.from(party.querySelectorAll(".party-slot")).slice(0,5).forEach(function(slot){
          var glyph=slot.querySelector(".profession-glyph"),name=slot.querySelector(".slot-copy b");
          if (glyph) mini.appendChild(glyph.cloneNode(true));
          if (name) names.push(name.textContent);
        });
        mini.title="Subgroup " + String(index+1) + ": " + names.join(", ") + " · open full roles and evidence";
        mini.setAttribute("aria-label",mini.title);
        mini.addEventListener("click",function(){party.scrollIntoView({behavior:"smooth",block:"start"});});
        host.appendChild(mini);
      });
    }
    function drawEnemy() {
      if (!panel || !colorSelect || !detailSelect) return;
      syncEnemyScopeButtons();
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
        panel.innerHTML = fight ? "<div class=\"comparison-banner fight-heading\"><b>Fight " +
          fmt(fight.index) + "</b> · " + fmt(fight.enemy_count) + " enemies" +
          (fightClock(fight.time_label) ? " · " + esc(fightClock(fight.time_label)) : "") +
          " · " + esc(readableLabel(fight.color)) + "</div><div class=\"intel-kpis\"><div><strong>" +
          fmt(fight.enemy_count) + "</strong><span>observed enemy count</span></div>" +
          "<div><strong>" + fmt(fight.observed_profession_count) +
          "</strong><span>identified professions</span></div><div><strong>" +
          fmt(fight.enemy_count ? Math.round(fight.observed_profession_count/fight.enemy_count*100) : 0) +
          "%</strong><span>profession coverage</span></div></div>" +
          compositionComparison(scope, fight) +
          "<article class=\"intel-card wide\"><h3>Observed composition</h3>" +
          chips(fight.professions, "profession", "count", 40) + "</article>" +
          partyGrid(fight) + pressurePanels(scope, fight) :
          "<p class=\"empty\">Fight composition was unavailable.</p>";
      }
      wireScopePanel();
    }
    if (colorSelect && detailSelect && panel) {
      Array.from(doc.querySelectorAll("[data-enemy-scope]")).forEach(function (button) {
        button.addEventListener("click", function () {
          colorSelect.value = button.getAttribute("data-enemy-scope") || "all";
          if (colorSelect.value === "all") {
            detailSelect.innerHTML = "<option value=\"summary\">Choose an opponent above</option>";
          } else {
            fillEnemyDetails(selectedEnemyScope() || {});
          }
          drawEnemy();
        });
      });
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
      if (currentView !== "classic") {
        try { wireInteractive(frame.contentDocument); } catch (_error) {}
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

  async function inflatePayload(elementId) {
    if (typeof DecompressionStream === "undefined") {
      throw new Error("Use a current Chrome, Edge, Firefox, or Safari browser.");
    }
    var payload = document.getElementById(elementId);
    var binary = atob(payload.textContent);
    payload.textContent = "";
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    var stream = new Blob([bytes]).stream()
      .pipeThrough(new DecompressionStream("gzip"));
    var text = await new Response(stream).text();
    binary = "";
    bytes = null;
    return text;
  }

  try {
    var modelJson = await inflatePayload("night-model-payload");
    model = JSON.parse(modelJson);
    modelJson = "";
    classicHtml = await inflatePayload("classic-payload");

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
    embedded_icons = dict(_PROFESSION_ICON_RE.findall(classic_html))
    if embedded_icons:
        night_model = dict(night_model)
        icons = dict(night_model.get("profession_icons") or {})
        for profession, payload in embedded_icons.items():
            icons.setdefault(profession, payload)
        night_model["profession_icons"] = icons
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
    model_payload = base64.b64encode(
        gzip.compress(model_json.encode("utf-8"), 9)
    ).decode("ascii")
    replacements = {
        "__TITLE__": html_escape.escape(title),
        "__SETTINGS__": settings_json,
        "__MODEL_PAYLOAD__": model_payload,
        "__PAYLOAD__": payload,
    }
    return re.sub(
        r"__(?:TITLE|SETTINGS|MODEL_PAYLOAD|PAYLOAD)__",
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


def unpack_night_model(report: str) -> dict[str, Any]:
    """Extract the losslessly compressed model from a switchable report."""
    match = _MODEL_PAYLOAD_RE.search(report)
    if not match:
        raise ValueError("switchable report model payload is missing")
    try:
        model_json = gzip.decompress(
            base64.b64decode(match.group(1), validate=True)
        ).decode("utf-8")
        model = json.loads(model_json)
    except (EOFError, OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("switchable report model payload is corrupt") from exc
    if not isinstance(model, dict):
        raise ValueError("switchable report model payload is not an object")
    return model


def convert_report_file(
    report_path: Path,
    tiddlers: Iterable[dict[str, Any]],
    *,
    default_view: str = DEFAULT_REPORT_VIEW,
    enemy_role_evidence: dict[str, Any] | None = None,
    player_skill_evidence: dict[str, Any] | None = None,
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
        build_night_model(
            list(tiddlers), selected_fights=selected_fights,
            enemy_role_evidence=enemy_role_evidence,
            player_skill_evidence=player_skill_evidence,
        ),
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
