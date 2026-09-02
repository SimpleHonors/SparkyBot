#!/usr/bin/env python3
"""Viewer-switcher report SPIKE generator (throwaway).

Proves/refutes: one self-contained .html = style tabs + ONE compressed data
payload + multiple skins (Classic TiddlyWiki in a blob iframe + a small
native Simple skin), viewer choice in localStorage, under Discord limits.

Payload = the real Top_Stats_Index.html viewer + SYNTHETIC fight tiddlers
sized like a big raid night (60 fights). Synthetic is stated everywhere.
"""
import base64, gzip, json, os, random, sys, time
from pathlib import Path

SBROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SBROOT))
from core.report_bake import _append_tiddler_block  # noqa: E402

VIEWER = Path(os.environ.get(
    "SPARKYBOT_VIEWER_HTML", SBROOT / "dev" / "Top_Stats_Index.html"
))
OUT_DIR = Path(os.environ.get(
    "SPARKYBOT_PREVIEW_DIR", SBROOT / "dist" / "viewer-previews"
))
DATE = "20260830"
N_FIGHTS = 60
random.seed(42)

PLAYERS = [f"Player{i:02d}.{random.randint(1000,9999)}" for i in range(50)]
PROFS = ["Guardian", "Necromancer", "Elementalist", "Revenant", "Engineer",
         "Warrior", "Mesmer", "Ranger", "Thief"]


def fight_tiddlers(n):
    tids = []
    for f in range(1, n + 1):
        squad = random.randint(25, 50)
        rows = []
        for p in random.sample(PLAYERS, squad):
            rows.append(
                f"|{p}|{random.choice(PROFS)}|{random.randint(1,10)}|"
                f"{random.randint(50_000, 900_000)}|{random.randint(0,40)}|"
                f"{random.randint(0,900_000)}|{random.randint(0,60)}|"
                f"{random.randint(0,25)}|{random.randint(0,9)}|"
            )
        table = ("|!Name|!Profession|!Party|!Damage|!Strips|!Healing|"
                 "!Cleanses|!Downs|!Deaths|h\n" + "\n".join(rows))
        base = f"{DATE}_Fight_{f:02d}"
        tids.append({"title": f"{base}_Overview",
                     "text": f"!!Fight {f} — SYNTHETIC spike data\n"
                             f"Squad {squad} vs {random.randint(20,70)} enemies, "
                             f"{random.randint(60,600)}s\n\n{table}",
                     "tags": "fight-overview"})
        for sec in ("Damage_Output_Review", "Support_Review", "Defense_Review"):
            tids.append({"title": f"{base}_{sec}",
                         "text": f"!!{sec.replace('_',' ')} (synthetic)\n\n{table}",
                         "tags": "fight-detail"})
    # night-level leaderboards
    for stat in ("Damage", "Strips", "Cleanses", "Healing", "Stability",
                 "Downs_Contribution"):
        rows = "\n".join(
            f"|{p}|{random.choice(PROFS)}|{random.randint(1_000_000, 40_000_000)}|"
            for p in random.sample(PLAYERS, 25))
        tids.append({"title": f"{DATE}_Top_{stat}",
                     "text": f"|!Name|!Profession|!Total|h\n{rows}",
                     "tags": "leaderboard"})
    return tids


REAL_REPORT = Path(__file__).parent / "real_report_fixed.html"


