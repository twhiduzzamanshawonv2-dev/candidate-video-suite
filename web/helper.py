"""
Local helper for the Candidate Video Suite web UI.

Runs on the user's PC (http://127.0.0.1:8765). The web page (hosted on Vercel)
talks to it for everything that needs the local machine: folder picker,
downloads, FFmpeg conversion and streaming videos to the reviewer.
"""
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

HOST = "127.0.0.1"
PORT = int(os.environ.get("HELPER_PORT", "8765"))

# Only these web origins may call the helper (the deployed site + localhost).
# Override with the HELPER_ORIGIN_REGEX environment variable if the domain changes.
ORIGIN_REGEX = re.compile(
    os.environ.get(
        "HELPER_ORIGIN_REGEX",
        r"https://candidate-video-suite\.vercel\.app|http://(localhost|127\.0\.0\.1)(:\d+)?",
    )
)

app = FastAPI(title="Candidate Video Helper")


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and not ORIGIN_REGEX.fullmatch(origin):
        return JSONResponse({"error": "Origin not allowed"}, status_code=403)

    if request.method == "OPTIONS":
        response = Response(status_code=204)
    else:
        response = await call_next(request)

    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = request.headers.get(
            "access-control-request-headers", "*"
        )
        response.headers["Access-Control-Max-Age"] = "600"
        # Chrome "Private Network Access": public page -> localhost
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


# ── Downloading / converting ───────────────────────────────────────────────────

JOBS = {}


def get_video_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0


def convert_to_mp4(src, preset, f):
    dst = src.replace(".webm", ".mp4")
    duration = get_video_duration(src)
    f["status"] = "converting"
    f["progress"] = 0
    cmd = ["ffmpeg", "-y", "-i", src, "-c:v", "libx264", "-crf", "23",
           "-preset", preset, "-c:a", "aac", "-progress", "pipe:1", dst]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            universal_newlines=True, bufsize=1)
    for line in proc.stdout:
        if "out_time_ms=" in line or "out_time_us=" in line:
            try:
                secs = int(line.split("=")[1].strip()) / 1_000_000
                if duration > 0:
                    f["progress"] = min(100, int(secs / duration * 100))
            except ValueError:
                pass
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg error")
    if os.path.exists(src):
        os.remove(src)


def process_file(f, folder, mode, preset):
    try:
        ext = ".mp4" if mode in ("rename", "convert") else ".webm"
        final_path = os.path.join(folder, f"{f['name']}{ext}")
        if os.path.exists(final_path):
            f["status"], f["progress"], f["message"] = "exists", 100, "Already downloaded"
            return

        download_path = final_path if mode == "rename" else os.path.join(folder, f"{f['name']}.webm")
        if os.path.exists(download_path):
            os.remove(download_path)

        f["status"] = "downloading"
        resp = requests.get(f["url"], stream=True, timeout=30)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(download_path, "wb") as out:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        f["progress"] = min(100, int(done / total * 100))

        if mode == "convert":
            convert_to_mp4(download_path, preset, f)
        f["status"], f["progress"] = "done", 100
    except Exception as e:
        f["status"], f["message"] = "failed", str(e)


def run_job(job):
    with ThreadPoolExecutor(max_workers=job["workers"]) as ex:
        for f in job["files"]:
            ex.submit(process_file, f, f["folder"], job["mode"], job["preset"])
    job["finished"] = True


class JobItem(BaseModel):
    id: str
    screen_url: str | None = None
    webcam_url: str | None = None


class JobRequest(BaseModel):
    folder: str
    mode: str = "convert"  # convert | original | rename
    preset: str = "ultrafast"
    workers: int = 4
    items: list[JobItem]


_ffmpeg_version = None


def ffmpeg_version():
    global _ffmpeg_version
    if _ffmpeg_version is None and shutil.which("ffmpeg"):
        try:
            out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
            _ffmpeg_version = out.stdout.splitlines()[0].split(" Copyright")[0] if out.stdout else ""
        except Exception:
            _ffmpeg_version = ""
    return _ffmpeg_version


@app.get("/api/ping")
def ping():
    return {
        "ok": True,
        "version": "1.1",
        "python": sys.version.split()[0],
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffmpeg_version": ffmpeg_version() or "",
        "ffprobe": shutil.which("ffprobe") is not None,
    }


@app.post("/api/browse")
def browse():
    code = (
        "import tkinter as tk\nfrom tkinter import filedialog\n"
        "r=tk.Tk();r.withdraw();r.attributes('-topmost',True)\n"
        "print(filedialog.askdirectory(master=r))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return {"path": out.stdout.strip()}


@app.post("/api/jobs")
def create_job(req: JobRequest):
    if not req.folder or not os.path.isdir(req.folder):
        raise HTTPException(400, "Destination folder does not exist")
    if req.mode == "convert" and not shutil.which("ffmpeg"):
        raise HTTPException(400, "FFmpeg is not installed (needed for Convert mode)")

    files = []
    for item in req.items:
        cid = item.id.strip()
        if not cid or re.search(r'[\\/:*?"<>|]', cid) or cid in (".", ".."):
            raise HTTPException(400, f"Invalid candidate ID: {item.id!r}")
        cdir = os.path.join(req.folder, cid)
        os.makedirs(cdir, exist_ok=True)
        for kind, url in (("screenrecord", item.screen_url), ("webcam", item.webcam_url)):
            if url:
                files.append({"cid": cid, "kind": kind, "name": f"{cid}_{kind}", "url": url,
                              "folder": cdir, "status": "queued", "progress": 0, "message": ""})

    job_id = uuid.uuid4().hex
    job = {"id": job_id, "mode": req.mode, "preset": req.preset,
           "workers": max(1, min(10, req.workers)), "files": files,
           "finished": False, "created": time.time()}
    JOBS[job_id] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return {"id": job_id, "total": len(files)}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return {
        "finished": job["finished"],
        "files": [{k: f[k] for k in ("cid", "kind", "status", "progress", "message")}
                  for f in job["files"]],
    }


# ── Reviewer support ───────────────────────────────────────────────────────────

@app.get("/api/candidates")
def candidates(folder: str):
    if not os.path.isdir(folder):
        return {"candidates": []}
    result = []
    for d in sorted(os.listdir(folder)):
        p = os.path.join(folder, d)
        if os.path.isdir(p):
            result.append({
                "name": d,
                "screen": os.path.exists(os.path.join(p, f"{d}_screenrecord.mp4")),
                "webcam": os.path.exists(os.path.join(p, f"{d}_webcam.mp4")),
            })
    return {"candidates": result}


@app.get("/video")
def video(request: Request, folder: str, path: str):
    root = os.path.realpath(folder)
    full = os.path.realpath(os.path.join(root, path))
    if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
        raise HTTPException(404, "Not found")

    size = os.path.getsize(full)
    mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
    start, end, status = 0, size - 1, 200

    rng = request.headers.get("range")
    if rng:
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        if not m or (not m.group(1) and not m.group(2)):
            raise HTTPException(400, "Invalid Range")
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        else:  # suffix range: last N bytes
            start = max(0, size - int(m.group(2)))
        end = min(end, size - 1)
        if start >= size or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206

    length = end - start + 1

    def stream():
        remaining = length
        with open(full, "rb") as fh:
            fh.seek(start)
            while remaining > 0:
                data = fh.read(min(131072, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length)}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(stream(), status_code=status, media_type=mime, headers=headers)


if __name__ == "__main__":
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex((HOST, PORT)) == 0:
            sys.exit(0)  # already running (e.g. auto-started at login)
    print(f"Helper running on http://{HOST}:{PORT}  (keep this window open)")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
