"""Wrap a baked report in a self-extracting compressed loader.

A baked TiddlyWiki report is ~99% tiddler-store JSON and compresses
roughly 4.7:1 with gzip. Packing the whole file as base64(gzip(html))
inside a small loader page keeps the report a single offline .html
(the Discord-download use case: no external scripts, nothing fetched)
while cutting it to about a third of its size.

The loader decompresses with the browser-native DecompressionStream
(Chrome/Edge 80+, Firefox 113+, Safari 16.4+) and rewrites the
document with the byte-identical original report, so rendering parity
is exact by construction.
"""

import base64
import binascii
import gzip
import html as html_escape
import re

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE)

_LOADER_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #1a1a2e; color: #ddd;
         display: flex; align-items: center; justify-content: center;
         height: 100vh; margin: 0; }}
  .msg {{ text-align: center; }}
  .err {{ color: #ff8080; max-width: 34em; }}
</style>
</head>
<body>
<div class="msg" id="m">Unpacking report&hellip;</div>
<script id="z" type="text/plain">{payload}</script>
<script>
(async function () {{
  var m = document.getElementById('m');
  try {{
    if (typeof DecompressionStream === 'undefined') {{
      throw new Error('This report needs a current browser (Chrome, Edge, Firefox or Safari from 2023 or newer).');
    }}
    // Decode inside a helper so the base64 text, the binary string and the
    // byte array all fall out of scope (and can be collected) as soon as the
    // stream is wired up. Kept as long-lived vars they would sit in memory
    // next to the unpacked report, tripling peak usage on big reports. The
    // payload element is emptied for the same reason.
    function payloadBlob() {{
      var el = document.getElementById('z');
      var bin = atob(el.textContent);
      var bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      el.textContent = '';
      return new Blob([bytes]);
    }}
    var stream = payloadBlob().stream().pipeThrough(new DecompressionStream('gzip'));
    var doc = await new Response(stream).text();
    document.open();
    document.write(doc);
    document.close();
  }} catch (e) {{
    m.className = 'msg err';
    m.textContent = 'Could not open the report: ' + e.message;
  }}
}})();
</script>
</body>
</html>
"""


def pack_html(html: str) -> str:
    """Return a self-extracting page whose payload is the given HTML."""
    title_m = _TITLE_RE.search(html)
    if title_m:
        # The <title> element of the source document already holds
        # entity-encoded text.  Decode it first, otherwise the escape below
        # encodes it a second time and the loader tab shows "&amp;" & co.
        title = html_escape.unescape(title_m.group(1).strip())
    else:
        title = "Combined Fight Log Summary"
    payload = base64.b64encode(
        gzip.compress(html.encode("utf-8"), 9)
    ).decode("ascii")
    return _LOADER_TEMPLATE.format(
        title=html_escape.escape(title), payload=payload
    )


def unpack_html(packed: str) -> str:
    """Inverse of pack_html (used by tests and tooling)."""
    m = re.search(
        r'<script id="z" type="text/plain">([A-Za-z0-9+/=]+)</script>', packed
    )
    if not m:
        raise ValueError("not a packed report")
    try:
        raw = base64.b64decode(m.group(1), validate=True)
        return gzip.decompress(raw).decode("utf-8")
    except (binascii.Error, EOFError, OSError, UnicodeDecodeError) as exc:
        # gzip.BadGzipFile is an OSError; a truncated stream raises EOFError.
        # Surface them all as one named condition instead of leaking codec
        # internals to the caller.
        raise ValueError("packed report payload is corrupt") from exc


def is_packed(html: str) -> bool:
    return '<script id="z" type="text/plain">' in html