def main():
    # Byte-identical CURRENT production report (2026-07-18, poison template
    # included) — nothing injected, nothing modified.
    report_html = REAL_REPORT.read_text(encoding="utf-8", errors="replace")
    tid_json = ""
    raw = report_html.encode("utf-8")
    payload = base64.b64encode(gzip.compress(raw, 9)).decode("ascii")

    simple_js = r"""
function extractStores(html){
  var opener='<script class="tiddlywiki-tiddler-store" type="application/json">';
  var all=[], i=0;
  while(true){ i=html.indexOf(opener,i); if(i<0)break;
    var j=html.indexOf('<\/script>', i);
    try{ all=all.concat(JSON.parse(html.slice(i+opener.length,j))); }catch(e){}
    i=j; }
  return all;
}
function esc(s){var d=document.createElement('i');d.textContent=s;return d.innerHTML;}
function strip(c){return c.replace(/<[^>]+>/g,'').replace(/\{\{[^}]*\}\}/g,'').replace(/[!\s]+/g,' ').trim();}
function fmt(v){return (v%1)?v.toFixed(1):v.toLocaleString();}
function renderSimple(html){
  var tids=extractStores(html);
  function find(sfx){for(var k=tids.length-1;k>=0;k--){var t=tids[k];
    if(t.title&&t.title.indexOf(sfx)>=0&&t.title.charAt(0)!=='$')return t;}return null;}
  function wcells(s){return s.replace(/\[img[^\]]*?\[([^\]\[]+)\|[^\]]*?\]\]/g,'{$1}');}
  function findEnd(sfx){for(var k=tids.length-1;k>=0;k--){var t=tids[k];
    if(!t.title||t.title.charAt(0)==='$')continue;
    if(!t.title.endsWith(sfx))continue;
    if(sfx.charAt(0)==='-'&&!/[0-9]/.test(t.title.charAt(t.title.length-sfx.length-1)))continue;
    return t;}return null;}

function metricBoard(sfx, token, label){
  // Headline support board: parse the named source table's metric column
  // (same normalization as core/night_model._norm_header), sort by value
  // desc, render top 10 as rank/Player/Class/value.
  var t=findEnd(sfx);
  if(!t)return '';
  var lines=t.text.split('\n'), col=-1;
  var norm=function(c){var m=/\[img[^\]]*?\[([^|\]]+)\|/.exec(c);
    return (m?m[1]:c).replace(/[^A-Za-z0-9]/g,'').toLowerCase();};
  lines.forEach(function(L0){
    var L=L0.replace(/\s+$/,'');
    if(col>=0||L.charAt(0)!=='|'||!/\|[hk]$/.test(L))return;
    var hc=wcells(L.replace(/\|[hk]$/,'')).split('|').slice(1);
    for(var q=0;q<hc.length;q++){if(norm(hc[q])===token){col=q;break;}}
  });
  if(col<0)return '';
  var rows=[];
  lines.forEach(function(L0){
    var L=L0.replace(/\s+$/,'');
    if(L.charAt(0)!=='|'||/\|[hkcf]$/.test(L))return;
    var cells=wcells(L).split('|').slice(1,-1);
    if(cells.length<=col)return;
    var val=parseFloat(cells[col].replace(/,/g,'').replace(/%$/,''));
    if(!isFinite(val))return;
    var nm='',prof='',acc=null,tip=/data-tooltip=['"]([^'"]+)['"]/.exec(L);
    if(tip)acc=tip[1];
    for(var q=1;q<cells.length;q++){var s=strip(cells[q]);
      if(s&&!/^[0-9.,%]+$/.test(s)&&/[A-Za-z]/.test(s)){nm=s;break;}}
    for(var q=0;q<cells.length;q++){
      var m2=/\{\{\s*([A-Za-z][A-Za-z0-9 ]*?)\s*\}\}/.exec(cells[q]);
      if(m2){prof=m2[1].trim();break;}}
    if(!nm||/\b(average|totals)\b/i.test(nm))return;
    rows.push([nm,acc,prof,val]);
  });
  var best={},order=[];
  rows.forEach(function(r){var k=r[1]||r[0];
    if(!(k in best)){best[k]=r;order.push(k);}
    else if(r[3]>best[k][3])best[k]=r;});
  rows=order.map(function(k){return best[k];});
  if(!rows.length)return '';
  rows.sort(function(a,b){return b[3]-a[3];});
  rows=rows.slice(0,10);
  var h=['<div class=board><h2>'+esc(label)+'</h2><table>',
    '<tr><th>#</th><th>Player</th><th>Class</th><th class=n>'+esc(label)+'</th></tr>'];
  rows.forEach(function(r,i){
    h.push('<tr><td class=n>'+(i+1)+'</td><td>'+esc(r[0])+
      (r[1]?' <i>'+esc(r[1])+'</i>':'')+'</td><td>'+esc(r[2])+
      '</td><td class=n>'+esc(fmt(r[3]))+'</td></tr>');});
  h.push('</table></div>'); return h.join('');
}

  var tag=find('-Tag_Stats'), summary=find('-Log-Summary');
  // headline numbers from the Tag_Stats "Totals" row
  var stats=null;
  if(tag)tag.text.split('\n').some(function(line){
    if(line.indexOf('|Totals')!==0)return false;
    var c=line.replace(/\|f$/,'|').split('|').slice(1,-1).map(strip).filter(function(x){return x!=='<';});
    stats={fights:c[1],edowns:c[2],ekills:c[3],adowns:c[4],adeaths:c[5],kdr:c[6]};
    return true;});
  var h=['<!doctype html><meta charset=utf-8><title>Fight Log Summary</title>',
    '<style>body{font-family:system-ui;background:#101720;color:#e8eef4;margin:24px auto;max-width:1150px;padding:0 20px}',
    'h1{color:#e8eef4;font-size:24px;margin-bottom:2px}p.sub{color:#8ca0b3;margin-top:0}',
    '.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}',
    '.card{background:#1a2430;border:1px solid #2a3a4c;border-radius:6px;padding:14px 16px}',
    '.card .v{font-size:26px;font-weight:700;color:#7fb3e8}.card .l{font-size:12px;color:#8ca0b3;margin-top:2px}',
    '.card.good .v{color:#5fd38f}.card.bad .v{color:#e08585}',
    '.boards{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px}',
    '.board h2{color:#e8eef4;font-size:15px;margin:0 0 6px}',
    'table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #22303e;padding:4px 8px;font-size:13px;text-align:left}',
    'th{color:#8ca0b3;font-weight:600}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}',
    'p.dim{color:#8ca0b3;font-size:13px;margin-top:26px}</style>'];
  h.push('<h1>Combined Fight Log Summary</h1>');
  if(summary)h.push('<p class=sub>'+esc((summary.caption||'').replace(/\bNone\b/g,'').replace(/\s*-\s*-\s*/g,' \u2014 ').replace(/\s+/g,' ').trim())+'</p>');
  if(stats){h.push('<div class=cards>',
    '<div class=card><div class=v>'+esc(stats.fights)+'</div><div class=l>Fights</div></div>',
    '<div class="card good"><div class=v>'+esc(stats.ekills)+'</div><div class=l>Enemies Killed</div></div>',
    '<div class="card good"><div class=v>'+esc(stats.edowns)+'</div><div class=l>Enemies Downed</div></div>',
    '<div class="card bad"><div class=v>'+esc(stats.adeaths)+'</div><div class=l>Our Deaths</div></div>',
    '<div class="card bad"><div class=v>'+esc(stats.adowns)+'</div><div class=l>Our Downs</div></div>',
    '<div class=card><div class=v>'+esc(stats.kdr)+'</div><div class=l>K/D Ratio</div></div>',
    '</div>');}
  h.push('<div class=boards>');
  h.push(metricBoard('-Support-Summary','condicleanse','Cleanses'));
  h.push(metricBoard('-Support-Summary','boonstrips','Boon Strips'));
  h.push(metricBoard('-Heal-Stats','healing','Healing'));
  h.push(metricBoard('-Support-Summary','resurrects','Revives'));
  h.push(metricBoard('-Offensive-Summary','downed','Enemy Downs'));
  h.push(metricBoard('-Offensive-Summary','downcontribution','Down Contribution'));
  h.push(metricBoard('-Offensive-Summary','killed','Enemy Kills'));
  h.push(metricBoard('-Uptimes','stability','Stability Uptime %'));
  h.push('</div>');
  h.push('<p class=dim>Headline view. Switch to Classic for every table, chart, and the Poison Coverage report.</p>');
  return h.join('');
}
"""

    shell = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>SparkyBot Report Spike — viewer picks the style</title>
