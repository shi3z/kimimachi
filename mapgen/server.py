"""Web UI server: serves out/ and generates new maps from a place name.

  python -m mapgen.server --port 8891

  POST /api/generate  {"place": "...", "size": 1500, "title": "..."}  -> {"id": ...}
  GET  /api/jobs/<id>                                                -> {"status", "log", "url"}
Jobs run one at a time in a background worker (each is a `python -m mapgen` subprocess).
"""
import argparse
import json
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
JOBS = {}
QUEUE = queue.Queue()


def safe_name(s):
    return re.sub(r'[\\/:*?"<>| ]+', "_", s).strip("._")[:60]


def worker():
    while True:
        job = QUEUE.get()
        job["status"] = "running"
        cmd = [sys.executable, "-u", "-m", "mapgen", job["place"], "--size", str(job["size"]),
               "--title", job["title"], "--out", str(OUT / job["dir"])]
        try:
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                job["log"].append(line.rstrip())
            proc.wait(timeout=1800)
            ok = proc.returncode == 0 and (OUT / job["dir"] / "index.html").exists()
            job["status"] = "done" if ok else "error"
            if ok:
                job["url"] = quote(job["dir"]) + "/index.html"
        except Exception as e:  # noqa: BLE001 - report anything to the UI
            job["log"].append(f"エラー: {e}")
            job["status"] = "error"
        job["finished"] = time.time()


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/generate":
            return self._json(404, {"error": "not found"})
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            place = str(data.get("place", "")).strip()
            size = float(data.get("size") or 1500)
        except (ValueError, TypeError):
            return self._json(400, {"error": "入力が不正です"})
        if not place or len(place) > 100:
            return self._json(400, {"error": "地名を入力してください"})
        size = max(300.0, min(5000.0, size))
        title = str(data.get("title") or "").strip()[:60] or place
        job = {"id": uuid.uuid4().hex[:12], "place": place, "size": size, "title": title,
               "dir": safe_name(title) or "map", "status": "queued", "log": [], "url": None,
               "created": time.time()}
        JOBS[job["id"]] = job
        QUEUE.put(job)
        return self._json(200, {"id": job["id"], "position": QUEUE.qsize()})

    def do_GET(self):
        if self.path.startswith("/api/jobs/"):
            job = JOBS.get(self.path.rsplit("/", 1)[-1])
            if not job:
                return self._json(404, {"error": "not found"})
            return self._json(200, {k: job[k] for k in ("id", "place", "title", "status", "log", "url")})
        if self.path in ("/", "/index.html"):
            from .export import write_gallery
            write_gallery(OUT)   # always fresh
        return super().do_GET()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8891)
    ap.add_argument("--bind", default="127.0.0.1")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer((a.bind, a.port), partial(Handler, directory=str(OUT)))
    print(f"serving {OUT} on http://{a.bind}:{a.port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
