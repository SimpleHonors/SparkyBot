#!/usr/bin/env python3
"""Viewer-switcher report SPIKE generator (throwaway).

Proves/refutes: one self-contained .html = style tabs + ONE compressed data
payload + multiple skins (Classic TiddlyWiki in a blob iframe + a small
native Simple skin), viewer choice in localStorage, under Discord limits.

Payload = the real Top_Stats_Index.html viewer + SYNTHETIC fight tiddlers
sized like a big raid night (60 fights). Synthetic is stated everywhere.
"""
import base64, gzip, json, random, sys, time
from pathlib import Path

SBROOT = "/tmp/claude-0/-root/2c233635-e4bd-40c9-95f5-e97d393d7637/scratchpad/sbwt-welcome"
sys.path.insert(0, SBROOT)
from core.report_bake import _append_tiddler_block  # noqa: E402

VIEWER = Path("/mnt/projects/ai_toolbox/SparkyBot/dev/Top_Stats_Index.html")
OUT_DIR = Path("/mnt/projects/ai_toolbox/SparkyBot-releases/previews")
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


def main():
    viewer = VIEWER.read_text(encoding="utf-8", errors="replace")
    tids = fight_tiddlers(N_FIGHTS)
    tid_json = json.dumps(tids)
    report_html = _append_tiddler_block(viewer, tids)

    raw = report_html.encode("utf-8")
    payload = base64.b64encode(gzip.compress(raw, 9)).decode("ascii")

    simple_js = r"""
function extractStore(html){
  var opener='<script class="tiddlywiki-tiddler-store" type="application/json">';
  var i=html.lastIndexOf(opener); if(i<0) return null;
  var j=html.indexOf('<\/script>', i);
  var txt=html.slice(i+opener.length, j);
  return JSON.parse(txt);
}
function esc(s){var d=document.createElement('i');d.textContent=s;return d.innerHTML;}
function renderSimple(html){
  var tids=extractStore(html)||[];
  var fights=tids.filter(function(t){return /_Overview$/.test(t.title);});
  var boards=tids.filter(function(t){return /_Top_/.test(t.title);});
  var h=['<!doctype html><meta charset=utf-8><title>Simple skin</title>',
    '<style>body{font-family:system-ui;background:#101720;color:#e8eef4;margin:16px}',
    'h1{color:#4c8ed9}table{border-collapse:collapse;margin:8px 0}',
    'td,th{border:1px solid #2a3a4c;padding:2px 8px;font-size:13px}',
    'th{background:#1a2430;position:sticky;top:0}</style>',
    '<h1>Combined Fight Log Summary — Simple skin (SYNTHETIC spike data)</h1>',
    '<p>', fights.length, ' fights. Same payload as Classic, different everything.</p>'];
  h.push('<h2>Fights</h2><table><tr><th>#</th><th>First line</th></tr>');
  fights.forEach(function(f,i){
    h.push('<tr><td>'+(i+1)+'</td><td>'+esc(f.text.split('\n')[1]||'')+'</td></tr>');});
  h.push('</table>');
  boards.slice(0,2).forEach(function(b){
    h.push('<h2>'+esc(b.title.replace(/^\d+_/,'').replace(/_/g,' '))+'</h2><table>');
    b.text.split('\n').slice(0,11).forEach(function(r){
      if(r.charAt(0)!=='|')return;
      var cells=r.replace(/\|h$/,'').split('|').filter(Boolean);
      h.push('<tr>'+cells.map(function(c){return '<td>'+esc(c.replace(/^!/,''))+'</td>';}).join('')+'</tr>');});
    h.push('</table>');});
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
 <span id=meta>SYNTHETIC spike · payload %PAYLOAD_MB%MB packed</span>
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
     'SYNTHETIC spike · decompress '+times.decompress+'ms · skin build '+(times[skin]||0)+'ms';
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
        "synthetic_tiddler_json_bytes": len(tid_json),
        "report_html_bytes": len(raw),
        "gzip_payload_b64_bytes": len(payload),
        "shell_chrome_bytes": len(shell) - len(payload),
        "total_shell_bytes": len(shell),
        "fights": N_FIGHTS,
        "tiddlers": len(tids),
    }
    for k, v in stats.items():
        print(f"{k}: {v/1e6:.2f}MB" if v > 10000 else f"{k}: {v}")
    print(f"vs 10MB Discord free limit: {'UNDER' if len(shell) <= 10_000_000 else 'OVER'}")
    print(f"vs 8MiB legacy limit:      {'UNDER' if len(shell) <= 8*1024*1024 else 'OVER'}")


if __name__ == "__main__":
    main()