<style>
 body{margin:0;font-family:system-ui;background:#16211f;color:#eaf2f0;display:flex;flex-direction:column;height:100vh}
 #bar{display:flex;gap:8px;align-items:center;padding:8px 12px;background:#101917;border-bottom:1px solid #32463f}
 button{background:#22312e;color:#eaf2f0;border:1px solid #32463f;border-radius:3px;padding:6px 16px;cursor:pointer}
 button.on{background:#3fa08f;color:#fff;border-color:#2f8172}
 #meta{margin-left:auto;font-size:12px;color:#93a8a3}
 iframe{flex:1;border:0;width:100%%;background:#fff}
 #load{padding:40px;text-align:center}
</style></head><body>
<div id=bar>
 <strong>Report style:</strong>
 <button id=b_classic>Classic</button>
 <button id=b_simple>Simple</button>
 <span id=meta>real report 2026-07-18 · payload %PAYLOAD_MB%MB packed</span>
</div>
<div id=load>Unpacking report data…</div>
<iframe id=frame hidden></iframe>
<script id=z type=text/plain>%PAYLOAD%</script>
<script>
%SIMPLE_JS%
(async function(){
 var t0=performance.now();
 var el=document.getElementById('z');
 var bin=atob(el.textContent); el.textContent='';
 var bytes=new Uint8Array(bin.length);
 for(var i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);
 var ds=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
 var html=await new Response(ds).text();
 var tDec=Math.round(performance.now()-t0);
 var docs={}, times={decompress:tDec};
 // file:// pages have an opaque origin, so blob: URLs mint as blob:null and
 // Chrome refuses them as iframe sources. srcdoc is origin-safe everywhere
 // and handles multi-MB documents fine.
 function docFor(skin){
   if(docs[skin])return docs[skin];
   var s0=performance.now();
   docs[skin] = skin==='classic' ? html : renderSimple(html);
   times[skin]=Math.round(performance.now()-s0);
   return docs[skin];
 }
 var frame=document.getElementById('frame');
 function show(skin){
   frame.srcdoc=docFor(skin); frame.hidden=false;
   document.getElementById('load').hidden=true;
   document.getElementById('b_classic').className=skin==='classic'?'on':'';
   document.getElementById('b_simple').className=skin==='simple'?'on':'';
   try{localStorage.setItem('sb-report-skin',skin);}catch(e){}
   document.getElementById('meta').textContent=
     'real report 2026-07-18 · decompress '+times.decompress+'ms · skin build '+(times[skin]||0)+'ms';
 }
 document.getElementById('b_classic').onclick=function(){show('classic');};
 document.getElementById('b_simple').onclick=function(){show('simple');};
 // Priority: #skin= hash (runner-forced default / deep link) > the
 // viewer's own remembered choice > Classic.
 var pref='classic';
 try{pref=localStorage.getItem('sb-report-skin')||'classic';}catch(e){}
 var m=/[#&]skin=([a-z]+)/.exec(location.hash||'');
 if(m)pref=m[1];
 show(pref==='simple'?'simple':'classic');
})();
</script></body></html>"""

    payload_mb = f"{len(payload)/1e6:.1f}"
    shell = (shell.replace("%SIMPLE_JS%", simple_js)
                  .replace("%PAYLOAD%", payload)
                  .replace("%PAYLOAD_MB%", payload_mb))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "spike-viewer-switcher.html"
    out.write_text(shell, encoding="utf-8")

    stats = {
        "viewer_bytes": VIEWER.stat().st_size,
        
        "report_html_bytes": len(raw),
        "gzip_payload_b64_bytes": len(payload),
        "shell_chrome_bytes": len(shell) - len(payload),
        "total_shell_bytes": len(shell),
        
        
    }
    for k, v in stats.items():
        print(f"{k}: {v/1e6:.2f}MB" if v > 10000 else f"{k}: {v}")
    print(f"vs 10MB Discord free limit: {'UNDER' if len(shell) <= 10_000_000 else 'OVER'}")
    print(f"vs 8MiB legacy limit:      {'UNDER' if len(shell) <= 8*1024*1024 else 'OVER'}")


if __name__ == "__main__":
    main()
