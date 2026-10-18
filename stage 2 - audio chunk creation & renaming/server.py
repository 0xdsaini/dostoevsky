#!/usr/bin/env python3
"""
Local audiobook chunk board for The Idiot.

Run from your project folder:
    python audiobook_board.py

Reads:
    manifest.csv
    text_chunks/...
Writes uploaded MP3s to:
    audio_in/<expected_audio_filename>

Binds only to 127.0.0.1. Nothing is uploaded to a remote service.
"""

import csv
import json
import mimetypes
import os
import re
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.csv"
AUDIO_DIR = ROOT / "audio_in"
HOST = "127.0.0.1"
PORT = 8765
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Idiot — Audio Chunk Board</title>
<style>
:root, [data-theme="dark"] {
  color-scheme: dark;
  --bg:#101114; --panel:#191b21; --panel2:#20232b; --line:#343844;
  --text:#f1f2f5; --muted:#a4a9b6; --accent:#b7c8ff; --good:#8de0b0;
  --warn:#ffce83; --bad:#ff9a9a; --header:#101114ed; --textbox:#e1e4ec;
  --dropbg:#15171c; --dragbg:#222735; --inputbg:#121318; --activebg:#282d3a;
}
[data-theme="light"] {
  color-scheme: light;
  --bg:#f4f5f8; --panel:#ffffff; --panel2:#f0f2f7; --line:#d7dbe5;
  --text:#20232b; --muted:#626a79; --accent:#465fc5; --good:#18794e;
  --warn:#996000; --bad:#b42318; --header:#f4f5f8ed; --textbox:#272c36;
  --dropbg:#fafbfe; --dragbg:#edf0ff; --inputbg:#ffffff; --activebg:#e8ecff;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,sans-serif}
button,input{font:inherit}
button{cursor:pointer;border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:10px;padding:9px 13px}
button:hover{border-color:var(--accent)}
button:disabled{opacity:.45;cursor:wait}
header{position:sticky;top:0;z-index:5;background:var(--header);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:12px 18px}
.top{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
h1{font-size:18px;margin:0}
.small{color:var(--muted);font-size:12px}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
main{max-width:1100px;margin:auto;padding:18px}
.progress-wrap{height:6px;background:#2c3039;border-radius:99px;overflow:hidden;margin-top:12px}
#progress{height:100%;width:0;background:var(--good);transition:width .2s}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:16px}
.card,.sidecard{border:1px solid var(--line);background:var(--panel);border-radius:16px;overflow:hidden}
.card-head{padding:18px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:flex-start;justify-content:space-between}
.kicker{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
h2{margin:3px 0 0;font-size:21px}
.badge{white-space:nowrap;border-radius:99px;padding:5px 9px;font-size:12px;background:#30343e;color:var(--muted)}
.badge.done{background:#183a2b;color:var(--good)}
.badge.empty{background:#3a3020;color:var(--warn)}
.textbox{padding:18px;max-height:44vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.75;color:var(--textbox)}
.text-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:0 18px 18px}
.text-actions audio{width:min(100%,480px);height:40px}
#jump{background:var(--inputbg)}
.upload-area{margin:0 18px 18px;border:1px dashed var(--line);border-radius:13px;padding:20px;text-align:center;background:var(--dropbg)}
.upload-area.drag{border-color:var(--accent);background:var(--dragbg)}
.upload-area strong{display:block;margin-bottom:5px}
.upload-area input{display:none}
.upload-actions{display:flex;justify-content:center;gap:8px;flex-wrap:wrap;margin-top:12px}
.file-name{font-size:12px;color:var(--muted);overflow-wrap:anywhere;margin-top:8px}
.statusline{padding:0 18px 18px;color:var(--muted);font-size:13px;min-height:25px}
.side{display:flex;flex-direction:column;gap:12px}
.sidecard{padding:15px}
.sidecard h3{font-size:14px;margin:0 0 10px}
.big{font-size:26px;font-weight:700}
.navrow{display:flex;gap:8px}
.navrow button{flex:1}
#jump{width:100%;margin-top:8px;border:1px solid var(--line);color:var(--text);padding:9px;border-radius:9px}
.chunklist{max-height:300px;overflow:auto;display:flex;flex-direction:column;gap:5px}
.chunkitem{display:flex;gap:8px;align-items:center;text-align:left;width:100%;padding:8px;border-radius:8px;font-size:12px}
.chunkitem.active{border-color:var(--accent);background:var(--activebg)}
.dot{width:8px;height:8px;border-radius:50%;background:#656b78;flex-shrink:0}
.dot.done{background:var(--good)}
.note{color:var(--muted);font-size:12px}
#toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:#292d36;border:1px solid var(--line);padding:10px 16px;border-radius:10px;display:none;z-index:10;max-width:90vw}
@media(max-width:800px){.layout{grid-template-columns:1fr}.textbox{max-height:40vh}.side{order:-1}.chunklist{max-height:150px}}
</style>
</head>
<body>
<header>
 <div class="top">
  <div><h1>The Idiot — Audio Chunk Board</h1><div class="small">Local-only board · MP3s are saved to your project folder</div></div>
  <div class="toolbar">
   <button id="themeToggle" title="Switch light/dark mode">☀ Light mode</button>
   <button id="prev">← Previous</button>
   <span id="counter" class="small">Loading…</span>
   <button id="next">Next →</button>
  </div>
 </div>
 <div class="progress-wrap"><div id="progress"></div></div>
</header>
<main>
 <div class="layout">
  <section class="card">
   <div class="card-head">
    <div><div id="chapterLabel" class="kicker">Chunk</div><h2 id="chunkTitle">Loading chunks…</h2></div>
    <span id="badge" class="badge">Loading</span>
   </div>
   <div id="text" class="textbox">Loading the chunk text…</div>
   <div class="text-actions">
    <button id="copyText">Copy chunk text</button>
    <audio id="player" controls preload="metadata" style="display:none" aria-label="Play this chunk's uploaded audio"></audio>
    <span id="playHint" class="note">No audio uploaded for this chunk yet.</span>
   </div>
   <div id="drop" class="upload-area">
    <strong>Drop an MP3 here, or choose a file</strong>
    <div class="note">It will be assigned to this chunk and saved with the expected filename.</div>
    <input id="file" type="file" accept="audio/mpeg,.mp3,audio/*">
    <div class="upload-actions">
     <button id="choose">Choose audio</button>
     <button id="clear" title="Clear the current file selection">Clear selection</button>
    </div>
    <div id="selected" class="file-name">No file selected</div>
   </div>
   <div id="statusline" class="statusline"></div>
  </section>
  <aside class="side">
   <div class="sidecard">
    <h3>Overall progress</h3>
    <div class="big" id="doneCount">—</div>
    <div class="note" id="remaining">chunks uploaded</div>
    <div class="note" style="margin-top:8px">Use ← and → keys to move between chunks. You can upload to any chunk in any order.</div>
   </div>
   <div class="sidecard">
    <h3>Jump to a chunk</h3>
    <input id="jump" type="search" placeholder="Search chapter or filename…">
    <div class="chunklist" id="chunklist"></div>
   </div>
   <div class="sidecard">
    <h3>Uploaded audio</h3>
    <div id="audioStatus" class="note">No file uploaded for this chunk.</div>
    <button id="remove" style="margin-top:10px;display:none">Remove uploaded audio</button>
   </div>
  </aside>
 </div>
 <p class="note" style="margin:16px 2px">Uploads are handled by this local Python server. Nothing is sent to a cloud service. Existing files are never overwritten; if a chunk already has audio, remove it first or choose another chunk.</p>
</main>
<div id="toast"></div>
<script>
let chunks=[], current=0, selectedFile=null, busy=false;
const $=id=>document.getElementById(id);
const savedTheme=localStorage.getItem('idiot-board-theme')||'dark';
document.documentElement.dataset.theme=savedTheme;
function updateThemeButton(){ $('themeToggle').textContent=document.documentElement.dataset.theme==='dark'?'☀ Light mode':'☾ Dark mode'; }
function toast(msg){$('toast').textContent=msg;$('toast').style.display='block';setTimeout(()=>$('toast').style.display='none',3000)}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function currentChunk(){return chunks[current]}
function uploaded(c){return !!c.uploaded}
function render(){
 if(!chunks.length)return;
 const c=currentChunk();
 $('counter').textContent=`${current+1} / ${chunks.length}`;
 $('chapterLabel').textContent=`Part ${c.part} · Chapter ${c.chapter_roman} · Chunk ${c.chunk}/${c.total_chunks}`;
 $('chunkTitle').textContent=c.expected_audio_filename;
 $('text').textContent=c.text;
 $('badge').textContent=uploaded(c)?'MP3 uploaded':'Awaiting MP3';
 $('badge').className='badge '+(uploaded(c)?'done':'empty');
 $('statusline').textContent=uploaded(c)?`Saved as ${c.expected_audio_filename}`:'No audio uploaded for this chunk yet.';
 $('audioStatus').textContent=uploaded(c)?c.expected_audio_filename:'No file uploaded for this chunk.';
 $('player').style.display=uploaded(c)?'block':'none';
 $('playHint').style.display=uploaded(c)?'none':'inline';
 $('remove').style.display=uploaded(c)?'inline-block':'none';
 if(uploaded(c)){
   const url='/audio/'+encodeURIComponent(c.expected_audio_filename)+'?v='+c.mtime;
   if($('player').dataset.src!==url){$('player').pause();$('player').src=url;$('player').dataset.src=url;$('player').load()}
 } else {$('player').pause();$('player').removeAttribute('src');$('player').dataset.src='';$('player').load()}
 $('doneCount').textContent=`${chunks.filter(uploaded).length} / ${chunks.length}`;
 $('remaining').textContent=`${chunks.length-chunks.filter(uploaded).length} chunks still empty`;
 $('progress').style.width=(chunks.filter(uploaded).length/chunks.length*100)+'%';
 renderList();
}
function renderList(){
 const q=$('jump').value.toLowerCase();
 const list=$('chunklist');list.innerHTML='';
 chunks.forEach((c,i)=>{
   const label=`${c.chapter_title} · chunk ${c.chunk} · ${c.expected_audio_filename}`;
   if(q&&!label.toLowerCase().includes(q))return;
   const b=document.createElement('button');
   b.className='chunkitem'+(i===current?' active':'');
   b.innerHTML=`<span class="dot ${uploaded(c)?'done':''}"></span><span>${esc(label)}</span>`;
   b.onclick=()=>{current=i;selectedFile=null;$('file').value='';$('selected').textContent='No file selected';render()};
   list.appendChild(b);
 });
}
function go(delta){if(!chunks.length)return;current=(current+delta+chunks.length)%chunks.length;selectedFile=null;$('file').value='';$('selected').textContent='No file selected';render()}
async function load(){
 const r=await fetch('/api/chunks');const d=await r.json();chunks=d.chunks||[];
 if(!chunks.length){$('chunkTitle').textContent='No chunks found';$('text').textContent='Check that manifest.csv and text_chunks/ are in the same folder as the server script.';return}
 render();
}
async function upload(file){
 if(!file||busy)return;
 const c=currentChunk();
 if(uploaded(c)){toast('This chunk already has an MP3. Remove it first.');return}
 if(!file.name.toLowerCase().endsWith('.mp3')&&!file.type.startsWith('audio/')){
   toast('Choose an audio file (preferably MP3).');return
 }
 busy=true;$('statusline').textContent='Uploading and saving…';$('drop').style.opacity='.65';
 const form=new FormData();form.append('file',file);form.append('chunk_id',c.id);
 try{
   const r=await fetch('/api/upload',{method:'POST',body:form});
   const d=await r.json();
   if(!r.ok)throw new Error(d.error||'Upload failed');
   c.uploaded=true;c.mtime=Date.now();c.saved_name=d.filename;
   toast('Uploaded and saved as '+d.filename);
   selectedFile=null;$('file').value='';$('selected').textContent='No file selected';
   await load();
 }catch(e){$('statusline').textContent='Upload failed: '+e.message;toast(e.message)}
 finally{busy=false;$('drop').style.opacity='1'}
}
$('prev').onclick=()=>go(-1);$('next').onclick=()=>go(1);
$('themeToggle').onclick=()=>{const next=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=next;localStorage.setItem('idiot-board-theme',next);updateThemeButton()};
updateThemeButton();
$('copyText').onclick=async()=>{try{await navigator.clipboard.writeText(currentChunk().text);toast('Chunk text copied. Paste it into WevoLabs.')}catch(e){const area=document.createElement('textarea');area.value=currentChunk().text;document.body.appendChild(area);area.select();const ok=document.execCommand('copy');area.remove();toast(ok?'Chunk text copied.':'Copy failed; select and copy the text manually.')}};

$('choose').onclick=()=>$('file').click();
$('clear').onclick=()=>{$('file').value='';selectedFile=null;$('selected').textContent='No file selected'};
$('file').onchange=e=>{selectedFile=e.target.files[0]||null;$('selected').textContent=selectedFile?`${selectedFile.name} · ${(selectedFile.size/1024/1024).toFixed(1)} MB`:'No file selected';if(selectedFile)upload(selectedFile)};
$('jump').oninput=renderList;
$('drop').ondragover=e=>{e.preventDefault();$('drop').classList.add('drag')};
$('drop').ondragleave=()=> $('drop').classList.remove('drag');
$('drop').ondrop=e=>{e.preventDefault();$('drop').classList.remove('drag');const f=e.dataTransfer.files[0];if(f)upload(f)};
$('remove').onclick=async()=>{
 const c=currentChunk();
 if(!uploaded(c)||!confirm(`Remove ${c.expected_audio_filename}? This deletes the server-side audio file.`))return;
 const r=await fetch('/api/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chunk_id:c.id})});
 const d=await r.json();if(!r.ok){toast(d.error||'Remove failed');return}
 toast('Audio removed');await load();
};
document.addEventListener('keydown',e=>{
 const tag=document.activeElement.tagName;
 if(['INPUT','TEXTAREA','SELECT'].includes(tag) || e.ctrlKey || e.metaKey || e.altKey)return;
 if(e.key==='ArrowLeft'){e.preventDefault();go(-1);return}
 if(e.key==='ArrowRight'){e.preventDefault();go(1);return}
 if(e.key.toLowerCase()==='c'){
   e.preventDefault();
   $('copyText').click();
   return;
 }
 if(e.code==='Space' && !['BUTTON','AUDIO'].includes(tag)){
   e.preventDefault();
   const player=$('player');
   if(player.style.display==='none' || !player.src){toast('No audio uploaded for this chunk yet.');return}
   if(player.paused){player.play().catch(()=>toast('Could not play this audio. Use the audio controls.'))}
   else{player.pause()}
 }
});
load().catch(e=>{$('chunkTitle').textContent='Could not load chunks';$('text').textContent=e.message});
</script>
</body>
</html>
"""


def load_manifest():
    if not MANIFEST.exists():
        raise RuntimeError(f"Missing {MANIFEST}")
    with MANIFEST.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RuntimeError("manifest.csv has no rows")
    return rows


def chunk_id(row):
    return f"{row.get('part','')}_{row.get('chapter','')}_{row.get('chunk','')}"


def safe_audio_path(filename):
    # Only allow a plain filename and MP3 extension.
    if Path(filename).name != filename or not filename.lower().endswith(".mp3"):
        raise ValueError("Invalid audio filename")
    path = AUDIO_DIR / filename
    if path.parent.resolve() != AUDIO_DIR.resolve():
        raise ValueError("Invalid destination")
    return path


def read_multipart(content_type, body):
    # Parse multipart/form-data using the standard-library email parser.
    from email.parser import BytesParser
    from email.policy import default

    message = (
        b"Content-Type: " + content_type.encode("utf-8") +
        b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    parsed = BytesParser(policy=default).parsebytes(message)
    fields = {}
    for part in parsed.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            fields[name] = {"filename": Path(filename).name, "data": payload}
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, errors="replace")
    return fields


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalAudiobookBoard/1.0"

    def log_message(self, fmt, *args):
        print("[http] " + (fmt % args))

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/chunks":
            try:
                rows = load_manifest()
                AUDIO_DIR.mkdir(parents=True, exist_ok=True)
                chunks = []
                for row in rows:
                    text_path = ROOT / row["text_file"]
                    if not text_path.is_file():
                        text = "[Missing text file: " + row["text_file"] + "]"
                    else:
                        text = text_path.read_text(encoding="utf-8", errors="replace")

                    expected = Path(row["audio_file"]).name
                    audio = safe_audio_path(expected)
                    chunks.append({
                        "id": chunk_id(row),
                        "part": row.get("part", ""),
                        "chapter": row.get("chapter", ""),
                        "chapter_roman": row.get("chapter_roman", ""),
                        "chapter_title": row.get("chapter_title", ""),
                        "chunk": row.get("chunk", ""),
                        "total_chunks": row.get("total_chunks", ""),
                        "text_file": row["text_file"],
                        "text": text,
                        "expected_audio_filename": expected,
                        "uploaded": audio.is_file(),
                        "mtime": int(audio.stat().st_mtime_ns) if audio.is_file() else 0
                    })
                self.send_json({"chunks": chunks})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if parsed.path.startswith("/audio/"):
            filename = Path(parsed.path.removeprefix("/audio/")).name
            try:
                audio = safe_audio_path(filename)
            except ValueError:
                self.send_error(400)
                return
            if not audio.is_file():
                self.send_error(404)
                return
            body_size = audio.stat().st_size
            range_header = self.headers.get("Range")
            start, end = 0, body_size - 1
            status = 200
            if range_header:
                match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
                if not match:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{body_size}")
                    self.end_headers()
                    return
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = int(last) if last else body_size - 1
                elif last:
                    suffix = int(last)
                    start = max(0, body_size - suffix)
                if start >= body_size or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{body_size}")
                    self.end_headers()
                    return
                end = min(end, body_size - 1)
                status = 206
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{body_size}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with audio.open("rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    block = f.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))

        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self.send_json({"error": "Upload is empty or exceeds 500 MB."}, 413)
            return

        body = self.rfile.read(length)

        if parsed.path == "/api/upload":
            try:
                fields = read_multipart(self.headers.get("Content-Type", ""), body)
                chunk_key = fields.get("chunk_id", "")
                file_info = fields.get("file")

                if not file_info or not file_info.get("data"):
                    self.send_json({"error": "No file received."}, 400)
                    return

                rows = load_manifest()
                row = next((r for r in rows if chunk_id(r) == chunk_key), None)
                if row is None:
                    self.send_json({"error": "Unknown chunk."}, 404)
                    return

                expected = Path(row["audio_file"]).name
                destination = safe_audio_path(expected)

                if destination.exists():
                    self.send_json({
                        "error": f"{expected} already exists. Remove it or select another chunk."
                    }, 409)
                    return

                # Upload to a temporary file first, then atomically move into place.
                AUDIO_DIR.mkdir(parents=True, exist_ok=True)
                temp = AUDIO_DIR / (".upload-" + uuid.uuid4().hex + ".tmp")
                try:
                    with temp.open("xb") as f:
                        f.write(file_info["data"])
                    # Accept MP3 files based on extension or MPEG content type.
                    original = file_info["filename"].lower()
                    content_type = mimetypes.guess_type(original)[0] or ""
                    head = file_info["data"][:3]
                    if not (
                        original.endswith(".mp3")
                        or content_type == "audio/mpeg"
                        or head == b"ID3"
                    ):
                        raise ValueError("File doesn't look like an MP3. Choose an MP3 audio file.")
                    if destination.exists():
                        raise FileExistsError(f"{expected} already exists.")
                    os.replace(temp, destination)
                finally:
                    if temp.exists():
                        temp.unlink()

                self.send_json({"ok": True, "filename": expected})
            except FileExistsError as e:
                self.send_json({"error": str(e)}, 409)
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return

        if parsed.path == "/api/remove":
            try:
                data = json.loads(body.decode("utf-8"))
                key = data.get("chunk_id", "")
                rows = load_manifest()
                row = next((r for r in rows if chunk_id(r) == key), None)
                if row is None:
                    self.send_json({"error": "Unknown chunk."}, 404)
                    return
                audio = safe_audio_path(Path(row["audio_file"]).name)
                if audio.exists():
                    audio.unlink()
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return

        self.send_error(404)


def main():
    if not MANIFEST.exists():
        print(f"ERROR: {MANIFEST} not found.")
        print("Save this script in the same folder as manifest.csv.")
        sys.exit(1)

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Audio Chunk Board running at http://{HOST}:{PORT}")
    print(f"Project: {ROOT}")
    print(f"MP3 destination: {AUDIO_DIR}")
    print("Local-only server. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
