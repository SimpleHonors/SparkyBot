"""Build one offline report with three core views and optional AI commentary.

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
  <button type="button" data-view="wall" hidden>Wall of Fame</button>
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
  function reportInstant(value) {
    var text=String(value || "").trim();
    // EI overview clocks are UTC; explicit offsets in recorded comments win.
    var match=text.match(/^(\d{4}-\d{2}-\d{2})(?:\s+-\s+|[T ])(\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(?:\s*(Z|[+-]\d{2}(?::?\d{2})?))?/);
    if (!match) return null;
    var offset=match[3] || 'Z';
    if (/^[+-]\d{2}$/.test(offset)) offset+=':00';
    var date=new Date(match[1]+'T'+match[2]+offset);
    return Number.isFinite(date.getTime()) ? date : null;
  }
  function reportTimestamp(value, includeDate) {
    var date=reportInstant(value);
    if (!date) return String(value || "");
    var options={timeZone:(model.session || {}).display_timezone || 'America/Chicago',hour:'numeric',minute:'2-digit',hour12:true};
    if (includeDate) {options.month='short';options.day='numeric';options.timeZoneName='short';}
    return new Intl.DateTimeFormat('en-US',options).format(date);
  }
  function fightClock(value) { return reportTimestamp(value,false); }
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
      esc(body) + "\" data-drill-evidence=\"" + "" + "\"" +
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
  function professionColor(profession) {
    var palette = model.profession_colors || {}, name = String(profession || "Unknown").trim().toLowerCase();
    var base = professionBase(name);
    // Read a validated source shade; new known specs inherit their base.
    function sourceColor(key) {
      var color = palette[key];
      return /^#[0-9a-f]{6}$/i.test(color || "") ? color : null;
    }
    // React's bubble chart separates families; Classic's pale elite shades are
    // useful as a small spec tint, not as the dominant color. Keep Ele red.
    var anchors={guardian:"#72C1C1",warrior:"#FFD166",revenant:"#D12705",ranger:"#8EEB2E",thief:"#C08F95",engineer:"#D09C59",elementalist:"#EC5752",mesmer:"#B679D5",necromancer:"#52A76F"};
    var shade=sourceColor(name) || sourceColor(base), anchor=anchors[base];
    if (shade && anchor) return "#"+[1,3,5].map(function(i){return Math.round(parseInt(anchor.slice(i,i+2),16)*.85+parseInt(shade.slice(i,i+2),16)*.15).toString(16).padStart(2,"0");}).join("");
    return shade || (base === "unknown" ? (sourceColor("unknown") || "var(--faint)") : "var(--"+base+")");
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
  function tablePlayer(row) {
    row=row || {};
    return '<span class="table-player" title="'+esc([row.profession || row.prof || "Unknown",row.account || ""].filter(Boolean).join(" · "))+'"><span role="img" aria-label="'+esc(row.profession || row.prof || "Unknown")+'">'+professionGlyph(row.profession || row.prof || "Unknown")+'</span><b>'+esc(row.name || "Player")+'</b></span>';
  }
  function playerBarLabel(row) {
    row=row || {};
    return '<span class="bar-label player-bar-label">'+tablePlayer(row)+'</span>';
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
    var rows = (board.rows || []).filter(function(row){return [row.total,row.value,row.rate,row.participation_weighted_rate,row.value_per_minute,row.per_minute].some(function(v){return finiteMetric(v) != null;});});
    if (!rows.length) return "";
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
    var rateSuffix = metric.rate_unit === "percent" ? "%" : metric.rate_unit === "stacks" ? " stacks" : "";
    if (metric.rate_unit === "stacks") compactRateLabel = "Average stacks";
    function rowRate(row) { return row.rate != null ? row.rate : row.participation_weighted_rate != null ? row.participation_weighted_rate : row.value_per_minute != null ? row.value_per_minute : row.per_minute; }
    rows.sort(function(a,b) {
      var aRate = rowRate(a);
      var bRate = rowRate(b);
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
    var maxTotal=Math.max.apply(null,rows.map(function(r){return Number(r.total!=null?r.total:r.value)||0;}).concat([0]));
    var maxRate=Math.max.apply(null,rows.map(function(r){return Number(rowRate(r))||0;}).concat([0]));
    var selectedMetric = hasRate ? "rate" : "total";
    var switchRateLabel = compactRateLabel === "DPS" || compactRateLabel === "HPS" || compactRateLabel === "Per Sec" ? "Per second" : compactRateLabel === "Per Min" ? "Per minute" : compactRateLabel;
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
        "><td class=\"rank\" data-rank-cell>" + esc(index + 1) +
        "</td><td class=\"player-cell\">" + tablePlayer(row) +
        "</td>" +
        (hasTotal ? '<td class="number" data-ranking-cell data-value-kind="total" title="'+esc(totalLabel+': '+fmt(total))+'"'+(!hasRate?numericBarStyle(total,maxTotal,row.profession):'')+'>'+compactMetric(total)+'</td>' : '') +
        (hasRate ? '<td class="number" data-ranking-cell data-value-kind="rate" title="'+esc(rateLabel+': '+fmt(rate)+rateSuffix)+'"'+numericBarStyle(rate,maxRate,row.profession)+'>'+compactMetric(rate)+rateSuffix+'</td>' : '') +
        (hasPullHits ? "<td class=\"number\" data-label=\"Logged Hits\">" + fmt(row.pull_skill_logged_hit_events) + "</td>" +
          "<td class=\"number\" data-label=\"Connection Rate\">" + fmt(row.pull_skill_connection_rate) + "%</td>" : "") +
        (hasPullCasts ? "<td class=\"number\" data-label=\"Casts\">" + fmt(row.pull_skill_casts) + "</td>" +
          "<td class=\"number\" data-label=\"Connected Hits / Cast\">" + fmt(row.pull_skill_connected_hits_per_cast) + "</td>" : "") +

        (hasParticipation ? "<td class=\"number\" data-label=\"Fight Time\">" + humanDuration(Number(participation)) + "</td>" : "") +
        (hasFights ? "<td class=\"number\" data-label=\""+esc(board.count_label || "Fights")+"\">" + fmt(fights) + "</td>" : "") + "</tr>";
    }).join("");
    return "<article class=\"board ranking-board\"><h3>" + esc(boardLabel) +
      "</h3>" +
      "<div class=\"table-wrap\"><table data-board-table><colgroup>" +
      "<col class=\"col-rank\"><col class=\"col-player\">" +
      (hasTotal ? '<col class="col-total">' : '') + (hasRate ? '<col class="col-rate">' : '') +
      (hasPullHits ? "<col class=\"col-logged\"><col class=\"col-connection\">" : "") +
      (hasPullCasts ? "<col class=\"col-casts\"><col class=\"col-hit-cast\">" : "") +

      (hasParticipation ? "<col class=\"col-time\">" : "") +
      (hasFights ? "<col class=\"col-fights\">" : "") +
      "</colgroup><thead><tr><th>#</th><th>"+esc(board.identity_label || "Player")+"</th>" +
      (hasTotal ? '<th class="number" aria-sort="'+(!hasRate?'descending':'none')+'"><button type="button" data-sort-key="total" title="'+esc(totalLabel)+'">'+esc(isPullBoard?'Hits':'Total')+'</button></th>' : '') +
      (hasRate ? '<th class="number" aria-sort="descending"><button type="button" data-sort-key="rate" title="'+esc(rateLabel)+'">'+esc(compactRateLabel)+'</button></th>' : '') +
      (hasPullHits ? "<th class=\"number\">Logged Hits</th><th class=\"number\" title=\"Connection Rate\">Connect %</th>" : "") +
      (hasPullCasts ? "<th class=\"number\">Casts</th><th class=\"number\" title=\"Connected Hits / Cast\">Hits / Cast</th>" : "") +

      (hasParticipation ? "<th class=\"number\"><button type=\"button\" data-sort-key=\"participation\">Fight Time</button></th>" : "") +
      (hasFights ? "<th class=\"number\"><button type=\"button\" data-sort-key=\"fights\">"+esc(board.count_label || "Fights")+"</button></th>" : "") +
      "</tr></thead><tbody>" + body + "</tbody></table></div>" +
      (rows.length > initialLimit ? "<footer class=\"board-actions\"><button type=\"button\" data-expand-board " +
        "aria-expanded=\"false\">Expand all " + rows.length + "</button></footer>" : "") + "</article>";
  }
  function compactMetric(value) {
    var n=finiteMetric(value);
    if(n == null)return '—';
    var scale=Math.abs(n)>=1000000 ? 1000000 : Math.abs(n)>=10000 ? 1000 : 1;
    return fmt(n/scale)+(scale===1000000?'M':scale===1000?'k':'');
  }
  function numericBarStyle(value,max,profession) {
    if(value==null || value<=0 || !max)return '';
    var width=Math.min(100,value/max*100),color=professionColor(profession);
    return ' style="--comparison-color:'+color+';background-image:linear-gradient(to right,var(--comparison-color) '+width+'%,transparent '+width+'%),linear-gradient(var(--comparison-track),var(--comparison-track));background-repeat:no-repeat;background-size:100% 9px;background-position:left bottom 2px"';
  }
  function totalsCards(compact) {
    var t=model.totals||{};
    function count(value){var n=finiteMetric(value);return n!=null && n>=0 ? n : null;}
    var kills=count(t.enemy_kills),deaths=count(t.ally_deaths),known=kills!=null && deaths!=null;
    var total=known ? kills+deaths : 0,split=total ? kills/total*100 : 0;
    function score(label,value,key,cls){return '<button type="button" class="'+cls+'"'+drillAttrs('summary-metric:'+key,label+' · '+fmt(value),label+': '+fmt(value),'')+'><span>'+label+'</span><strong>'+fmt(value)+'</strong></button>';}
    return '<section class="kill-comparison" aria-label="Combat scoreboard"><div class="combat-duel">'+
      score('Our kills',kills,'kills','combat-score combat-kills')+
      score('K/D',count(t.kdr),'kdr','combat-ratio')+
      score('Our deaths',deaths,'ally_deaths','combat-score combat-deaths')+
      '</div><div class="kill-split" role="img" data-split-state="'+(!known?'missing':total?'populated':'zero')+'" aria-label="Our kills '+fmt(kills)+' · Our deaths '+fmt(deaths)+'"><i style="width:'+split+'%;background:var(--summary-kills)"></i><i style="width:'+(total?100-split:0)+'%;background:var(--summary-deaths)"></i></div><div class="combat-support">'+
      score('Enemy downs',count(t.enemy_downs),'downs','combat-enemy-downs')+
      score('Fights',count(t.fights),'fights','combat-fights')+
      score('Our downs',count(t.ally_downs),'ally_downs','combat-our-downs')+'</div></section>';
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
      "<h2>Category leaders</h2></div></div>" +
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
        var reason="";
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
          "</button>";
      }).join("") + "</div></section>";
  }
  function reportHeading(kicker) {
    var s = model.session || {};
    var title = esc(s.report_title || "Night report");
    var date=s.date;
    var wall=model.sparky_wall||{},comments=(wall.players||[]).flatMap(function(p){return p.comments||[];});
    var posted=comments.map(function(c){return String(c.timestamp||'');}).filter(function(t){return /^\d{4}-\d{2}-\d{2} .* [+-]\d{2}(?::\d{2})?$/.test(t);});
    var dates=Array.from(new Set(posted.map(function(t){return t.slice(0,10);})));
    if(dates.length===1)date=dates[0];
    var bits=[date,s.total_duration ? 'Combat time '+humanDuration(s.total_duration):null].filter(Boolean).map(esc);
    return '<header class="hero"><h1>'+title+'</h1><p>'+bits.join(' · ')+'</p></header>';
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

  function utilityStabilityBoard() {
    var board=tableBySource("Stability-Generation");
    if (!board) return null;
    // Only this mixed utility grid changes display units; preserve source data.
    return Object.assign({},board,{metric:Object.assign({},board.metric,{rate_label:"Stability Generation / min",rate_unit:"per_minute"}),rows:(board.rows || []).map(function(row){return Object.assign({},row,{rate:finiteMetric(row.rate) == null ? null : row.rate*60});})});
  }
  function curatedBoards(group) {
    var groups={damage:[
      derivedDamageBoard("Damage to Enemy Players","targetdamage","targetdamageps","DPS"),
      derivedDamageBoard("Power Damage","targetpower","targetpowerps","Power DPS"),
      derivedDamageBoard("Condition Damage","targetcondition","targetconditionps","Condition DPS"),
      derivedMetricBoard("Down-Contribution Damage","Offensive-Summary","downcontribution",null,"Down Contribution / sec",false)
    ],healing:[
      healMetric("Healing","healing","healingps","Healing / sec"),
      healMetric("Downed-Ally Healing","downedhealing","downedhealingps","Downed Healing / sec")
    ],utility:[
      supportMetric("Condition Cleanses","condicleanse","Cleanses / min"),
      supportMetric("Boons Removed","boonstrips","Boons Removed / min"),
      utilityStabilityBoard(),
      supportMetric("Resurrects","resurrects","Resurrects / min"),
      offensiveMetric("Outgoing Crowd Control","appliedcrowdcontrol","Crowd Control / min")
    ],strips:[
      supportMetric("Boons Removed","boonstrips","Boons Removed / min"),
      supportMetric("Boon Duration Removed (seconds)","boonstripstime","Boon Duration Removed / min (seconds)"),
      supportMetric("Boons Removed from Downed Enemies","boonstripsdowned","Downed-Enemy Boons Removed / min"),
      supportMetric("Downed-Enemy Boon Duration Removed (seconds)","boonstripstimedowned","Downed-Enemy Boon Duration Removed / min (seconds)"),
      offensiveMetric("Outgoing Crowd Control","appliedcrowdcontrol","Crowd Control / min")
    ]};
    return (groups[group] || []).filter(Boolean);
  }
  function metricIdentity(row) {
    // Keep character/profession variants distinct; never join on account alone.
    return JSON.stringify([String(row.account || "").replace(/^:/,""),row.name || "",row.profession || ""]);
  }
  function finiteMetric(value) {
    return value == null || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
  }
  function compareMetricValues(a,b,direction) {
    a=finiteMetric(a);b=finiteMetric(b);
    if (a == null) return b == null ? 0 : 1;
    if (b == null) return -1;
    return (a-b)*(direction === "ascending" ? 1 : -1);
  }
  // Local metric artwork: decorative cues always accompany a readable label.
  function metricCue(label) {
    var kind=/Down-Contribution/.test(label)?'down':/Power/.test(label)?'power':/Condition Damage/.test(label)?'condition':/Damage/.test(label)?'damage':/Cleanses/.test(label)?'cleanse':/Removed/.test(label)?'strip':/Stability/.test(label)?'stability':/Resurrects/.test(label)?'res':/Crowd Control/.test(label)?'cc':'healing';
    var paths={damage:'M4 20L19 5V3h-2L3 17m1-5 8 8M3 21l3-3',power:'M13 2L5 13h6l-1 9 9-13h-6z',condition:'M12 2s-7 8-7 13a7 7 0 0014 0c0-5-7-13-7-13zm-3 13a3 3 0 003 3',down:'M12 3v13m-5-5 5 5 5-5M5 21h14',cleanse:'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5zM19 3v4m-2-2h4',strip:'M12 3l8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6zM3 21L21 3',stability:'M12 3l8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6zM8 12l3 3 5-6',res:'M12 20V5m-5 5 5-5 5 5M5 19v3h14v-3',cc:'M5 9V6a2 2 0 014 0v5-7a2 2 0 014 0v7-6a2 2 0 014 0v7-3a2 2 0 014 0v7l-4 6H9l-6-8a2 2 0 012-3l4 4',healing:'M9 3h6v6h6v6h-6v6H9v-6H3V9h6z'};
    return {kind:kind,icon:'<svg class="metric-cue-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="'+paths[kind]+'"/></svg>'};
  }
  function metricGrid(group) {
    var compactLabels={"Damage to Enemy Players":["Damage","DPS"],"Power Damage":["Power","Power DPS"],"Condition Damage":["Condition","Condi DPS"],"Down-Contribution Damage":["Down contrib.","Down dmg/sec"],"Healing":["Healing","HPS"],"Downed-Ally Healing":["Downed healing","Downed HPS"],"Condition Cleanses":["Cleanses","Cleanses/min"],"Boons Removed":["Strips","Strips/min"],"Boon Duration Removed (seconds)":["Duration removed (s)","Removed seconds/min"],"Boons Removed from Downed Enemies":["Downed strips","Downed strips/min"],"Downed-Enemy Boon Duration Removed (seconds)":["Downed duration (s)","Downed seconds/min"],"Stability Generation":["Stability","Stab/min"],"Resurrects":["Resurrects","Res/min"],"Outgoing Crowd Control":["Crowd control","CC/min"]};
    var boards=curatedBoards(group).filter(function(board){return (board.rows || []).some(function(row){return finiteMetric(row.total) != null || finiteMetric(row.rate) != null;});}),players=new Map();
    boards.forEach(function(board,index){(board.rows || []).forEach(function(row){
      var key=metricIdentity(row),item=players.get(key) || {row:row,values:[]};
      item.values[index]=row;players.set(key,item);
    });});
    var rows=Array.from(players.values()).sort(function(a,b){return compareMetricValues(
      a.values[0] && a.values[0].rate,b.values[0] && b.values[0].rate,"descending");});
    if (!rows.length) return "";
    var title={damage:"Damage & down contribution",healing:"Healing & downed allies",utility:"Utility & support",strips:"Boon removal & control summary"}[group];
    var primaryMax=Math.max.apply(null,rows.map(function(item){return finiteMetric(item.values[0] && item.values[0].rate) || 0;}));
    var columns='<colgroup><col class="grid-player-col">'+boards.map(function(board,index){var label=board.metric && board.metric.label || board.stat,totalWidth=group==='utility' ? ({'Condition Cleanses':80,'Stability Generation':90}[label] || 60) : ({'Down-Contribution Damage':96,'Downed-Ally Healing':96}[label] || 82);return '<col class="grid-total-col" style="width:'+totalWidth+'px!important"><col class="grid-rate-col'+(index===0?' grid-primary-col':'')+'">';}).join('')+'<col class="grid-time-col"></colgroup>';
    var heads=boards.map(function(board,index){var label=board.metric && board.metric.label || board.stat;
      var rateLabel=board.metric && board.metric.rate_label || "Rate";
      var names=compactLabels[label] || compactLabels[board.stat] || [label,rateLabel];
      var shortLabels={"Damage to Enemy Players":["Total","DPS"],"Power Damage":["Power","DPS"],"Condition Damage":["Condi","DPS"],"Down-Contribution Damage":["Down dmg","/s"],"Healing":["Total","HPS"],"Downed-Ally Healing":["Down heal","HPS"],"Condition Cleanses":["Cleanses","/min"],"Boons Removed":["Strips","/min"],"Boon Duration Removed (seconds)":["Removed s","s/min"],"Boons Removed from Downed Enemies":["Down strips","/min"],"Downed-Enemy Boon Duration Removed (seconds)":["Down s","s/min"],"Stability Generation":["Stability","/min"],"Resurrects":["Res","/min"],"Outgoing Crowd Control":["CC","/min"]};
      names=shortLabels[label] || shortLabels[board.stat] || names;
      var cue=metricCue(label),cueNames={'Damage to Enemy Players':'Damage','Healing':'Healing','Condition Cleanses':'Cleanse'};
      names=[cueNames[label] || names[0],names[1]];
      return ['total','rate'].map(function(kind){
        var selected=index===0 && kind==='rate',name=names[kind==='total'?0:1];
        return '<th scope="col" class="pair-'+kind+' metric-cue-'+cue.kind+'" aria-sort="'+(selected?'descending':'none')+'"><button type="button"'+(selected?' data-sort-direction="descending"':'')+' data-sort-key="m'+index+'-'+kind+'" data-detail-label="'+esc((compactLabels[label] || compactLabels[board.stat] || [label,rateLabel])[kind==='total'?0:1])+'" aria-label="'+esc(kind==='total'?label+' · Total':rateLabel)+'" title="'+esc(kind==='total'?label+' · Total':rateLabel)+'"><span class="metric-cue-label">'+(kind==='total'?cue.icon:'')+esc(name)+'</span></button></th>';
      }).join('');
    }).join("");
    return "<article class=\"board metric-grid-board\"><h3>"+esc(title)+"</h3>"+"<div class=\"metric-grid-scroll\" tabindex=\"0\" role=\"region\" aria-label=\""+esc(title)+" sortable table\"><table class=\"metric-grid metric-grid-compact\" data-metric-grid=\""+group+"\" data-initial-limit=\"5\" style=\"--grid-min-width:"+Math.max(925,315+boards.length*122)+"px\">"+columns+"<thead><tr><th scope=\"col\">Player</th>"+heads+"<th scope=\"col\"><button type=\"button\" data-sort-key=\"participation\" title=\"Fight time\">Time</button></th></tr></thead><tbody>"+
      rows.map(function(item,index){var attrs="",cells=boards.map(function(board,i){var row=item.values[i] || {},total=finiteMetric(row.total),rate=finiteMetric(row.rate);
        attrs+=" data-m"+i+"-total=\""+(total == null ? "" : total)+"\" data-m"+i+"-rate=\""+(rate == null ? "" : rate)+"\"";
        var label=board.metric && board.metric.rate_label || "Rate",tip=(board.stat || "Metric")+" total: "+(total == null ? "unavailable" : fmt(total))+"; "+label+": "+(rate == null ? "unavailable" : fmt(rate));
        return ['total','rate'].map(function(kind){var value=kind==='total'?total:rate;
          var primary=i===0 && kind==='rate';
          return '<td class="number pair-'+kind+(primary?' grid-primary-value':'')+'"'+(primary?numericBarStyle(value,primaryMax,item.row.profession):'')+' data-metric-cell="'+i+'" data-value-kind="'+kind+'" tabindex="0" title="'+esc(tip)+'" aria-label="'+esc(tip)+'">'+compactMetric(value)+'</td>';
        }).join('');
      }).join("");
      var times=item.values.map(function(row){return finiteMetric(row.participation_time);}).filter(function(v){return v != null;});
      var seconds=times.length ? Math.max.apply(null,times) : null, varied=times.some(function(t){return t !== seconds;});
      return "<tr"+attrs+" data-participation=\""+(seconds == null ? "" : seconds)+"\""+(index>=5 ? " class=\"board-extra\" hidden" : "")+"><th scope=\"row\">"+tablePlayer(item.row)+"</th>"+cells+"<td class=\"number\" title=\"Fight time\">"+(seconds == null ? "—" : humanDuration(seconds))+"</td></tr>";
      }).join("")+"</tbody></table></div>"+(rows.length>5 ? "<footer class=\"board-actions\"><button type=\"button\" data-expand-board aria-expanded=\"false\">Expand all "+rows.length+"</button></footer>" : "")+"</article>";
  }
  var refreshCss = ".metric-grid-scroll{overflow:auto;max-width:100%}.metric-grid{min-width:900px;table-layout:auto}.metric-grid th{white-space:normal;text-transform:none;min-width:110px}.metric-grid th:first-child{min-width:170px;position:sticky;left:0;background:var(--panel);z-index:1}.metric-grid th small{display:block;color:var(--muted);font-weight:400}.metric-grid td b{font-weight:600}.metric-grid td small{color:var(--muted)}.metric-grid button{min-height:28px}.metric-grid th[aria-sort] button:after{content:none}.metric-grid button[data-sort-direction=ascending]:after{content:' ↑'}.metric-grid button[data-sort-direction=descending]:after{content:' ↓'}.metric-grid-board{margin:20px 0}.secondary-support{margin:24px 0}.secondary-support summary{cursor:pointer;padding:14px;color:var(--muted)}";
  refreshCss += ".boon-generation,.boon-uptimes{margin:26px 0}.boon-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:16px}.boon-card,.generation-card{padding:18px;border:1px solid var(--line);border-radius:10px;background:var(--panel);margin:12px 0}.boon-card .chart-title,.generation-card .chart-title{display:flex;flex-direction:column;gap:4px;margin-bottom:14px}.chart-title span,.boon-stat-row small{color:var(--muted);font-size:12px}.boon-stat-row{display:grid;grid-template-columns:minmax(120px,1fr) minmax(140px,2fr);align-items:center;gap:14px;padding:10px 0}.boon-stat-row small{display:block}.sparky-boon-bar{position:relative;display:flex;align-items:center;gap:12px;min-height:30px;font-variant-numeric:tabular-nums}.sparky-boon-bar>b{font-size:12px;min-width:70px;text-align:right}.boon-track{flex:1;display:block;height:14px;border-radius:3px;background:var(--panel-2);overflow:hidden}.boon-track i{height:100%;display:block;background:var(--boon-color);border-radius:3px}.chart-tooltip{display:none;position:absolute;z-index:20;bottom:100%;right:0;max-width:440px;padding:10px;background:var(--surface);color:var(--text);border:1px solid var(--line);box-shadow:0 4px 18px #0004;font-size:12px;white-space:normal}.sparky-boon-bar:hover .chart-tooltip,.sparky-boon-bar:focus .chart-tooltip{display:block}@media(max-width:620px){.boon-stat-row{grid-template-columns:1fr;gap:4px}.generation-card .sparky-boon-bar>b{min-width:130px}}";
  refreshCss += ".bubble-card{padding:20px;margin:20px 0;border:1px solid var(--line);border-radius:10px;background:var(--panel)}.bubble-card h3{margin:0}.bubble-card p{color:var(--muted);font-size:12px}.bubble-scroll{overflow:auto}.bubble-scroll svg{display:block;width:100%;min-width:560px;max-height:480px}.bubble-scroll text{fill:var(--muted);font:12px Segoe UI,sans-serif}.bubble-gridline{stroke:var(--line);stroke-width:1;fill:none}.bubble-scroll circle{stroke:var(--point-color);stroke-width:1.5;fill-opacity:1;cursor:pointer}.bubble-scroll circle:hover,.bubble-scroll circle:focus{stroke:var(--text);stroke-width:3;fill-opacity:1}.bubble-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px}.bubble-legend span:before{content:'';display:inline-block;width:9px;height:9px;margin-right:5px;border-radius:50%;background:var(--point-color)}";
  refreshCss += ".multiboon-chart{margin:24px 0}.multiboon-chart p{font-size:12px;color:var(--muted)}.multiboon-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;margin:16px 0}.multiboon-legend span{display:flex;align-items:center;gap:5px}.multiboon-legend i{width:10px;height:10px;border-radius:2px}.multiboon-row{display:grid;grid-template-columns:minmax(110px,190px) minmax(80px,1fr) 60px;gap:10px;align-items:center;margin:9px 0;font-size:12px}.multiboon-name small{display:block;color:var(--muted)}.multiboon-bar{position:relative;min-width:0;padding:6px 0;outline-offset:3px}.multiboon-track{display:flex;width:100%;height:18px;background:var(--panel-2);border-radius:3px;overflow:hidden}.multiboon-track i{height:100%;flex-shrink:0}.multiboon-bar .chart-tooltip{display:none;position:absolute;left:0;bottom:100%;z-index:20;white-space:pre-line;max-width:min(440px,70vw);width:max-content;padding:12px;border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:7px;box-shadow:0 6px 24px #0005;pointer-events:none}.multiboon-bar:hover .chart-tooltip,.multiboon-bar:focus .chart-tooltip{display:block}.multiboon-row:first-child .chart-tooltip{bottom:auto;top:100%}@media(max-width:600px){.multiboon-row{grid-template-columns:110px minmax(60px,1fr) 42px;gap:5px}.multiboon-chart{padding:10px}}";
  refreshCss += ".metric-grid-compact{min-width:760px}.metric-grid-compact th{white-space:nowrap;min-width:90px}.metric-grid-compact td{font-size:14px}.metric-mode{display:flex;justify-content:flex-end;gap:0;margin:10px 0}.metric-mode button{padding:6px 15px;border:1px solid var(--line);background:var(--panel);color:var(--muted);cursor:pointer}.metric-mode button:first-child{border-radius:6px 0 0 6px}.metric-mode button:last-child{border-radius:0 6px 6px 0}.metric-mode button[aria-pressed=true]{background:var(--accent);color:var(--on-accent);border-color:var(--accent)}.comparison-head,.bubble-comparison-row{display:grid;grid-template-columns:minmax(180px,1.1fr) minmax(160px,1fr) minmax(160px,1fr) 150px;gap:24px;align-items:center}.comparison-head{padding:12px;color:var(--muted);font-size:12px;border-bottom:1px solid var(--line)}.comparison-head small{display:block;font-size:11px;color:var(--faint);margin-top:5px}.bubble-comparison-row{position:relative;width:100%;border:0;border-bottom:1px solid var(--line-soft);background:transparent;color:var(--text);font:inherit;text-align:left;padding:12px;cursor:pointer}.bubble-comparison-row:hover,.bubble-comparison-row:focus{background:var(--panel-2)}.bubble-identity .profession-glyph{flex:0 0 26px;width:26px;height:26px;margin:0}.bubble-identity{display:flex;gap:10px;align-items:center;min-width:0}.bubble-player-name{display:block;font-size:13px;overflow-wrap:anywhere}.bubble-identity small{display:block;font-size:11px;color:var(--muted)}.comparison-value{display:flex;align-items:center;gap:10px}.comparison-value b{min-width:58px;text-align:right;font-size:12px;font-variant-numeric:tabular-nums}.comparison-track{display:block;flex:1;height:8px;background:var(--panel-2);border-radius:2px}.comparison-track i{height:100%;display:block;background:var(--point-color);border-radius:2px}.comparison-size{display:flex;align-items:center;gap:10px;font-size:12px;font-variant-numeric:tabular-nums}.comparison-size svg{width:32px;height:32px;flex:0 0 32px}.bubble-comparison-row:hover .chart-tooltip,.bubble-comparison-row:focus .chart-tooltip{display:block;pointer-events:none}.bubble-zero{margin:16px 0;padding:12px;border:1px solid var(--line);border-radius:6px;color:var(--muted);font-size:12px}.bubble-zero summary{cursor:pointer}.bubble-zero ul{columns:2;padding-left:18px}.bubble-zero li{padding:5px 0}.comparison-scroll{overflow:visible}.comparison-rows{max-height:570px;overflow-y:auto;scrollbar-gutter:stable}.comparison-head{scrollbar-gutter:stable;overflow-y:auto}@media(max-width:760px){.bubble-card{padding:14px}.comparison-head{display:none}.comparison-rows{max-height:none;overflow:visible}.bubble-comparison-row{grid-template-columns:1fr 1fr;gap:10px 16px;padding:14px 0}.bubble-identity{grid-column:1/-1}.comparison-value{display:grid;grid-template-columns:1fr auto;gap:5px}.comparison-value:before{content:attr(data-label);grid-column:1/-1;color:var(--muted);font-size:11px}.comparison-value b{display:block;grid-column:1/-1;text-align:left;font-size:14px}.comparison-track{grid-column:1/-1;width:100%}.comparison-size{grid-column:1/-1}.comparison-size:after{content:attr(data-label);color:var(--muted);font-size:11px}.bubble-zero ul{columns:1}}";
  refreshCss += ".bubble-name-key{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:5px;margin:12px 0}.bubble-name-key button{font:inherit;font-size:12px;text-align:left;padding:6px;border:1px solid var(--line);border-radius:4px;background:var(--panel);color:var(--text);cursor:pointer}.bubble-name-key button{display:flex;align-items:center;gap:6px}.bubble-name-key button:before,.bubble-identity:before{content:'';display:block;flex:0 0 9px;width:9px;height:9px;border-radius:50%;background:var(--point-color)}.bubble-name-key .profession-glyph{flex:0 0 24px;width:24px;height:24px;margin:0}.bubble-name-key small{display:block;color:var(--muted)}.bubble-number{font-size:10px!important;pointer-events:none}.bubble-scatter.has-highlight circle:not(.point-highlight){opacity:.12}.bubble-scatter circle.point-highlight{stroke:var(--text);stroke-width:4;fill-opacity:1}.bubble-companion summary{cursor:pointer;padding:10px}";
  refreshCss += "table th.number,table th.numeric-header,table th.number button,table th.numeric-header button,.metric-grid thead th:not(:first-child),.metric-grid thead th:not(:first-child) button{text-align:right}.metric-grid thead th:first-child,.metric-grid tbody th{text-align:left}.metric-grid{table-layout:fixed;width:100%}.metric-grid thead button{display:block;width:100%;padding-left:0;padding-right:0;position:relative;white-space:normal}.metric-grid thead button:after{position:absolute;right:-12px}.damage-composition-row strong{min-width:0;max-width:100%;white-space:normal;overflow-wrap:anywhere;text-align:right}@media(max-width:620px){.damage-composition-row{grid-template-columns:minmax(0,1fr) 78px!important;gap:8px}.damage-composition-row .damage-player{grid-column:1/-1}.damage-composition-row .damage-player>span:last-child{flex:1}.damage-composition-row strong{font-size:11px}}";
  refreshCss += ".chart-tooltip{display:none!important}#chart-popup{position:fixed;z-index:2147483647;pointer-events:none;max-width:min(290px,calc(100vw - 16px));box-sizing:border-box;padding:9px 12px;border:1px solid var(--line);border-radius:7px;background:var(--panel);color:var(--text);font:12px/1.5 Segoe UI,sans-serif;box-shadow:0 6px 20px #0005;overflow-wrap:anywhere}#chart-popup[hidden]{display:none}.multiboon-track i:focus{outline:2px solid var(--text);outline-offset:-2px}";
  refreshCss += "table,.comparison-head{--table-head:color-mix(in srgb,var(--panel) 92%,var(--text) 8%);--table-selected:color-mix(in srgb,var(--panel) 88%,var(--text) 12%);--table-hover:color-mix(in srgb,var(--panel) 84%,var(--text) 16%)}thead th,.metric-grid thead th:first-child{background:var(--table-head)!important;color:var(--text)!important;border-bottom:2px solid var(--line)}thead th[aria-sort=ascending],thead th[aria-sort=descending]{background:var(--table-selected)!important}thead th:has(button[data-sort-key]):is(:hover,:focus-within){background:var(--table-hover)!important}.metric-grid tbody th{color:var(--text)}.metric-grid tbody tr:is(:hover,:focus-within)>*,.ranking-board tbody tr:is(:hover,:focus-within)>*{background-color:var(--panel-2)}.metric-grid tbody tr[hidden]{display:none!important}thead button[data-sort-key]{min-height:34px;padding:4px 22px 4px 0!important;position:relative;white-space:nowrap}thead button[data-sort-key]:after,.metric-grid thead th button[data-sort-key]:after{content:'↕';position:absolute;right:0;top:50%;transform:translateY(-50%);font-size:17px;line-height:1;color:var(--text);opacity:1}thead th[aria-sort=ascending] button[data-sort-key]:after,.metric-grid thead th[aria-sort=ascending] button[data-sort-key]:after{content:'↑'}thead th[aria-sort=descending] button[data-sort-key]:after,.metric-grid thead th[aria-sort=descending] button[data-sort-key]:after{content:'↓'}thead button[data-sort-key]:focus-visible{outline:2px solid var(--text);outline-offset:2px}.table-player{display:inline-flex;align-items:center;gap:7px;white-space:nowrap;text-transform:none;letter-spacing:normal}.table-player b{font-size:14px;line-height:24px;white-space:nowrap;overflow:visible}.table-player .profession-glyph{display:inline-grid;flex:0 0 24px;width:24px;height:24px;margin:0}.metric-grid thead th:first-child,.metric-grid tbody th{width:250px;min-width:250px}.metric-grid tbody th{padding-top:5px;padding-bottom:5px}.metric-grid{min-width:960px}.metric-grid tbody td{padding-top:5px;padding-bottom:5px}";
  refreshCss += ".table-player>span{display:inline-flex;flex-shrink:0}.table-wrap{overflow-x:auto!important}.table-wrap table{min-width:850px}.table-wrap td:has(.table-player){white-space:nowrap;width:280px}.score-context{color:var(--muted);font-size:12px;margin-left:8px}.bubble-name-key{grid-template-columns:repeat(auto-fit,minmax(265px,1fr))}.bubble-name-key button{white-space:nowrap;font-size:14px}.comparison-scroll{overflow-x:auto}.comparison-head,.bubble-comparison-row{min-width:940px;grid-template-columns:minmax(280px,1.3fr) minmax(170px,1fr) minmax(170px,1fr) 150px}.comparison-head{background:var(--table-head);color:var(--text)}.bubble-comparison-row{padding-top:6px;padding-bottom:6px}.multiboon-chart{overflow-x:auto}.multiboon-row{min-width:720px;grid-template-columns:270px minmax(200px,1fr) 60px}.boon-stat-row{grid-template-columns:minmax(260px,1fr) minmax(140px,2fr)}.generation-card,.boon-card{overflow-x:auto}.col-player{width:280px}.poison-player{width:280px}@media(max-width:760px){.table-wrap table{display:table!important;min-width:850px!important}.table-wrap table colgroup{display:table-column-group!important}.table-wrap table thead{display:table-header-group!important}.table-wrap table tbody{display:table-row-group!important}.table-wrap table tr{display:table-row!important}.table-wrap table td,.table-wrap table th{display:table-cell!important;padding:7px 11px!important}.table-wrap table td:before{display:none!important}.comparison-head{display:grid;overflow:visible}.comparison-rows{overflow:visible}.bubble-comparison-row{display:grid;gap:24px}.bubble-identity,.comparison-size,.comparison-value,.comparison-value b,.comparison-track{grid-column:auto}.comparison-value{display:flex}.comparison-value:before,.comparison-size:after{display:none}.comparison-value b{text-align:right}.comparison-track{width:auto}}";
  refreshCss += ".compare-player-card,.compare-head>div.compare-player-card:last-child{display:flex;min-width:0;overflow-x:auto}.bar-card{overflow-x:auto}.metric-row:has(.table-player){min-width:650px;grid-template-columns:270px minmax(150px,1fr) auto}.player-bar-label{overflow:visible}.strip-contributors{grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}.damage-player .table-player b{overflow:visible;text-overflow:clip}.table-player .profession-glyph{width:24px;height:24px}.boon-stat-row:has(.table-player){min-width:520px}.compare-player-card .table-player b{font-size:15px}";
  refreshCss += ".kill-comparison{--summary-kills:color-mix(in srgb,#66869f 75%,var(--panel));--summary-deaths:color-mix(in srgb,#a16d7b 75%,var(--panel))}.boards{grid-template-columns:minmax(0,1fr)!important}.ranking-board{grid-column:1/-1;min-width:0}.ranking-board .metric-mode{padding:0 12px}.ranking-board tr[hidden]{display:none!important}.ranking-board table[data-board-table]{min-width:640px}.ranking-board .table-wrap table[data-board-table]{min-width:640px!important}.ranking-board .col-player{width:40%}.ranking-board .col-total{width:28%}";
  refreshCss += ".kill-comparison{--summary-kills:#70c5dc;--summary-deaths:#e79c91;margin:20px 0 28px;padding:18px 28px 8px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}.kill-comparison button{background:none;border:0;color:var(--text);font:inherit;cursor:pointer;padding:0;min-height:44px;font-variant-numeric:tabular-nums}.kill-comparison button:focus-visible{outline:2px solid var(--text);outline-offset:4px;border-radius:3px}.kill-comparison button:hover strong{text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:5px}.combat-duel{display:grid;grid-template-columns:minmax(0,1fr) 132px minmax(0,1fr);align-items:center;gap:28px}.kill-comparison .combat-score{display:flex;align-items:center;justify-content:center;gap:24px}.combat-score span{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}.combat-score strong{font-size:56px;line-height:1.1;letter-spacing:-.045em;font-weight:750}.combat-deaths span{order:2}.kill-comparison .combat-ratio{display:flex;flex-direction:column-reverse;align-items:center;justify-content:center;position:relative;padding:13px 18px;isolation:isolate}.combat-ratio:before{content:'';position:absolute;inset:0;background:var(--panel-2);border:1px solid var(--line);clip-path:polygon(12px 0,calc(100% - 12px) 0,100% 12px,100% calc(100% - 12px),calc(100% - 12px) 100%,12px 100%,0 calc(100% - 12px),0 12px);z-index:-1}.combat-ratio strong{font-size:34px;line-height:1.2;letter-spacing:-.035em}.combat-ratio span{font-size:11px;letter-spacing:.15em;color:var(--muted);margin-top:3px}.kill-split{display:flex;height:5px;overflow:hidden;margin:16px 0 1px;background:var(--line)}.kill-split i{display:block;height:100%;flex-shrink:0}.kill-split i:first-child{box-shadow:inset -2px 0 var(--panel)}.combat-support{display:grid;grid-template-columns:minmax(0,1fr) 132px minmax(0,1fr);gap:28px;align-items:center}.combat-support button{display:flex;align-items:center;gap:9px;font-size:13px}.combat-support span{font-size:12px;color:var(--muted)}.combat-support strong{font-weight:650}.combat-enemy-downs{justify-content:flex-start}.combat-fights{justify-content:center}.combat-our-downs{justify-content:flex-end}@media(max-width:700px){.kill-comparison{padding:16px 16px 8px}.combat-duel,.combat-support{grid-template-columns:minmax(0,1fr) 92px minmax(0,1fr);gap:10px}.kill-comparison .combat-score{flex-direction:column;gap:8px}.combat-deaths span{order:0}.combat-score strong{font-size:42px}.combat-score span{font-size:10px;letter-spacing:.07em;white-space:nowrap}.kill-comparison .combat-ratio{padding:13px 8px}.combat-ratio strong{font-size:28px}.combat-support{gap:8px;grid-template-columns:minmax(0,1fr) 62px minmax(0,1fr)}.combat-support button{gap:4px;font-size:13px;flex-wrap:wrap;align-content:center;line-height:1.4}.combat-support span{font-size:10px}.kill-split{margin-top:17px}}";
  refreshCss += ".ranking-board{width:100%}.ranking-board .table-wrap table[data-board-table]:not(.responsive-condensed){width:100%;table-layout:fixed;min-width:640px!important}.ranking-board .table-wrap table[data-board-table]:has(.col-logged):not(.responsive-condensed){min-width:930px!important}.ranking-board table[data-board-table]:not(.responsive-condensed) col.col-rank{width:32px!important}.ranking-board table[data-board-table]:not(.responsive-condensed) col.col-player{width:230px!important}.ranking-board table[data-board-table]:not(.responsive-condensed) col.col-total{width:auto!important}.ranking-board table[data-board-table]:not(.responsive-condensed) col.col-rate{width:88px!important}.ranking-board table[data-board-table]:not(.responsive-condensed) col.col-time{width:100px!important}.ranking-board table[data-board-table]:not(.responsive-condensed) col.col-fights{width:64px!important}.ranking-board table[data-board-table] col.col-logged{width:82px!important}.ranking-board table[data-board-table] col.col-connection{width:96px!important}.ranking-board table[data-board-table] col.col-casts{width:60px!important}.ranking-board table[data-board-table] col.col-hit-cast{width:86px!important}.ranking-board table[data-board-table]:not(.responsive-condensed) th,.ranking-board table[data-board-table]:not(.responsive-condensed) td{width:auto;padding-left:7px;padding-right:7px}";
  // Compact native metric grids without shrinking names, icons or numeric text.
  refreshCss += ".metric-grid-board{width:fit-content;max-width:100%;min-width:0;justify-self:start}.metric-grid,.metric-grid-compact{width:max-content;min-width:0;table-layout:auto}.metric-grid th,.metric-grid td{width:112px;min-width:90px;padding:3px 9px;line-height:1.2;white-space:nowrap}.metric-grid thead th:first-child,.metric-grid tbody th{width:auto;min-width:210px}.metric-grid tbody th,.metric-grid tbody td{padding:3px 9px}.metric-grid .table-player{vertical-align:middle;gap:6px;line-height:1.2}.metric-grid .table-player b{line-height:1.2}.metric-grid button{min-height:0}.metric-grid thead button[data-sort-key]{min-height:0;padding:4px 20px 4px 0!important;line-height:1.2}.metric-grid thead th{padding-top:5px;padding-bottom:5px}.kill-comparison{--summary-kills:#20c9f3;--summary-deaths:#ff685e}.combat-kills strong,.combat-enemy-downs strong{color:var(--summary-kills)}.combat-deaths strong,.combat-our-downs strong{color:var(--summary-deaths)}html[data-theme=studio-light] .kill-comparison{--summary-kills:#007caa;--summary-deaths:#c83735}";
  refreshCss += ".ranking-board .table-wrap table[data-board-table].responsive-metrics,.table-wrap table.responsive-metrics{width:max-content;min-width:0!important;table-layout:auto}.ranking-board .table-wrap table[data-board-table].responsive-metrics{width:100%}.metric-grid.responsive-condensed .responsive-identity{width:calc(100% - 108px)!important}.responsive-metrics col{width:auto!important}.table-wrap .responsive-metrics th,.table-wrap .responsive-metrics td{width:auto;white-space:nowrap}.row-metric-details{display:none;font-size:12px;font-weight:400;white-space:normal}.row-metric-details summary{cursor:pointer;color:var(--muted);padding:3px 0;min-height:24px}.row-metric-details dl{margin:4px 0;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:5px 10px}.row-metric-details dt{overflow-wrap:anywhere}.row-metric-details dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}.table-wrap table.responsive-condensed,.metric-grid.responsive-condensed{width:100%!important;min-width:0!important;table-layout:fixed!important}.responsive-condensed colgroup,.responsive-condensed col{display:none!important}.responsive-condensed .responsive-extra{display:none!important}.responsive-condensed .responsive-identity{width:calc(100% - 136px)!important;min-width:0!important;white-space:normal!important}.responsive-condensed .responsive-primary{overflow-wrap:anywhere;width:108px!important;min-width:0!important;white-space:normal!important}.responsive-condensed .responsive-rank{width:28px!important;min-width:0!important;padding-left:5px!important;padding-right:3px!important}.responsive-condensed .table-player{max-width:100%;white-space:normal;align-items:center}.responsive-condensed .table-player b{white-space:normal;overflow-wrap:anywhere;line-height:1.25}.responsive-condensed .row-metric-details{display:block}.responsive-condensed .responsive-identity{position:static}.responsive-condensed th button{white-space:normal!important}.responsive-condensed tbody tr[hidden]{display:none!important}";
  // Share the identity/time footprint; spare width separates pairs, not total from rate.
  refreshCss += ".metric-grid-board{width:100%;justify-self:stretch}.metric-grid.metric-grid-compact{width:100%;min-width:var(--grid-min-width);table-layout:fixed}.metric-grid col.grid-player-col{width:230px!important}.metric-grid col.grid-rate-col{width:60px!important}.metric-grid col.grid-time-col{width:85px!important}.metric-grid thead th:first-child,.metric-grid tbody th{width:230px;min-width:0}.metric-grid:not(.responsive-condensed) .pair-total{padding-right:4px!important}.metric-grid:not(.responsive-condensed) .pair-rate{padding-left:4px!important}.metric-grid[data-metric-grid=healing] col.grid-rate-col{width:78px!important}.metric-grid:not(.responsive-condensed) thead button[data-sort-key]{display:flex;flex-direction:row-reverse;justify-content:flex-start;gap:4px;padding-left:0!important;padding-right:0!important}.metric-grid:not(.responsive-condensed) thead th button[data-sort-key]:after{position:static;transform:none}";
  // One header line, adjacent total/rate columns, and compact sort targets.
  refreshCss += ".table-wrap table thead th,.metric-grid thead th{padding:3px 7px!important;font-size:12px;line-height:1.2;text-transform:none;letter-spacing:normal}.table-wrap table thead button[data-sort-key],.metric-grid thead button[data-sort-key]{min-height:22px!important;line-height:1.2!important;padding:2px 17px 2px 0!important;font-size:inherit}.metric-grid th,.metric-grid td{width:auto;min-width:0;padding:3px 7px}.metric-grid .pair-total{border-left:1px solid var(--line)}.metric-grid thead th:first-child,.metric-grid tbody th{min-width:195px}.ranking-board .table-wrap table[data-board-table].responsive-metrics{width:max-content}.ranking-board .responsive-metrics td,.ranking-board .responsive-metrics tbody th{padding:3px 7px!important;font-size:14px;line-height:1.25}.ranking-board .responsive-metrics .player-cell{min-width:210px}.ranking-board .responsive-metrics .number{min-width:70px}.ranking-board .responsive-metrics .rank{min-width:20px}.responsive-condensed[data-primary-count='2'] .responsive-primary{width:80px!important;min-width:0!important;white-space:nowrap!important}.responsive-condensed[data-primary-count='2'] .responsive-identity{width:calc(100% - 188px)!important;min-width:0!important}.metric-grid.responsive-condensed[data-primary-count='2'] .responsive-identity{width:calc(100% - 160px)!important}.responsive-condensed[data-primary-count='2'] .player-cell{min-width:0!important}.responsive-condensed[data-primary-count='2'] thead .responsive-primary button{white-space:nowrap!important}.responsive-condensed[data-primary-count='2'] .row-metric-details dl{grid-template-columns:minmax(0,1fr);gap:2px}.responsive-condensed[data-primary-count='2'] .row-metric-details dd{text-align:left;margin-bottom:5px}";
  refreshCss += ".metric-grid thead th{padding-left:4px!important;padding-right:4px!important}.metric-grid tbody th,.metric-grid tbody td{padding:3px 4px}.metric-grid thead th:first-child,.metric-grid tbody th{padding-left:8px!important;padding-right:8px!important}";
  refreshCss += ".responsive-condensed tbody tr:has(.row-metric-details[open]) .responsive-primary{vertical-align:top;padding-top:8px!important}";
  // Header-only pair cues; no additional data bars or reduced numeric type.
  refreshCss += ".metric-grid[data-metric-grid=utility]:not(.responsive-condensed) col.grid-rate-col:not(.grid-primary-col){width:52px!important}.metric-grid[data-metric-grid=utility]:not(.responsive-condensed) tbody .pair-rate:not(.grid-primary-value){padding-right:4px!important}";
  refreshCss += ".metric-grid:not(.responsive-condensed) col.grid-primary-col{width:auto!important}.metric-grid .grid-primary-value{font-weight:750}.metric-grid tbody th,.metric-grid tbody td{padding-top:6px;padding-bottom:6px}.metric-grid:not(.responsive-condensed) .pair-rate:not(.grid-primary-value){padding-right:10px!important}.metric-grid:not(.responsive-condensed) .grid-primary-value{padding-right:8px!important}.metric-grid thead .pair-total,.metric-grid thead .pair-rate{border-bottom-color:var(--line)!important}.metric-grid tbody td.pair-total{color:var(--muted)}";
  refreshCss += ".metric-grid .metric-cue-label{display:inline-flex;align-items:center;gap:3px;white-space:nowrap}.metric-grid .metric-cue-icon{width:12px;height:12px;flex:0 0 12px;fill:none;stroke:var(--metric-cue);stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}.metric-grid thead .pair-total,.metric-grid thead .pair-rate{border-bottom-color:color-mix(in srgb,var(--metric-cue) 55%,var(--line))}.metric-grid .metric-cue-damage{--metric-cue:#db987f}.metric-grid .metric-cue-power{--metric-cue:#d5b16f}.metric-grid .metric-cue-condition{--metric-cue:#c79cca}.metric-grid .metric-cue-down{--metric-cue:#b3a5c9}.metric-grid .metric-cue-cleanse,.metric-grid .metric-cue-healing{--metric-cue:#7fbcac}.metric-grid .metric-cue-strip{--metric-cue:#c9a28b}.metric-grid .metric-cue-stability{--metric-cue:#c7b77e}.metric-grid .metric-cue-res{--metric-cue:#93bda2}.metric-grid .metric-cue-cc{--metric-cue:#89b6cd}.metric-grid thead th button[data-sort-key]:after{font-size:11px;opacity:.45}.metric-grid thead th[aria-sort=descending] button:after,.metric-grid thead th[aria-sort=ascending] button:after{opacity:.8}.metric-grid:not(.responsive-condensed) thead button[data-sort-key]{gap:2px}.metric-grid thead th:last-child button,.metric-grid tbody td:last-child{color:var(--muted)}.metric-grid[data-metric-grid=damage] [data-sort-key=m0-rate],.metric-grid[data-metric-grid=damage] td[data-metric-cell='0'][data-value-kind=rate]{font-weight:750}";
  refreshCss += ".ranking-board .table-wrap table[data-board-table].responsive-metrics:not(.responsive-condensed){width:100%;table-layout:fixed;min-width:640px!important}.ranking-board .table-wrap table[data-board-table].responsive-metrics:has(.col-logged):not(.responsive-condensed){min-width:930px!important}";
  refreshCss += ".ranking-board table[data-board-table]:not(.responsive-condensed) col.col-fights{width:80px!important}";
  refreshCss += ".ranking-board table[data-board-table]:not(.responsive-condensed):not(:has(.col-total)) col.col-rate{width:auto!important}";
  // Give surplus ranking width to the one comparison bar, never a blank total column.
  refreshCss += ".ranking-board table[data-board-table]:not(.responsive-condensed):has(.col-rate) col.col-total{width:112px!important}.ranking-board table[data-board-table]:not(.responsive-condensed) col.col-rate{width:auto!important}";
  function renderSimple() {
    var barrier=healMetric("Barrier","barrier","barrierps","Barrier / sec");
    var body = "<div class=\"simple-briefing\">" +
      reportHeading("Nightly briefing · essential results") +
      "" +
      totalsCards(true) +
      "<section class=\"boards\">" + ["damage","healing","utility"].map(function(group){return metricGrid(group);}).join("") + "</section>" +
      boonGenerationCharts(true) +
      (barrier ? "<details class=\"secondary-support\" open><summary>Barrier</summary>" + boardCard(barrier,10) + "</details>" : "") +
      "</div>";
    var simpleCss = [
      ".simple-briefing{--brief-accent:var(--accent-2);max-width:1120px;margin:0 auto;counter-reset:brief-board}",
      ".simple-briefing .hero{position:relative;padding:20px 24px;border:0;border-left:4px solid var(--brief-accent);",
      "border-radius:0;background:var(--surface)}.simple-briefing .hero:after{content:'';position:absolute;left:38px;right:38px;bottom:0;height:1px;background:var(--line)}",
      ".simple-briefing .eyebrow{color:var(--brief-accent);letter-spacing:.22em}",
      ".simple-briefing .hero h1{max-width:820px;margin:8px 0;font:500 clamp(28px,4vw,40px)/1.08 Georgia,'Times New Roman',serif;letter-spacing:-.025em}",
      ".brief-deck{margin:14px 0;color:var(--muted);font-size:14px}",
      ".simple-briefing .kpis{grid-template-columns:repeat(6,minmax(0,1fr));gap:1px;margin:0 0 16px;padding:1px;background:var(--line)}",
      ".simple-briefing .kpi,.simple-briefing .kpi[class*=metric-]{min-height:76px;padding:12px 16px;border:0;border-radius:0;background:var(--surface);cursor:default}",
      ".simple-briefing .kpi strong{color:var(--brief-accent);font:600 27px/1 Georgia,'Times New Roman',serif}",
      ".simple-briefing .kpi span{display:block;margin-top:9px;letter-spacing:.035em}",
      ".simple-briefing .boards{gap:16px}.simple-briefing .board{counter-increment:brief-board;border:0;border-radius:0;background:var(--table-surface);overflow:visible}",
      ".simple-briefing .board h3{display:flex;gap:14px;align-items:baseline;padding:0 0 12px;border-bottom:2px solid var(--table-rule);font:600 22px/1.2 Georgia,'Times New Roman',serif}",
      ".simple-briefing .board h3:before{content:counter(brief-board,decimal-leading-zero);color:var(--table-label);font:800 11px/1 Segoe UI,system-ui,sans-serif;letter-spacing:.12em}",
      ".simple-briefing .table-wrap{border-bottom:1px solid var(--table-rule)}.simple-briefing th{padding-top:11px;padding-bottom:11px;color:var(--text);background:transparent}",
      ".simple-briefing td{padding-top:11px;padding-bottom:11px}",
      ".simple-briefing .board-actions{padding:11px 0 0;border:0}.simple-briefing .board-actions button{border-radius:0;border-color:var(--line);background:var(--panel-2)}",
      ".simple-briefing .foot{margin:44px 0 0;padding:20px 0;border-top:1px solid var(--line);color:var(--muted);font-family:Georgia,'Times New Roman',serif}",
      ".simple-briefing .metric-grid tbody th{font-size:14px;color:var(--text);font-weight:600}.simple-briefing .metric-grid thead th{color:var(--muted)}",
      "@media(max-width:850px){.simple-briefing .kpis{grid-template-columns:repeat(3,minmax(0,1fr))}.simple-briefing .hero{padding:20px}.simple-briefing .hero:after{left:20px;right:20px}}",
      "@media(max-width:520px){.simple-briefing .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.brief-deck{font-size:13px}.simple-briefing .hero h1{font-size:28px}",
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
      simpleCss + refreshCss + "</style></head><body class=\"simple-report\"><main class=\"wrap\">" +
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
          " data-index=\"" + esc(f.index || 0) + "\" data-time=\"" + esc(reportInstant(f.time_label) ? reportInstant(f.time_label).getTime() : f.index || 0) +
          "\" data-duration=\"" + esc(fightDurationMs(f.duration)) + "\" data-squad=\"" + esc(f.squad || 0) +
          "\" data-enemy=\"" + esc(f.enemy || 0) + "\" data-downs=\"" + esc(f.downs || 0) +
          "\" data-kills=\"" + esc(f.kills || 0) + "\" data-damage-out=\"" + esc(f.damage_out || 0) +
          "\" data-damage-in=\"" + esc(f.damage_in || 0) + "\" data-outcome=\"" + outcome + "\"" +
          drillAttrs("fight-row", "Fight " + (f.index || "—"),
          "Squad " + fmt(f.squad) + " vs " + fmt(f.enemy) + " · " +
          fmt(f.downs) + " downs · " + fmt(f.kills) + " kills",
          "Observed fight summary · " + (reportTimestamp(f.time_label,true) || "time unavailable")) +
          (index >= 5 && !showAll ? " hidden" : "") +
          "><td class=\"number\" data-label=\"Fight\">" + fmt(f.index) + "</td><td data-label=\"Time\">" +
          "<span title=\"" + esc(reportTimestamp(f.time_label,true) || "Time unavailable") + "\">" +
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
    if (!rows.length) return "";
    var initialLimit=5;
    return "<article class=\"board poison-board\"><h3>Poison Applications</h3>" +
      "<div class=\"table-wrap\"><table class=\"poison-table\" data-board-table><colgroup>" +
      "<col class=\"poison-rank\"><col class=\"poison-player\">" +
      "<col class=\"poison-apps\"><col class=\"poison-rate\"><col class=\"poison-output\"></colgroup>" +
      "<thead><tr><th>#</th><th>Player</th>" +
      "<th class=\"number\"><button type=\"button\" data-sort-key=\"total\">Applications</button></th>" +
      "<th class=\"number\" aria-sort=\"descending\"><button type=\"button\" data-sort-key=\"rate\">Apps / min</button></th>" +
      "<th class=\"number\">Poison / sec</th></tr></thead><tbody>" + rows.map(function (r, i) {
        return "<tr" + drillAttrs("condition-row", r.name || "Poison output",
          fmt(r.apps) + " applications · " + fmt(r.apps_per_min) + " per minute",
          "Observed poison output; relic trigger attribution unavailable") +
          " data-total=\"" + esc(r.apps || 0) + "\" data-rate=\"" + esc(r.apps_per_min || 0) + "\"" +
          (i >= initialLimit ? " class=\"board-extra poison-extra\" hidden" : "") +
          "><td class=\"number\">" + (i + 1) + "</td><td class=\"poison-player-cell\">" + tablePlayer(r) +
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
    var cards=boardsFor(terms).map(function(board){return boardCard(board,limit || 10);}).filter(Boolean);
    return cards.length ? "<div class=\"boards\">"+cards.join("")+"</div>" : "";
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
      var metrics=row.metrics || {}, hasTotal=finiteMetric(metrics[totalKey]) != null;
      var hasRate=rateKey && finiteMetric(metrics[rateKey]) != null;
      if (!hasTotal && !hasRate) return null;
      var total=hasTotal ? Number(metrics[totalKey]) : null;
      var participation=finiteMetric(row.participation_time || metrics.fighttime || metrics.activetime) || 0;
      var rate=hasRate ? Number(metrics[rateKey]) :
        (total != null && participation>0 ? total / participation * (perMinute ? 60 : 1) : null);
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
    return "<div class=\"metric-chart-grid\"><article class=\"board rate-ranking-card tone-" + esc(tone || "accent") +
      "\"><h3>" + esc(board.stat) + "</h3><div class=\"rate-ranking-head\"><span>"+(/by profession/i.test(board.stat)?"Profession":"Player")+"</span><span>" + esc(rateLabel) +
      "</span></div>" + rows.map(function(row,index){
        var value=weightedRowValue(row),width=Math.abs(value)/max*100;
        return "<button type=\"button\" class=\"rate-ranking-row\"" + drillAttrs("session-player",
          row.name || board.stat, rateLabel + " " + fmt(value) +
          (row.total != null ? " · Total " + fmt(row.total) : ""),
          "Selected night · " + (board.source_tiddler || board.stat)) +
          " style=\"--comparison-color:"+professionColor(row.profession)+"\"><span class=\"rate-ranking-rank\">"+(index+1)+"</span>" + playerBarLabel(row) + "<b>" + fmt(value) + "</b><i class=\"rate-ranking-track\"><em style=\"width:" +
          width + "%\"></em></i></button>";
      }).join("") + "</article></div>";
  }
  refreshCss += '.rate-ranking-card{padding:0!important;overflow:hidden}.rate-ranking-card h3{margin:0;padding:14px 16px;border-bottom:1px solid var(--line)}.rate-ranking-head{display:flex;justify-content:space-between;gap:12px;padding:8px 16px 8px 48px;color:var(--muted);font-size:12px}.rate-ranking-row{display:grid;grid-template-columns:24px minmax(0,1fr) auto;align-items:center;gap:8px;width:100%;padding:10px 16px;border:0;border-top:1px solid var(--line-soft);background:transparent;color:var(--text);font:inherit;text-align:left;cursor:pointer}.rate-ranking-row:hover,.rate-ranking-row:focus-visible{background:var(--panel-2)}.rate-ranking-rank{color:var(--muted);font-size:12px}.rate-ranking-row>b{font-size:14px;font-variant-numeric:tabular-nums;text-align:right}.rate-ranking-row .player-bar-label{min-width:0}.rate-ranking-track{grid-column:1/-1;display:block;height:8px;background:var(--panel-2);border-radius:3px;overflow:hidden}.rate-ranking-track em{display:block;height:100%;background:var(--accent);border-radius:3px}.rate-ranking-row .table-player,.rate-ranking-row .table-player b{white-space:normal;overflow-wrap:anywhere}.rate-ranking-row .table-player>span{flex-shrink:0}@media(max-width:620px){.rate-ranking-row{padding:10px 12px;gap:7px;grid-template-columns:20px minmax(0,1fr) auto}.rate-ranking-head{padding-left:39px;padding-right:12px}}';
  function metricBoardView(board, tone, includeTable) {
    if (!board) return "";
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
  refreshCss += ".bubble-card h4{margin:16px 0 8px}.bubble-paired{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:12px;align-items:start}.bubble-paired>h4{grid-column:1/-1}.bubble-scroll .bubble-scatter{width:100%;max-width:900px;min-width:0}.bubble-key-scroll{max-height:430px;overflow:auto}.bubble-identity-table{width:100%;min-width:0!important;table-layout:fixed}.bubble-identity-table th,.bubble-identity-table td{padding:5px!important;font-size:12px;text-align:right;white-space:normal!important}.bubble-identity-table th:first-child{width:46%;text-align:left}.bubble-identity-table thead th{overflow-wrap:anywhere;text-transform:none}.bubble-scroll text{font-size:16px}.bubble-identity-table thead{position:sticky;top:0;z-index:1}.bubble-identity-table .bubble-identity{display:flex;align-items:center;gap:4px;width:100%;padding:0;border:0;background:none;color:var(--text);font:inherit;text-align:left;cursor:pointer}.bubble-identity-table .table-player{min-width:0;white-space:normal}.bubble-identity-table .table-player b{white-space:normal;overflow-wrap:anywhere}.bubble-identity:focus-visible{outline:2px solid var(--accent)}.bubble-scatter .bubble-number{display:none}.bubble-scatter .point-highlight+.bubble-number{display:block}.bubble-scatter.has-highlight [data-bubble]:not(.point-highlight){opacity:.12}.bubble-scatter .point-highlight{stroke-width:2}@media(max-width:1200px){.bubble-paired{grid-template-columns:minmax(0,1fr)}.bubble-key-scroll{max-height:280px}}@media(max-width:600px){.bubble-card{padding:12px}.bubble-scroll text{font-size:24px}.bubble-identity-table th,.bubble-identity-table td{padding:4px 2px!important}}";
  function bubbleChart(group) {
    var support=group === "support";
    var xBoard=support ? supportMetric("Boons Removed","boonstrips","Boons Removed / min") : derivedDamageBoard("DPS","targetdamage","targetdamageps","DPS");
    var yBoard=support ? supportMetric("Condition Cleanses","condicleanse","Cleanses / min") : derivedMetricBoard("Down contribution","Offensive-Summary","downcontribution",null,"Down contribution / sec",false);
    var sizeBoard=support ? supportMetric("Resurrects","resurrects","Resurrects / min") : derivedMetricBoard("Damage to downed enemies","Offensive-Summary","againstdowneddamage",null,"Damage to downed enemies / sec",false);
    function indexed(board){var map=new Map();if(board)(board.rows||[]).forEach(function(row){map.set(metricIdentity(row),row);});return map;}
    var xs=indexed(xBoard),ys=indexed(yBoard),sizes=indexed(sizeBoard);
    var keys=Array.from(new Set(Array.from(xs.keys()).concat(Array.from(ys.keys()),Array.from(sizes.keys()))));
    function value(row){var n=finiteMetric(row && row.rate);return n!=null && n>=0 ? n : null;}
    var all=keys.map(function(key){return {key:key,row:xs.get(key)||ys.get(key)||sizes.get(key),x:value(xs.get(key)),y:value(ys.get(key)),size:value(sizes.get(key))};});
    var maxX=Math.max.apply(null,all.map(function(p){return p.x||0;}).concat([0]));
    var maxY=Math.max.apply(null,all.map(function(p){return p.y||0;}).concat([0]));
    var maxSize=Math.max.apply(null,all.map(function(p){return p.size||0;}).concat([0]));
    var xLabel=support ? "Boons removed / min" : "DPS",yLabel=support ? "Cleanses / min" : "Down contribution / sec";
    var sizeLabel=support ? "resurrects / min" : "damage to downed enemies / sec";
    function shown(v){return v==null ? "—" : fmt(v);}
    function detail(p){return p.row.name+" · "+p.row.profession+" · "+xLabel+": "+shown(p.x)+" · "+yLabel+": "+shown(p.y)+" · "+sizeLabel+": "+shown(p.size);}
    function attrs(p){return ' data-overview-name="'+esc(p.row.name)+'" data-identity="'+esc(p.key)+'" data-x="'+(p.x==null?'':p.x)+'" data-y="'+(p.y==null?'':p.y)+'" data-size="'+(p.size==null?'':p.size)+'"';}
    function radius(p){return p.size>0 ? 18*Math.sqrt(p.size/maxSize) : 3;}
    function circle(p,cx,cy,index){return '<circle data-bubble data-point-index="'+index+'" data-point-name="'+esc(p.row.name)+'" data-size="'+(p.size==null?'':p.size)+'" data-tooltip="'+esc(detail(p))+'" tabindex="0" role="button" aria-label="'+esc(detail(p))+'" cx="'+cx.toFixed(3)+'" cy="'+cy.toFixed(3)+'" r="'+radius(p).toFixed(3)+'" fill="'+(p.size>0?'var(--point-color)':'none')+'" stroke="var(--point-color)"'+(p.size==null?' stroke-dasharray="2 2"':'')+drillAttrs('session-player',p.row.name,detail(p),'Selected night')+'/>';}
    var points=[],missing=[];
    all.forEach(function(p){
      if(p.x==null||p.y==null){missing.push(p);return;}
      if(p.x!==0||p.y!==0)points.push(p);
    });
    points.sort(function(a,b){return b.y-a.y||b.x-a.x;});
    var scatter='';
    if(points.length){
      scatter='<section class="bubble-paired"><div class="bubble-scroll"><svg class="bubble-scatter" viewBox="0 0 900 480" role="img" aria-label="'+esc(xLabel+' versus '+yLabel)+'">';
      for(var tick=0;tick<=4;tick++) {
        var tx=75+tick*190,ty=410-tick*90;
        scatter+='<path class="bubble-gridline" d="M '+tx+' 50 V 410 M 75 '+ty+' H 835"/><text x="'+tx+'" y="432" text-anchor="middle">'+fmt(maxX*tick/4)+'</text><text x="65" y="'+(ty+4)+'" text-anchor="end">'+fmt(maxY*tick/4)+'</text>';
      }
      scatter+='<text x="455" y="468" text-anchor="middle">'+esc(xLabel)+'</text><text x="75" y="22">'+esc(yLabel)+'</text>';
      scatter+=points.slice().sort(function(a,b){return (b.size||0)-(a.size||0);}).map(function(p){var index=points.indexOf(p),cx=75+(maxX?p.x/maxX*760:0),cy=410-(maxY?p.y/maxY*360:0);return '<g style="--point-color:'+professionColor(p.row.profession)+'"'+attrs(p)+'>'+circle(p,cx,cy,index)+'<text class="bubble-number" x="'+(cx+radius(p)+3).toFixed(3)+'" y="'+(cy-4).toFixed(3)+'">'+(index+1)+'</text></g>';}).join('')+'</svg></div><div class="bubble-key-columns">'+identityTable(points.slice(0,Math.ceil(points.length/2)),true)+identityTable(points.slice(Math.ceil(points.length/2)),true)+'</div></section>';
    }
    function identityTable(list,paired){
      if(!list.length)return '';
      return '<div class="bubble-key-scroll"><table class="bubble-identity-table"><thead><tr><th>Player</th><th title="'+esc(xLabel)+'">'+(support?'Removed / min':'DPS')+'</th><th title="'+esc(yLabel)+'">'+(support?'Cleanses / min':'Down / sec')+'</th><th title="'+esc(sizeLabel)+'">'+(support?'Res / min':'Downed / sec')+'</th></tr></thead><tbody>'+list.map(function(p){var index=points.indexOf(p);return '<tr style="--point-color:'+professionColor(p.row.profession)+'"'+(paired?'':attrs(p))+'><th><button type="button" class="bubble-identity" '+(paired?'data-highlight-point="'+index+'" ':'')+'data-tooltip="'+esc(detail(p))+'"'+drillAttrs('session-player',p.row.name,detail(p),'Selected night')+'>'+(paired?'<span>'+(index+1)+'.</span>':'')+tablePlayer(p.row)+'</button></th><td>'+compactMetric(p.x)+'</td><td>'+compactMetric(p.y)+'</td><td>'+compactMetric(p.size)+'</td></tr>';}).join('')+'</tbody></table></div>';
    }
    return '<article class="bubble-card bubble-comparison" data-bubble-chart="'+group+'"><h3>'+(support?'Boon removal & cleansing':'Damage & down pressure')+'</h3><p class="bubble-size-key">Area: '+esc(sizeLabel)+' · ○ 0 · ◌ —</p>'+scatter+(missing.length?'<h4>Incomplete data</h4>'+identityTable(missing,false):'')+'</article>';
  }
  refreshCss += ".bubble-comparison .bubble-paired{display:block}.bubble-comparison .bubble-scroll{overflow:visible}.bubble-comparison .bubble-scatter{margin:0 auto;max-width:100%;height:auto;max-height:520px}.bubble-comparison .bubble-key-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;margin-top:16px}.bubble-comparison .bubble-key-scroll{max-height:none;overflow:visible;min-width:0}.bubble-comparison .bubble-identity-table th,.bubble-comparison .bubble-identity-table td{font-size:14px;padding:6px 5px!important}.bubble-comparison .bubble-identity-table th:first-child{width:52%}.bubble-comparison .bubble-identity-table td{font-variant-numeric:tabular-nums}.bubble-comparison .bubble-identity-table thead{position:static}.bubble-comparison .bubble-identity-table tbody tr:has(.bubble-identity:focus-visible),.bubble-comparison .bubble-identity-table tbody tr:hover{background:color-mix(in srgb,var(--text) 6%,transparent)}.bubble-comparison .bubble-size-key{font-size:14px;margin:8px 0 18px}@media(max-width:1100px){.bubble-comparison .bubble-key-columns{grid-template-columns:minmax(0,1fr);gap:0}.bubble-comparison .bubble-key-columns>.bubble-key-scroll+ .bubble-key-scroll thead{display:none}}@media(max-width:600px){.bubble-comparison .bubble-identity-table th,.bubble-comparison .bubble-identity-table td{padding:6px 2px!important}.bubble-comparison .bubble-identity-table th:first-child{width:46%}}";
  function damageCompositionView() {
    var source=tableBySource("Damage");
    if (!source) return "";
    var rows=(source.rows || []).map(function(row){var metrics=row.metrics || {};
      return {row:row,total:Number(metrics.targetdamageps || 0),power:Number(metrics.targetpowerps || 0),
        condition:Number(metrics.targetconditionps || 0)};}).sort(function(a,b){return b.total-a.total;}).slice(0,5);
    var max=Math.max.apply(null,rows.map(function(item){return item.total;}).concat([1]));
    return "<article class=\"board damage-composition-card\"><h3>Total DPS · Power + Condition</h3>" +
      "<div class=\"damage-composition-legend\"><span class=\"power\">Power</span><span class=\"condition\">Condition</span></div>" +
      rows.map(function(item,index){var row=item.row,totalWidth=Math.max(0,item.total/max*100),powerShare=item.total ? item.power/item.total*100 : 0,
        conditionShare=item.total ? item.condition/item.total*100 : 0;
        return "<button type=\"button\" class=\"damage-composition-row\"" + drillAttrs("session-player",row.name || "Player",
          "Total DPS " + fmt(item.total) + " · Power DPS " + fmt(item.power) + " · Condition DPS " + fmt(item.condition),
          "Selected night · Damage to enemy players") + "><span class=\"damage-rank\">"+(index+1)+"</span><span class=\"damage-player\">" + tablePlayer(row) +
          "</span><span class=\"damage-total-track\"><i style=\"width:" + totalWidth.toFixed(1) + "%\"><em class=\"power\" style=\"width:" +
          Math.max(0,powerShare).toFixed(1) + "%\"></em><em class=\"condition\" style=\"width:" + Math.max(0,conditionShare).toFixed(1) +
          "%\"></em></i></span><strong>" + fmt(item.total) + " DPS</strong></button>";}).join("") + "</article>";
  }
  function fightImpactBoards(includeTables) {
    var boards=[derivedMetricBoard("Down-Contribution Damage","Offensive-Summary","downcontribution",null,"Down Contribution / sec",false),
      derivedMetricBoard("Enemy Downs","Offensive-Summary","downed",null,"Enemy Downs / min",true),
      derivedMetricBoard("Enemy Kills","Offensive-Summary","killed",null,"Enemy Kills / min",true)].filter(Boolean);
    return boards.length ? boards.map(function(board){return metricBoardView(board,"danger",includeTables);}).join("") :
      "";
  }
  function supportMetric(label,key,rateLabel) {
    return derivedMetricBoard(label,"Support-Summary",key,null,rateLabel,true);
  }
  function offensiveMetric(label,key,rateLabel) {
    return derivedMetricBoard(label,"Offensive-Summary",key,null,rateLabel,true);
  }
  refreshCss += '.support-rankings{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:16px 0}.support-ranking{min-width:0}.support-ranking table{width:100%;min-width:0!important;table-layout:fixed}.support-ranking tbody tr[hidden]{display:none!important}.support-ranking td{padding:9px 14px!important;white-space:normal!important}.support-ranking-labels,.support-ranking-line{display:grid;grid-template-columns:minmax(0,1fr) 110px 74px;gap:8px;align-items:center}.support-ranking-labels{min-height:34px;padding:0 14px 8px;font-size:12px;color:var(--muted)}.support-ranking-labels span:not(:first-child),.support-ranking-line>span{text-align:right;font-variant-numeric:tabular-nums}.support-ranking .table-player{min-width:0;white-space:normal}.support-ranking .table-player b{white-space:normal;overflow-wrap:anywhere}.support-ranking-track{display:block;height:7px;background:var(--border);border-radius:4px;margin-top:8px;overflow:hidden}.support-ranking-track i{display:block;height:100%;background:var(--support);border-radius:4px}.support-ranking h3{margin-bottom:12px}@media(max-width:760px){.support-rankings{grid-template-columns:minmax(0,1fr)}.support-ranking-labels,.support-ranking-line{grid-template-columns:minmax(0,1fr) 82px 64px;gap:6px}.support-ranking table tbody tr{display:table-row!important}.support-ranking table tbody td{display:table-cell!important;width:auto!important}}';
  function supportRankings() {
    var definitions=[['Cleansing','condicleanse','Cleanses / min'],['Boon removal','boonstrips','Strips / min']];
    return '<div class="support-rankings" data-support-rankings>'+definitions.map(function(def){
      var board=supportMetric(def[0],def[1],def[2]);
      if(!board)return '';
      var rows=(board.rows||[]).slice().sort(function(a,b){return compareMetricValues(finiteMetric(a.rate),finiteMetric(b.rate),'descending');});
      var maximum=Math.max.apply(null,rows.map(function(row){return finiteMetric(row.rate)||0;}).concat([0]));
      function shown(value){return finiteMetric(value)==null?'—':fmt(value);}
      return '<article class="board support-ranking" data-support-metric="'+def[1]+'"><h3>'+def[0]+'</h3><div class="support-ranking-labels"><span>Player</span><span>'+def[2]+' ↓</span><span>Total</span></div><table aria-label="'+def[0]+'" data-initial-limit="5"><tbody>'+rows.map(function(row,index){
        var rate=finiteMetric(row.rate),width=maximum>0&&rate!=null?Math.max(0,rate)/maximum*100:0;
        return '<tr data-support-player="'+esc(metricIdentity(row))+'" data-rate="'+(rate==null?'':rate)+'" data-total="'+(finiteMetric(row.total)==null?'':row.total)+'"'+(index>=5?' hidden':'')+'><td><div class="support-ranking-line">'+tablePlayer(row)+'<span title="'+esc(def[2]+': '+shown(rate))+'">'+shown(rate)+'</span><span title="Total: '+shown(row.total)+'">'+shown(row.total)+'</span></div><span class="support-ranking-track" aria-hidden="true"><i style="width:'+width+'%;background:'+professionColor(row.profession)+'"></i></span></td></tr>';
      }).join('')+'</tbody></table>'+(rows.length>5?'<footer class="board-actions"><button type="button" data-expand-board aria-expanded="false">Expand all '+rows.length+'</button></footer>':'')+'</article>';
    }).join('')+'</div>';
  }
  function supportOverviewView() {
    var boards=[supportMetric("Condition Cleanses","condicleanse","Cleanses / min"),
      supportMetric("Boons Removed","boonstrips","Boons Removed / min"),
      offensiveMetric("Crowd Control","appliedcrowdcontrol","Crowd Control / min"),
      supportMetric("Resurrects","resurrects","Resurrects / min")].filter(Boolean);
    return metricGrid("utility") + supportRankings() + (boards.length ? "<div class=\"curated-metric-stack\">" + boards.map(function(board){
      return metricBoardBars(board,"support");}).join("") + "</div>" :
      "");
  }
  function cleansesView() {
    return metricBoardView(supportMetric("Condition Cleanses","condicleanse","Cleanses / min"),"support",true);
  }
  function stripsAndControlView() {
    return metricGrid("strips") || "";
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
    return metricGrid("healing") + (boards.length ? "<div class=\"curated-metric-stack\">" + boards.map(function(board){
      return metricBoardBars(board,"heal");}).join("") + "</div>" :
      "");
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
      identity_label:"Profession",count_label:"Players",value_label:"Healing / sec",metric:{total_label:"Healing",rate_label:"Healing / sec"},rows:rows} : null;
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
      esc(kicker) + "</span><h2>" + esc(title) + "</h2></div></div>";
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
            fmt(value), "Source: Overview fight summary") +
          "><title>Fight " + esc(f.index || fightIndex + 1) + " · " + s.label +
          ": " + fmt(value) + "</title></rect>";
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
    return boardsFor(terms).filter(function(board){return (board.rows || []).length;}).slice(0,4)
      .map(function(board){return metricBoardBars(board,tone);}).join("");
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
      "</article>";
  }
  function boonColor(key) {
    var colors={stability:"#e4b95b",might:"#ef816b",fury:"#d884bb",quickness:"#71c7d5",alacrity:"#9e9fea",protection:"#73a9e5",aegis:"#e1cc80",resolution:"#85c7b1",resistance:"#b5a4de",regeneration:"#79c78e",vigor:"#b1cc6d"};
    return colors[String(key).toLowerCase().split(" ")[0]] || "var(--accent)";
  }
  function accessibleBar(label,value,unit,width,detail) {
    var exact=label+": "+fmt(value)+" "+unit+(detail ? " · "+detail : "");
    return "<div class=\"sparky-boon-bar\" tabindex=\"0\" title=\""+esc(exact)+"\" aria-label=\""+esc(exact)+"\"><span class=\"boon-track\" aria-hidden=\"true\"><i style=\"width:"+Math.max(0,Math.min(100,width)).toFixed(2)+"%\"></i></span><b>"+fmt(value)+" "+esc(unit)+"</b><span class=\"chart-tooltip\">"+esc(exact)+"</span></div>";
  }
  function multiboonGenerationChart(compact) {
    var data=model.boon_generation || {};
    var unavailable='';
    if (data.scope !== "session" || data.unit !== "weighted_generation") return unavailable;
    var totals={},rows=(data.rows || []).map(function(row){
      var boons={},total=0;
      Object.keys(row.boons || {}).forEach(function(name){
        var value=finiteMetric(row.boons[name]);
        if (value == null || value<0) return;
        boons[name]=value;total+=value;totals[name]=(totals[name] || 0)+value;
      });
      return {name:row.name || "Player",profession:row.profession || "Unknown",boons:boons,total:total};
    }).filter(function(row){return Object.keys(row.boons).length;}).sort(function(a,b){return b.total-a.total || a.name.localeCompare(b.name);});
    if (!rows.length) return unavailable;
    var names=Object.keys(totals).sort(function(a,b){return totals[b]-totals[a] || a.localeCompare(b);});
    var grand=names.reduce(function(sum,name){return sum+totals[name];},0);
    var major=names.filter(function(name,index){return index<7 && (grand===0 || totals[name]/grand>=.01);});
    var minor=names.filter(function(name){return major.indexOf(name)<0;});
    var categories=major.concat(minor.length ? ["Other"] : []),max=Math.max.apply(null,rows.map(function(row){return row.total;}).concat([1]));
    function color(name){return name === "Other" ? "#8d96a6" : boonColor(name);}
    return '<section class="multiboon-chart generation-card" data-multiboon-chart><div class="chart-title"><h2>Squad boons</h2><span>Boon output</span></div><div class="multiboon-legend">'+categories.map(function(name){return '<span><i style="background:'+color(name)+'"></i>'+esc(name)+'</span>';}).join('')+'</div><div class="multiboon-rows">'+rows.map(function(row,index){
      var detail=row.name+' · Boon output: '+fmt(row.total);
      return (compact && index===10 ? '<details class="boon-more"><summary>Show all '+rows.length+' player rows / collapse</summary>' : '')+'<div class="multiboon-row" data-multiboon-player="'+esc(row.name)+'"><span class="multiboon-name">'+tablePlayer(row)+'</span><div class="multiboon-bar" tabindex="0" aria-label="'+esc(detail)+'" title="'+esc(detail)+'"><span class="multiboon-track">'+categories.map(function(name){
        var value=name === 'Other' ? minor.reduce(function(sum,key){return sum+(row.boons[key] || 0);},0) : (row.boons[name] || 0);
        return '<i data-boon="'+esc(name)+'" tabindex="0" data-tooltip="'+esc(row.name+' · '+name+': '+fmt(value))+'" aria-label="'+esc(row.name+' · '+name+': '+fmt(value))+'" style="background:'+color(name)+';width:'+(value/max*100)+'%"></i>';
      }).join('')+'</span><span class="chart-tooltip">'+esc(detail)+'</span></div><b>'+fmt(row.total)+'</b></div>';
    }).join('')+(compact && rows.length>10 ? '</details>' : '')+'</div></section>';
  }
  function boonGenerationCharts(compact) {
    var overview=multiboonGenerationChart(compact);
    var boards=(model.stat_tables || []).filter(function(board){return /generation/i.test(String(board.stat || ""));});
    if (!boards.length) return overview;
    return overview + "<section class=\"boon-generation\"><div class=\"section-head\"><div>" +
      "<h2>Boon generation by profession</h2></div></div>" +
      boards.map(function(board){
        var professions={};
        (board.rows || []).forEach(function(row){var profession=row.profession || "Unknown";
          var item=professions[profession] || {profession:profession,total:0,time:0,players:0};
          var total=finiteMetric(row.total != null ? row.total : row.metrics && row.metrics.totalgen),time=finiteMetric(row.participation_time || row.metrics && row.metrics.fighttime);
          if (total == null || !(time>0)) return;
          item.total += total;
          item.time += time;
          item.players += 1; professions[profession]=item;});
        var rows=Object.keys(professions).map(function(key){var item=professions[key];
          item.rate=item.time ? item.total/item.time : 0; return item;})
          .sort(function(a,b){return b.rate-a.rate;});
        if (!rows.length) return "";
        var max=Math.max.apply(null,rows.map(function(row){return row.rate;}).concat([1]));
        var rateLabel=board.metric && board.metric.rate_label || "Generation / sec";
        return "<article class=\"bar-card generation-card\" style=\"--boon-color:"+boonColor(board.stat)+"\"><div class=\"chart-title\"><b>" +
          esc(titleCase(board.stat)) + " by Profession</b><span>" + esc(rateLabel) + " · highest first</span></div>" +
          rows.map(function(row){return "<div class=\"boon-stat-row\"><span><b>" + esc(row.profession) +
            "</b><small>"+fmt(row.players)+" players · "+humanDuration(row.time)+" combined participation</small></span>"+
            accessibleBar(rateLabel,row.rate,"generation / sec",row.rate/max*100,"Total generation: "+fmt(row.total)+"; combined player time: "+fmt(row.time)+" seconds")+"</div>";}).join("") + "</article>";
      }).join("") + "</section>";
  }
  function boonUptimeCharts(keys, title) {
    var board=(model.stat_tables || []).find(function(item){return item.source_key === "Uptimes" || item.stat === "Uptimes";});
    if (!board) return "";
    var cards=(keys || []).map(function(key){
      var rows=(board.rows || []).map(function(row){return {name:row.name,account:row.account,
        profession:row.profession,value:finiteMetric(row.metrics && row.metrics[key]),
        participation:row.participation_time,fights:row.fight_count};})
        .filter(function(row){return row.value != null;})
        .sort(function(a,b){return b.value-a.value;}).slice(0,5);
      if (!rows.length) return "";
      var stacks=key === "might" && !(board.rows || []).some(function(row){return row.metric_units && row.metric_units[key] === "percent";});
      var max=stacks ? 25 : 100,unit=stacks ? "stacks" : "%",label=titleCase(key)+(stacks ? " Average Stacks" : " Uptime");
      return "<article class=\"bar-card boon-card\" style=\"--boon-color:"+boonColor(key)+"\"><div class=\"chart-title\"><b>" +
        esc(label) + "</b><span>Top 5 · received boons · scale 0–"+max+" "+unit+"</span></div>" +
        rows.map(function(row){return "<div class=\"boon-stat-row\">" + tablePlayer(row) +
          accessibleBar(label,row.value,unit,row.value/max*100,row.name+"; received, not generated")+"</div>";}).join("") + "</article>";
    }).filter(Boolean).join("");
    return cards ? "<section class=\"boon-uptimes\"><div class=\"section-head\"><div><span class=\"eyebrow\">" +
      esc(title) + "</span><h2>Uptime leaders</h2></div></div>" +
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
    if (!rows.length) return "";
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
    function amount(row){var candidates=metric ? [row[metric]] : [row.damage,row.count,row.uptime_percent,row.value];return candidates.map(finiteMetric).find(function(v){return v != null;});}
    rows = (rows || []).filter(function(row){return amount(row) != null;}).sort(function(a,b){return amount(b)-amount(a);}).slice(0,10);
    if (!rows.length) return "";
    var values=rows.map(amount);
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
      "<span>Damage first · sort by hits or casts</span></div>" +
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
        "</div>" : "") + "</article>";
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
      "<span class=\"duel-mode\"><button type=\"button\" class=\"selected\" aria-pressed=\"true\" data-duel-mode=\"total\">Total</button>" +
      "<button type=\"button\" aria-pressed=\"false\" data-duel-mode=\"normalized\">Per enemy / fight</button></span><span>" +
      esc(scopeLabel) + " · " + fmt(fights) + " matched fight" + (fights === 1 ? "" : "s") +
      "</span></div><div class=\"duel-legend\"><span class=\"ours\">Our squad</span>" +
      "<span class=\"enemy\">Enemy</span></div>" +
      "<div class=\"duel-grid\">" + rendered + "</div></article>";
  }
  function poisonContext() {
    return "";
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
  // Dedicated High Scores ranking layout; keep shared table/grid styles untouched.
  refreshCss += ".boards.high-score-grid{grid-template-columns:minmax(0,1fr)}.high-score-bars table.high-score-table,.high-score-bars table.high-score-table thead,.high-score-bars table.high-score-table tbody{display:block;width:100%;min-width:0}.high-score-bars table.high-score-table tr{display:grid;grid-template-columns:24px minmax(0,1fr) max-content;gap:5px 10px;padding:8px 12px;align-items:center}.high-score-bars table.high-score-table th,.high-score-bars table.high-score-table td{display:block;min-width:0;width:auto;padding:0;border:0;white-space:normal}.high-score-bars table.high-score-table tr>:first-child{grid-column:1;grid-row:1}.high-score-bars table.high-score-table tr>:nth-child(2){grid-column:2;grid-row:1}.high-score-bars table.high-score-table tr>:nth-child(3){grid-column:3;grid-row:1}.high-score-bars table.high-score-table thead tr{padding-top:3px;padding-bottom:3px}.high-score-bars table.high-score-table tbody tr{border-bottom:1px solid var(--line-soft)}.high-score-bars table.high-score-table tbody tr[hidden]{display:none!important}.high-score-bars .score-entry{display:flex!important;flex-wrap:wrap;align-items:center;gap:3px 12px;cursor:pointer}.high-score-bars .score-context{color:var(--muted);font-size:13px;overflow-wrap:anywhere}.high-score-bars .table-player{max-width:100%;min-width:0}.high-score-bars .table-player b{white-space:normal;overflow-wrap:anywhere}.high-score-bars table.high-score-table .score-result{white-space:nowrap;font-variant-numeric:tabular-nums}.high-score-bars table.high-score-table tbody tr:before,.high-score-bars table.high-score-table tbody tr:after{content:'';grid-column:2/4;grid-row:2;height:9px;border-radius:3px;pointer-events:none}.high-score-bars table.high-score-table tbody tr:before{background:var(--line-soft);width:100%}.high-score-bars table.high-score-table tbody tr:after{background:var(--score-color);width:var(--score-width)}";
  refreshCss += ".damage-composition-card.board{padding:0;border-top:1px solid var(--line);overflow:hidden}.damage-composition-card .damage-composition-legend{padding:6px 12px 6px 46px;font-size:12px;text-transform:none}.damage-composition-card .damage-composition-row{grid-template-columns:24px minmax(0,1fr) max-content;gap:5px 10px;padding:8px 12px;min-width:0}.damage-composition-card .damage-rank{grid-column:1;grid-row:1;color:var(--muted);font-size:14px;text-align:center}.damage-composition-card .damage-player{grid-column:2;grid-row:1;min-width:0}.damage-composition-card .damage-player .table-player{max-width:100%;white-space:normal}.damage-composition-card .damage-player .table-player b{white-space:normal;overflow-wrap:anywhere}.damage-composition-card .damage-composition-row strong{grid-column:3;grid-row:1;font-size:14px;font-weight:400}.damage-composition-card .damage-total-track{grid-column:2/4;grid-row:2;width:100%;height:9px;border-radius:3px;background:var(--line-soft)}.damage-composition-card .damage-total-track>i{border-radius:3px}.damage-composition-card .damage-composition-row:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}";
  refreshCss += ".damage-composition-card .damage-composition-row{grid-template-columns:24px minmax(0,1fr) max-content!important}";
  // One native table palette; semantic profession/boon/team hues remain data.
  refreshCss += ':root{--table-surface:color-mix(in srgb,var(--panel) 96%,var(--text) 4%);--table-rule:color-mix(in srgb,var(--panel) 82%,var(--text) 18%);--table-label:color-mix(in srgb,var(--text) 78%,var(--panel));--comparison-track:color-mix(in srgb,var(--panel) 86%,var(--text) 14%)}' +
    ':root{--table-head:color-mix(in srgb,var(--panel) 88%,var(--text) 12%);--table-selected:color-mix(in srgb,var(--panel) 82%,var(--text) 18%);--table-hover:color-mix(in srgb,var(--panel) 78%,var(--text) 22%)}' +
    '.board,.bar-card,.bubble-card,.boon-card,.generation-card{background:var(--table-surface);border:1px solid var(--table-rule)!important;box-shadow:none}.board h3,.bar-card .chart-title,.bubble-card h3{color:var(--text);background:transparent;border-color:var(--table-rule)}' +
    'table,.comparison-head{--table-head:color-mix(in srgb,var(--panel) 88%,var(--text) 12%);--table-selected:color-mix(in srgb,var(--panel) 82%,var(--text) 18%);--table-hover:color-mix(in srgb,var(--panel) 78%,var(--text) 22%)}thead th,.metric-grid thead th:first-child{border-bottom-color:var(--table-rule)!important}.metric-grid tbody .pair-total,.metric-grid tbody td:last-child,.ranking-board .number,.bubble-identity-table td{color:var(--text)!important}.metric-grid tbody th{background:transparent!important}' +
    '.board tbody td,.board tbody th,.bubble-identity-table td,.bubble-identity-table th,.rate-ranking-row{border-color:var(--table-rule)}.rate-ranking-head,.support-ranking-labels,.comparison-head,.high-score-bars thead tr{background:var(--table-head,var(--comparison-track));color:var(--text)}.rate-ranking-rank,.board .rank,.board .score-context,.damage-rank{color:var(--table-label)!important}' +
    '.board tbody tr:is(:hover,:focus-within)>td,.board tbody tr:is(:hover,:focus-within)>th,.rate-ranking-row:is(:hover,:focus-visible),.damage-composition-row:is(:hover,:focus-visible){background-color:var(--table-hover,var(--comparison-track))!important}' +
    '.rate-ranking-track,.support-ranking-track,.damage-composition-card .damage-total-track{height:9px;background:var(--comparison-track);border-radius:3px}.rate-ranking-track em{background:var(--comparison-color,var(--accent))}.rate-ranking-track em,.support-ranking-track i{border-radius:3px}.high-score-bars table.high-score-table tbody tr:before{background:var(--comparison-track)}.rate-ranking-track em,.support-ranking-track i,.high-score-bars table.high-score-table tbody tr:after{opacity:1}.metric-grid tbody td[style*="--comparison-color"],.ranking-board tbody td[style*="--comparison-color"]{padding-bottom:14px!important;padding-top:5px!important}';
  function renderHighScoreBlocks(blocks) {
    if (!blocks.length) return "";
    return "<div class=\"boards high-score-grid\">" + blocks.map(function (block) {
      var scoreRows=(block.rows || []).slice(0,100).sort(function(a,b){return compareMetricValues(a.score,b.score,"descending");});
      var maxScore=Math.max.apply(null,scoreRows.map(function(r){return Number(r.score)||0;}).concat([0]));
      var rows = scoreRows.map(function (row, i) {
        var context=[row.fight != null ? "Fight " + row.fight : ""].concat(row.details || []).filter(Boolean).join(" · ");
        var width=maxScore > 0 ? Math.max(0,Math.min(100,Number(row.score)/maxScore*100 || 0)) : 0;
        return '<tr data-score="' + esc(row.score == null ? '' : row.score) + '" style="--score-width:' + width + '%;--score-color:' + professionColor(row.profession) + '"' +
          (i >= 5 ? ' class="board-extra" hidden' : '') + '><td class="rank" data-rank-cell>' +
          (i + 1) + '</td><td class="score-entry"' + drillAttrs('high-score',
            (row.name || 'Player') + ' · ' + (block.caption || 'High score'),
            fmt(row.score) + (context ? ' · ' + context : ''), '') + '>' + tablePlayer(row) +
          (context ? '<span class="score-context">'+esc(context)+'</span>' : '') +
          '</td><td class="number score-result">' + fmt(row.score) + '</td></tr>';
      }).join("");
      return '<article class="board high-score-bars"><h3>' + esc(block.caption || 'High score') +
        '</h3><table class="high-score-table" data-board-table><thead><tr><th>#</th><th>Player / Fight / Skill</th>' +
        '<th class="number">Result</th></tr></thead><tbody>' + rows +
        '</tbody></table>' + (scoreRows.length > 5 ? '<footer class="board-actions"><button type="button" data-expand-board aria-expanded="false">Expand all ' + scoreRows.length + '</button></footer>' : '') + '</article>';
    }).join("") + "</div>";
  }
  function playerSkillDamageView() {
    var source=model.player_skill_damage || [],players=Array.isArray(source) ? source : (source.players || []);
    if (!players.length) return "";
    return "<div class=\"skill-player-grid\">" + players.map(function(player){
      var skills=(player.skills || []).slice().sort(function(a,b){return Number(b.damage || 0)-Number(a.damage || 0);});
      var rows=skills.map(function(skill,index){return "<tr" + (index >= 5 ? " class=\"board-extra\" hidden" : "") +
        "><td class=\"rank\" data-label=\"#\">" + (index+1) + "</td><td data-label=\"Skill\"><b>" + esc(skill.skill || "Unknown skill") +
        "</b></td><td class=\"number\" data-label=\"Damage\">" + fmt(skill.damage) + "</td><td class=\"number\" data-label=\"Down Contribution\">" +
        fmt(skill.down_contribution) + "</td><td class=\"number\" data-label=\"Hits\">" + fmt(skill.hits) +
        "</td><td class=\"number\" data-label=\"Damage / Hit\">" + fmt(skill.damage_per_hit) +
        "</td><td class=\"number\" data-label=\"Share\">" + fmt(skill.percent_of_total) + "%</td></tr>";}).join("");
      return "<article class=\"board skill-player-board\"><h3>" + tablePlayer(player) + "</h3><div class=\"skill-coverage\"><b>" + fmt(player.total_damage) +
        " damage represented</b><span>Per-player table exported by Classic; absence does not mean zero.</span></div>" +
        "<div class=\"table-wrap\"><table class=\"skill-damage-table\"><colgroup><col class=\"skill-rank\"><col class=\"skill-name\"><col class=\"skill-value\"><col class=\"skill-value\"><col class=\"skill-value\"><col class=\"skill-value\"><col class=\"skill-share\"></colgroup>" +
        "<thead><tr><th>#</th><th>Skill</th><th class=\"number\">Damage</th><th class=\"number\">Down Contribution</th><th class=\"number\">Hits</th><th class=\"number\">Damage / Hit</th><th class=\"number\">Share</th></tr></thead><tbody>" + rows +
        "</tbody></table></div>" + (skills.length > 5 ? "<footer class=\"board-actions\"><button type=\"button\" data-expand-board aria-expanded=\"false\">Expand all " + skills.length + "</button></footer>" : "") + "</article>";
    }).join("") + "</div>";
  }
  function resurrectionSkillView() {
    var source=tableBySource("Combat-Resurrect");
    if (!source) return "";
    var ignored={prof:1,fighttime:1,activetime:1,numfights:1,count:1},totals={};
    (source.rows || []).forEach(function(row){Object.keys(row.metrics || {}).forEach(function(key){
      if (!ignored[key] && Number.isFinite(Number(row.metrics[key]))) totals[key]=(totals[key] || 0)+Number(row.metrics[key]);
    });});
    var rows=Object.keys(totals).map(function(key){return {key:key,value:totals[key]};}).sort(function(a,b){return b.value-a.value;});
    if (!rows.length) return "";
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
    return "";
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
    return "";
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
      fmt(Array.isArray(c.colors) ? c.colors.join(" / ") : c.colors) +
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
      "<h3>Switch opponent view</h3></div></div>" +
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
      "";
    return "" + comparison +
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
      }).join("") + "</div></article>";
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
      "<h3>Explore " + esc(label) + "</h3></div></div>" +
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
  function traitAppearanceLabel(item) {
    if (item.eligible_actor_appearances == null || item.observed_percent == null)
      return fmt(item.actor_appearances)+" observed appearances · eligible denominator / rate unavailable";
    return fmt(item.actor_appearances)+" / "+fmt(item.eligible_actor_appearances)+
      " eligible appearances (>=15 seconds active) · "+fmt(item.observed_percent)+"% observed";
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
      var detail="Proven trait "+item.trait+" triggered "+item.observed_skill+" in "+traitAppearanceLabel(item);
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
    var validation=scope ? (scope.role_validation || {status:"team_attribution_unavailable"}) : (model.enemy_intel && model.enemy_intel.role_validation || {});
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
          source_scope:candidate.source_scope,limitation:scope ? "Team-scoped profession-level candidate, not an individual build." : "All-opponents profession-level candidate, not an individual build."} : {},
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
      "<p>Avg. per fight · Estimated roles</p></div>"+
      '<div class="enemy-trait-findings">'+
      (scope ? enemyBuildEvidenceView(validation,rows.map(function(row){return row.profession;}),scopeLabel) : "<div class=\"intel-kpis\"><div><strong>"+fmt(snapshots)+"</strong><span>enemy snapshots</span></div><div><strong>"+
      fmt(totalEnemies/snapshots)+"</strong><span>average group size</span></div><div><strong>"+fmt(totalEnemies)+"</strong><span>observed enemy slots</span></div></div>"+
      enemyBuildEvidenceView(validation)) + "</div>"+
      "<div class=\"intel-visuals\">"+professionBars+(members.some(function(m){return roleClass(m.role)!=="unknown";}) ? "<article class=\"intel-card\"><h3>Representative Role Mix</h3>"+chips(roleRows,"name","count",12)+"</article>" : "<p class=\"empty\">Roles unavailable.</p>")+"</div>"+partyGrid({estimated_subgroups:parties})+
      "</section>";
  }
  function enemyTraitFinding(item) {
    var percent=Number(item.observed_percent);
    var hasRate=item.observed_percent != null && Number.isFinite(percent) && percent>=0 && percent<=100 && Number(item.eligible_actor_appearances)>0;
    var mechanics=item.trait_description || item.evidence_skill_description || '';
    return '<div class="enemy-trait"><b class="trait-name">'+esc(item.trait)+'</b>'+
      '<span class="trait-context">'+esc(item.specialization)+' · '+esc(item.observed_skill)+'</span>'+
      (hasRate ? '<strong class="trait-rate">'+fmt(percent)+'% observed</strong>'+
        '<div class="trait-meter" role="meter" aria-label="'+esc(item.trait)+' observed" aria-valuemin="0" aria-valuemax="100" aria-valuenow="'+fmt(percent)+'"><i style="width:'+percent+'%"></i></div>'+
        '<small class="trait-count">'+fmt(item.actor_appearances)+' / '+fmt(item.eligible_actor_appearances)+' eligible appearances · detected / eligible · &gt;=15 seconds active</small>' :
        '<small class="trait-count">'+esc(traitAppearanceLabel(item))+'</small>')+
      (mechanics ? '<details class="trait-mechanics"><summary>Mechanics</summary><p>'+esc(mechanics)+'</p></details>' : '')+'</div>';
  }
  function enemyBuildEvidenceView(validation,selectedProfessions,scopeLabel) {
    if (scopeLabel && validation.status === "team_attribution_unavailable")
      return '<section class="enemy-build-evidence"><h2>Enemy Build Evidence</h2><p class="empty">Team-attributed evidence unavailable for '+esc(scopeLabel)+'. Legacy or unknown-team observations are not assigned by profession. All-opponents evidence is available only in the global view.</p></section>';
    var allowed=Array.isArray(selectedProfessions) ? new Set(selectedProfessions) : null;
    var rows=Object.keys(validation.professions || {}).map(function(profession){
      return {profession:profession,profile:validation.professions[profession]};
    }).filter(function(row){return (!allowed || allowed.has(row.profession)) && ((row.profile.traits || []).length || (row.profile.consumables || []).length);});
    if (!rows.length) return "<section class=\"enemy-build-evidence\"><div class=\"section-head\"><div><span class=\"eyebrow\">Observable build fingerprints</span><h2>Enemy Build Evidence</h2></div></div><p class=\"empty\">No observed build evidence.</p></section>";
    var hasConsumables=rows.some(function(row){return (row.profile.consumables || []).length;});
    var evidenceTitle=(scopeLabel || "All opponents")+" · "+(hasConsumables ? "Enemy Traits & Consumables" : "Observed Enemy Trait Procs");
    return "<section class=\"enemy-build-evidence\"><div class=\"section-head\"><div><span class=\"eyebrow\">Observable build fingerprints</span><h2>"+esc(evidenceTitle)+"</h2></div></div><div class=\"intel-grid\">"+
      rows.map(function(row){var traits=row.profile.traits || [],consumables=row.profile.consumables || [];
        return "<article class=\"intel-card\"><h3>"+professionInline(row.profession)+"</h3>"+
          (traits.length ? '<h4>Observed major traits</h4><div class="build-evidence-list enemy-trait-list">'+traits.map(enemyTraitFinding).join('')+'</div>' : '')+
          (consumables.length ? "<h4>Observed food / utility buffs</h4><div class=\"build-evidence-list\">"+consumables.map(function(item){return "<div><b>"+esc(item.name)+"</b><span>"+esc(item.classification)+((item.roles || []).length ? " · "+esc(item.roles.join(" / ")) : "")+"</span><small>Seen in "+fmt(item.actor_appearances)+" enemy appearance(s)</small></div>";}).join("")+"</div>" : "")+"</article>";
      }).join("")+"</div>"+(!hasConsumables ? "" : "")+"</section>";
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
    return '<details class="estimated-layout" open><summary>Estimated subgroup layout</summary>'+"<div class=\"evidence-key\"><span><i class=\"observed\"></i>Observed profession</span>" +
      "<span><i class=\"inferred\"></i>Inferred placement</span>" +
      (hasUnknown ? "<span><i class=\"unknown\"></i>Unknown profession</span>" : "") +
      (hasOpen ? "<span><i class=\"unknown\"></i>Open slot</span>" : "") +
      (coverage != null ? "<span><b>Profession coverage " + coverage + "%</b></span>" : "") +
      "</div>" +
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
            (roleKind === "unknown" ? "" : "<span class=\"role-badge role-" + roleKind + "\">" + roleGlyph(role)+esc(roleText) + "</span>")+"</" + tag + ">";
        }).join("");
        return "<article class=\"party\"><header><b>Subgroup " +
          fmt(partyNumber) + "</b>" + (confidenceVaries ? "<span>" +
          esc(confidenceLabel(party.confidence)) + " confidence</span>" : "") + "</header>" +
          "<div class=\"party-slots\" aria-label=\"Subgroup " + esc(partyNumber) +
          " five-player slots\">" + members + "</div></article>";
      }).join("") + "</div></details>";
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
        var unitsA=(first.tables[def[0]] || {}).metric_units || {},unitsB=(second.tables[def[0]] || {}).metric_units || {};
        var might=def[0] === "Uptimes" && def[1] === "might";
        var label=might && unitsA.might !== "percent" && unitsB.might !== "percent" ? "Might average stacks" : def[2];
        function display(value,units){return value == null ? "—" : might ? drillValue("might",value,{might:units.might || "stacks"}) : def[3] === "percent" ? fmt(value)+"%" : def[3] === "duration" ? humanDuration(value) : fmt(value);}
        var edge="Context only",aClass="",bClass="";
        if (def[4] && a != null && b != null) {
          if (a === b) edge="Even";
          else {
            var winner=a>b ? first : second,high=Math.max(a,b),low=Math.min(a,b);
            var countMetric=["downed","killed","appliedcrowdcontrol","interrupts","condicleanse","boonstrips","resurrects"].indexOf(def[1])>=0;
            edge=countMetric ? esc(winner.name)+" +"+fmt(high-low) : low === 0 ? fmt(high)+" vs 0 (relative change undefined)" : esc(winner.name)+" +"+fmt((high-low)/low*100)+"%";
            if (a>b) aClass=" compare-lead"; else bClass=" compare-lead";
          }
        }
        return "<div class=\"compare-stat\"><span class=\"compare-metric-label\">"+esc(label)+"</span><b class=\"compare-player-a"+aClass+"\">"+display(a,unitsA)+"</b><b class=\"compare-player-b"+bClass+"\">"+display(b,unitsB)+"</b><small class=\"compare-edge\">"+edge+"</small></div>";
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
    if (valueKey === "damage") title += " · share of listed damage";
    var rows=(skills || []).filter(function(row){return skillChartValue(row,valueKey)>0;})
      .slice().sort(function(a,b){return skillChartValue(b,valueKey)-skillChartValue(a,valueKey);});
    if (!rows.length) return "";
    var total=rows.reduce(function(sum,row){return sum+skillChartValue(row,valueKey);},0),top=rows.slice(0,7);
    var represented=top.reduce(function(sum,row){return sum+skillChartValue(row,valueKey);},0);
    if (represented < total) {var other={skill:"Other skills"};other[valueKey]=total-represented;top.push(other);}
    var payload=encodeURIComponent(JSON.stringify(top));
    return "<div class=\"skill-share\"><div class=\"skill-pie-visual\" data-skill-chart data-chart-title=\""+esc(title)+"\" data-chart-total=\""+total+"\" data-chart-value-key=\""+esc(valueKey)+"\" data-chart-unit=\""+esc(unitLabel)+"\" data-chart-skills=\""+esc(payload)+"\">"+skillChartSvg(top,total,false,true,valueKey,unitLabel)+"<button type=\"button\" class=\"open-skill-chart\">Open larger labeled chart</button></div><div><h4>"+esc(title)+"</h4><div class=\"skill-pie-legend\">"+top.map(function(row,index){return "<button type=\"button\" data-skill-slice=\""+index+"\"><b>"+esc(row.skill || "Unknown skill")+"</b><small>"+fmt(skillChartValue(row,valueKey)/total*100)+"%</small></button>";}).join("")+"</div></div></div>";
  }
  function comparisonSkillPanel(player) {
    var skills=player.skillDamage && player.skillDamage.skills || [];
    if (!skills.length) {
      var casts=Object.keys(player.evidence && player.evidence.skill_casts || {}).map(function(skill){
        return {skill:skill,count:Number(player.evidence.skill_casts[skill] || 0)};
      }).filter(function(row){return row.count>0;}).sort(function(a,b){return b.count-a.count;});
      if (!casts.length) return "<article class=\"compare-player-detail\"><h3>"+esc(player.name)+" · Skills</h3><p class=\"muted\">No detailed skill evidence is available for this squad player.</p></article>";
      var totalCasts=casts.reduce(function(total,row){return total+row.count;},0);
      return "<article class=\"compare-player-detail\"><h3>"+esc(player.name)+" · Skills</h3>"+skillSharePie(casts,"Cast share by skill","count","casts")+"<div class=\"compare-skill-list\">"+
        casts.slice(0,12).map(function(row,index){var share=totalCasts ? row.count/totalCasts*100 : 0;return "<div><span>"+(index+1)+". "+esc(row.skill)+"</span><b>"+fmt(row.count)+" casts<small>"+fmt(share)+"%</small></b></div>";}).join("")+"</div></article>";
    }
    var ordered=skills.slice().sort(function(a,b){return Number(b.damage || 0)-Number(a.damage || 0);});
    return "<article class=\"compare-player-detail\"><h3>"+esc(player.name)+" · Skills</h3>"+skillSharePie(ordered,"Share of listed damage")+"<div class=\"compare-skill-list\">"+ordered.slice(0,12).map(function(skill,index){return "<div><span>"+(index+1)+". "+esc(skill.skill)+"<small>"+fmt(skill.hits)+" hits · "+fmt(skill.down_contribution)+" down contribution</small></span><b>"+fmt(skill.damage)+"<small>"+fmt(skill.percent_of_total)+"% of all damage</small></b></div>";}).join("")+"</div></article>";
  }
  function comparisonEvidencePanel(player) {
    var evidence=player.evidence;
    if (!evidence) return "<article class=\"compare-player-detail\"><h3>"+esc(player.name)+" · Weapons / Rotation</h3><p class=\"muted\">No weapon or rotation data.</p></article>";
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
    function playerCard(player){return '<div class="compare-player-card">'+tablePlayer(player)+'</div>';}
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
      subpanel("dps", "overview", sectionHead("Damage", "DPS overview", "Compare totals and participation-normalized rates. Down contribution and damage to downed enemies measure different parts of the fight.") + metricGrid("damage") + bubbleChart("dps") + damageCompositionView() + highScoreGridExact(["Highest 1s Burst Damage","Highest Outgoing Skill Damage","Damage per Second"]), false) +
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
    var body =       reportHeading("Pro · fight review") + totalsCards(false) +
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
      "</section>" +
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
      ".enemy-build-evidence{margin:18px 0}.build-evidence-list{display:grid;gap:6px;margin:7px 0 13px}.build-evidence-list>div{padding:9px;border-left:3px solid var(--accent-2);border-radius:4px;background:var(--panel-2)}.build-evidence-list b,.build-evidence-list span,.build-evidence-list small{display:block}.build-evidence-list span{margin-top:2px;color:var(--accent);font-size:10px;font-weight:800}.build-evidence-list small,.build-evidence-list p{color:var(--muted);font-size:9px}.build-evidence-list p{margin:5px 0 0;line-height:1.4}.enemy-trait-list{gap:10px}.enemy-trait-list .enemy-trait{min-width:0;padding:12px;border-left:2px solid var(--accent-2);overflow-wrap:anywhere}.enemy-trait-list .trait-name{font-size:15px;line-height:1.4;color:var(--text)}.enemy-trait-list .trait-context,.enemy-trait-list .trait-count,.enemy-trait-list .trait-mechanics,.enemy-trait-list .trait-mechanics p{font-size:12px;line-height:1.5;color:var(--muted);font-weight:400}.enemy-trait-list .trait-rate{display:block;margin-top:8px;font-size:18px;color:var(--text);font-variant-numeric:tabular-nums}.enemy-trait-list .trait-meter{height:6px;margin:6px 0;border-radius:3px;background:color-mix(in srgb,var(--text) 10%,var(--panel));overflow:hidden}.enemy-trait-list .trait-meter i{display:block;height:100%;background:color-mix(in srgb,var(--accent-2) 55%,var(--panel));border-radius:inherit}.enemy-trait-list .trait-mechanics{margin-top:6px}.enemy-trait-list .trait-mechanics summary{cursor:pointer}.evidence-signal{padding:8px;border-left:3px solid var(--good);background:color-mix(in srgb,var(--good) 8%,var(--panel-2));font-size:11px}",
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
      "<title>Sparky Pro report</title><style>" + commonCss + extraCss + refreshCss +
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
      return "<div class=\"drill-fights\">" +
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
    var body="<div class=\"player-profile\" data-player-profile>" +
      "<div class=\"drill-player\">" + professionInline(identity.profession || identity.prof || "Unknown") +
      "<div><b>" + esc(requested) + "</b><small>" + esc(identity.account || "Account unavailable") +
      (participation ? " · " + humanDuration(participation) : "") + (fights != null ? " · " + fmt(fights) + " fights" : "") +
      "</small></div></div>";
    body += metricCards("Damage",damage,[["targetdamage","Damage to Enemy Players"],["targetdamageps","DPS"],["targetpower","Power Damage"],["targetpowerps","Power DPS"],["targetcondition","Condition Damage"],["targetconditionps","Condition DPS"],["targetbreakbardamage","Breakbar Damage"]]);
    body += metricCards("Healing & Barrier",heal,[["healing","Healing"],["healingps","Healing / sec"],["barrier","Barrier"],["barrierps","Barrier / sec"],["downedhealing","Downed-Ally Healing"],["downedhealingps","Downed Healing / sec"]]);
    body += metricCards("Support",support,[["condicleanse","Allied Conditions Cleansed"],["condicleanseself","Self-Cleansed Conditions"],["condicleansetime","Condition Duration Removed"],["boonstrips","Enemy Boons Removed"],["boonstripstime","Enemy Boon Duration Removed"],["resurrects","Resurrects"],["resurrecttime","Resurrection Time"]]);
    body += metricCards("Fight Impact",offense,[["downcontribution","Down-Contribution Damage"],["downed","Enemy Downs"],["killed","Enemy Kills"],["againstdowneddamage","Damage to Downed Enemies"],["appliedcrowdcontrol","Crowd Control Applied"],["interrupts","Interrupts"]]);
    body += metricCards("Key Boon Uptime",uptime,[["stability","Stability Uptime"],["protection","Protection Uptime"],["aegis","Aegis Uptime"],["resolution","Resolution Uptime"],["resistance","Resistance Uptime"],["might",(uptime && uptime.metric_units || {}).might === "percent" ? "Might Uptime" : "Might average stacks"]]);
    if (poison) body += "<section class=\"profile-group\"><h3>Poison Applications</h3><div class=\"drill-metrics\"><div><span>Applications</span><b>" + fmt(poison.apps) + "</b></div><div><span>Applications / min</span><b>" + fmt(poison.apps_per_min) + "</b></div><div><span>Applications / sec</span><b>" + fmt(poison.output) + "</b></div><div><span>Active Fight Time</span><b>" + humanDuration(Number(poison.fight_time || 0)) + "</b></div></div></section>";
    if (skillPlayer) body += "<section class=\"profile-group\"><h3>Damage by Skill</h3>" + skillSharePie(skillPlayer.skills || [],"Share of listed damage") + "<div class=\"drill-fights\">" + (skillPlayer.skills || []).slice(0,10).map(function(skill){return "<div class=\"drill-fight\"><div><b>" + esc(skill.skill) + "</b><small>" + fmt(skill.hits) + " hits · " + fmt(skill.damage_per_hit) + " damage/hit · " + fmt(skill.down_contribution) + " down contribution</small></div><span>" + fmt(skill.percent_of_total) + "% of all damage</span><strong>" + fmt(skill.damage) + "</strong></div>";}).join("") + "</div></section>";
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
    if (row) return "<div class=\"drill-feature\"><b>" + esc(label) + "</b></div>" +
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
    return "<div class=\"drill-kpis\"><div><span>Our sightings</span><b>" +
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
    Array.from(doc.querySelectorAll("thead button[data-sort-key]")).forEach(function(button){
      var th=button.closest("th");
      if (!th.hasAttribute("aria-sort")) th.setAttribute("aria-sort","none");
    });
    var popup=doc.createElement('div');popup.id='chart-popup';popup.setAttribute('role','tooltip');popup.hidden=true;doc.body.appendChild(popup);
    var tooltipSelector='[data-tooltip],.sparky-boon-bar,.multiboon-bar,.bubble-comparison-row,.metric-row,.damage-composition-row,[data-skill-slice]';
    function prepareCharts() {
      doc.querySelectorAll('.chart-tooltip').forEach(function(tip){var owner=tip.parentElement;if(owner.hasAttribute('title')) owner.removeAttribute('title');});
      doc.querySelectorAll('table').forEach(function(table){var row=table.querySelector('tbody tr');if(!row)return;Array.from(row.children).forEach(function(cell,index){if(cell.classList.contains('number'))table.querySelectorAll('thead tr').forEach(function(head){if(head.children[index])head.children[index].classList.add('numeric-header');});});});
    }
    prepareCharts();
    function responsiveMetrics() {
      doc.querySelectorAll('.metric-grid,.table-wrap table').forEach(function(table){
        var first=table.querySelector('tbody tr'),heads=table.querySelectorAll('thead tr:first-child th');
        if(!first || !heads.length || !first.querySelector('.table-player') || !table.getBoundingClientRect().width)return;
        var identity=Array.from(first.cells).findIndex(function(c){return !!c.querySelector('.table-player');});
        var primary=Array.from(first.cells).findIndex(function(c){return c.matches('[data-ranking-cell],[data-metric-cell]');});
        if(primary<0)primary=Array.from(first.cells).findIndex(function(c,i){return i>identity&&c.classList.contains('number');});
        if(primary<0)return;
        table.classList.add('responsive-metrics');table.classList.remove('responsive-condensed');
        var primaryColumns=Array.from(first.cells).map(function(c,i){return c.matches('[data-ranking-cell],[data-metric-cell="0"]') ? i : -1;}).filter(function(i){return i>=0;});
        if(!primaryColumns.length)primaryColumns=[primary];
        table.dataset.primaryCount=primaryColumns.length;
        function columnKind(i){return i===identity?'identity':primaryColumns.indexOf(i)>=0?'primary':i<identity?'rank':'extra';}
        var extras=[];
        Array.from(heads).forEach(function(h,i){var kind=columnKind(i);h.classList.add('responsive-'+kind);if(kind==='extra')extras.push(i);});
        Array.from(table.tBodies[0].rows).forEach(function(row){
          Array.from(row.cells).forEach(function(c,i){c.classList.add('responsive-'+columnKind(i));});
          var cell=row.cells[identity],details=cell.querySelector('.row-metric-details');
          if(!extras.length)return;
          if(!details){details=doc.createElement('details');details.className='row-metric-details';details.innerHTML='<summary>Details</summary><dl></dl>';details.addEventListener('click',function(e){e.stopPropagation();});cell.appendChild(details);}
          details.querySelector('dl').innerHTML=extras.map(function(i){var button=heads[i].querySelector('button'),key=button && button.getAttribute('data-sort-key'),raw=key && row.getAttribute('data-'+key),label=button && (button.dataset.detailLabel || button.title) || heads[i].textContent.trim();return '<dt>'+esc(label)+'</dt><dd>'+esc(raw!=null && /^m\d+-/.test(key)?fmt(finiteMetric(raw)):row.cells[i].textContent.trim())+'</dd>';}).join('');
        });
        if(table.scrollWidth>table.parentElement.clientWidth+1)table.classList.add('responsive-condensed');
      });
    }
    responsiveMetrics();
    doc.defaultView.addEventListener('resize',responsiveMetrics);
    doc.addEventListener('click',function(){queueMicrotask(responsiveMetrics);});
    function hidePopup(){popup.hidden=true;}
    function showPopup(event) {
      var target=event.target.closest && event.target.closest(tooltipSelector);
      if(!target){hidePopup();return;}
      var text=target.dataset.tooltip;
      if(!text && target.classList.contains('multiboon-bar')) {
        var row=target.closest('.multiboon-row');
        text=target.getAttribute('aria-label');
      }
      if(!text){var tip=target.querySelector('.chart-tooltip, title');text=tip ? tip.textContent : target.getAttribute('title') || target.dataset.drillBody || target.getAttribute('aria-label');if(tip && tip.tagName.toLowerCase()==='title')tip.remove();}
      if(!text){hidePopup();return;}
      target.dataset.tooltip=text;target.removeAttribute('title');
      var ancestor=target.parentElement.closest('[title]');if(ancestor && ancestor.matches('.multiboon-bar,.sparky-boon-bar'))ancestor.removeAttribute('title');
      popup.textContent=text;popup.hidden=false;
      var win=doc.defaultView,rect=target.getBoundingClientRect(),keyboard=event.type==='focusin';
      var x=keyboard ? rect.left+rect.width/2 : event.clientX,y=keyboard ? rect.bottom : event.clientY;
      var w=popup.offsetWidth,h=popup.offsetHeight;
      popup.style.left=Math.max(8,Math.min(x+14,win.innerWidth-w-8))+'px';
      popup.style.top=Math.max(8,Math.min(y+16+h>win.innerHeight ? y-h-14 : y+16,win.innerHeight-h-8))+'px';
    }
    doc.addEventListener('pointermove',showPopup);
    doc.addEventListener('focusin',showPopup);
    doc.addEventListener('pointerout',function(event){if(!event.relatedTarget || !event.relatedTarget.closest(tooltipSelector))hidePopup();});
    doc.addEventListener('focusout',hidePopup);
    doc.addEventListener('scroll',function(){
      var active=doc.activeElement;
      if(active && active.matches(tooltipSelector)) {
        var rect=active.getBoundingClientRect();
        if(rect.bottom>0 && rect.top<doc.defaultView.innerHeight) {
          doc.defaultView.requestAnimationFrame(function(){if(doc.activeElement===active)showPopup({target:active,type:'focusin'});});
          return;
        }
      }
      hidePopup();
    },true);
    doc.addEventListener('keydown',function(event){if(event.key==='Escape')hidePopup();});
    doc.addEventListener('click',function(){hidePopup();prepareCharts();});
    function highlightPoint(event) {
      var key=event.target.closest('[data-highlight-point]'),card=event.target.closest('[data-bubble-chart]');
      if (!key && doc.activeElement && doc.activeElement.matches('[data-highlight-point]')) {key=doc.activeElement;card=key.closest('[data-bubble-chart]');}
      if (!card) return;
      var svg=card.querySelector('.bubble-scatter');
      if (!svg) return;
      svg.classList.toggle('has-highlight',!!key);
      svg.querySelectorAll('[data-point-index]').forEach(function(point){
        var active=key && point.dataset.pointIndex===key.dataset.highlightPoint;
        point.classList.toggle('point-highlight',!!active);
        if(active) svg.appendChild(point.parentNode);
      });
    }
    doc.addEventListener('pointerover',highlightPoint);
    doc.addEventListener('focusin',highlightPoint);
    function clearPoint(event){
      if(!event.target.closest('[data-highlight-point]'))return;
      var card=event.target.closest('[data-bubble-chart]');
      if(card)card.querySelectorAll('.has-highlight,.point-highlight').forEach(function(el){el.classList.remove('has-highlight','point-highlight');});
    }
    doc.addEventListener('focusout',clearPoint);
    doc.addEventListener('pointerout',function(event){if(!event.relatedTarget || !event.relatedTarget.closest('[data-highlight-point]'))clearPoint(event);});
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
        var heading=sortButton.closest("th"),current=table.hasAttribute("data-metric-grid") ? sortButton.getAttribute("data-sort-direction") : heading && heading.getAttribute("aria-sort");
        var direction=current === "ascending" ? "descending" :
          current === "descending" ? "ascending" : "descending";
        rows.sort(function(a,b){
          if (table.hasAttribute("data-metric-grid")) return compareMetricValues(a.getAttribute("data-"+key),b.getAttribute("data-"+key),direction);
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
          "</div>" +
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
  function wallEnabled() {
    return !!(model && model.sparky_wall && model.sparky_wall.enabled === true);
  }
  function renderWall() {
    if (!wallEnabled()) return "";
    var players=model.sparky_wall.players || [];
    var previewNote=model.sparky_wall.preview === true ? (model.sparky_wall.demo === true ?
      "<p class=\"empty wall-preview\">Demo content — fictional players and sample callouts for reviewing this interface. Not historical commentary from this raid.</p>" :
      "<p class=\"empty wall-preview\"><b>Preview only.</b> This reference has no recorded AI commentary. This empty Wall demonstrates the optional view; real reports only show it when AI commentary is enabled. No quotes have been invented.</p>") : "";
    var demo=model.sparky_wall.preview === true && model.sparky_wall.demo === true;
    var body=previewNote+reportHeading(demo ? "Sparky · fictional demo commentary" : "Sparky · recorded AI commentary")+"<h2>Wall of Fame</h2><p class=\"wall-note\">"+
      (demo ? "Sample callouts for interface review. Counts demonstrate category mentions, not awards or wins. Select a fictional player to read the samples." :
      "Recorded fight comments.")+"</p>";
    body+=players.length ? players.map(function(player){
      var categories=Object.keys(player.categories || {}).sort().map(function(category){return "<span>"+esc(readableLabel(category))+" <b>"+fmt(player.categories[category])+"</b></span>";}).join("");
      return "<details class=\"wall-player\" data-wall-player=\""+esc(player.id)+"\"><summary><strong>"+esc(player.name)+"</strong><span>"+fmt(player.mentions)+" recorded mentions</span></summary><div class=\"wall-categories\" aria-label=\"Recorded category mention counts\">"+categories+"</div><div class=\"wall-comments\">"+(player.comments || []).map(function(comment){
        return "<article class=\"wall-comment\"><blockquote>"+esc(comment.text)+"</blockquote><footer><span>Fight ID: <code>"+esc(comment.fight_id || "Unavailable")+"</code></span>"+
          (comment.fight_timestamp ? "<span>Fight time: "+esc(reportTimestamp(comment.fight_timestamp,true))+"</span>" : "")+
          "<span>Recorded: "+esc(reportTimestamp(comment.timestamp,true) || "Unavailable")+"</span><span>Categories: "+esc((comment.categories || []).map(readableLabel).join(" · ") || "Uncategorized")+"</span></footer></article>";
      }).join("")+"</div></details>";
    }).join("") : "<p class=\"empty\">No recorded player mentions for the covered fights. Earlier commentary cannot be reconstructed.</p>";
    var css=".wall-note{max-width:800px;color:var(--muted)}.wall-player{margin:14px 0;border:1px solid var(--line);border-radius:10px;background:var(--panel)}.wall-player summary{display:flex;justify-content:space-between;gap:14px;padding:18px;cursor:pointer;list-style:disclosure-closed}.wall-player summary:focus-visible{outline:2px solid var(--accent)}.wall-player[open] summary{border-bottom:1px solid var(--line)}.wall-player summary strong{font-size:18px}.wall-player summary span{color:var(--muted)}.wall-categories{display:flex;gap:8px;flex-wrap:wrap;padding:16px 18px}.wall-categories span{padding:5px 10px;background:var(--panel-2);border:1px solid var(--line);border-radius:5px}.wall-categories b{margin-left:8px;color:var(--accent)}.wall-comments{padding:0 18px 18px}.wall-comment{padding:16px 0;border-top:1px solid var(--line)}.wall-comment blockquote{margin:0 0 14px;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65}.wall-comment footer{display:flex;flex-direction:column;gap:4px;color:var(--muted);font-size:12px;overflow-wrap:anywhere}.wall-comment code{font-size:11px}@media(max-width:520px){.wall-player summary{flex-direction:column}}";
    return "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Wall of Fame</title><style>"+commonCss+css+"</style></head><body><main class=\"wrap\">"+body+"</main></body></html>";
  }
  function documentFor(view) {
    if (!docs[view]) {
      docs[view] = view === "classic" ? classicHtml :
        (view === "simple" ? renderSimple() : view === "wall" ? renderWall() : renderSparky());
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
      (view === "simple" ? "Fast headline view" : view === "wall" ? "Saved AI fight commentary" : "Complete guided view");
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
    if (wallEnabled()) {
      views.push("wall");
      document.querySelector('[data-view="wall"]').hidden=false;
    }
    modelJson = "";
    classicHtml = await inflatePayload("classic-payload");

    var preferred = settings.defaultView;
    try {
      var remembered = localStorage.getItem("sparkybot-report-view");
      if (views.indexOf(remembered) >= 0) preferred = remembered;
    } catch (_error) {}
    var match = /[#&]view=(sparky|simple|classic|wall)/.exec(location.hash || "");
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


def _source_profession_colors(classic_html: str) -> dict[str, str]:
    """Read the report's exported palette as data, never execute source JavaScript."""
    colors: dict[str, str] = {}
    for store in re.findall(
        r'<script\b[^>]*type=[\"\']application/json[\"\'][^>]*>(.*?)</script>',
        classic_html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            tiddlers = json.loads(store)
        except (ValueError, TypeError):
            continue
        if not isinstance(tiddlers, list):
            continue
        for tiddler in tiddlers:
            if not isinstance(tiddler, dict):
                continue
            for literal in re.findall(r'\bconst\s+ProfessionColor\s*=\s*(\{[^{}]*\})\s*;',
                                      str(tiddler.get("text", ""))):
                try:
                    palette = json.loads(literal)
                except ValueError:
                    continue
                for profession, color in palette.items():
                    if (re.fullmatch(r'[A-Za-z]+', profession)
                            and isinstance(color, str)
                            and re.fullmatch(r'#[0-9A-Fa-f]{6}', color)):
                        colors.setdefault(profession.lower(), color)
    return colors


def build_switchable_report(
    classic_html: str,
    night_model: dict[str, Any],
    *,
    default_view: str = DEFAULT_REPORT_VIEW,
) -> str:
    """Embed Classic, Simple, Pro, and an AI-only Wall when its model is enabled."""
    default_view = normalize_report_view(default_view)
    source_colors = _source_profession_colors(classic_html)
    if source_colors:
        night_model = dict(night_model, profession_colors=source_colors)
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
