"""
Local helper for the Candidate Video Suite web UI.

Runs on the user's PC (http://127.0.0.1:8765). The web page (hosted on Vercel)
talks to it for everything that needs the local machine: folder picker,
downloads, FFmpeg conversion and streaming videos to the reviewer.
"""
import base64
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import glob
from concurrent.futures import ThreadPoolExecutor

# Started with pythonw (no console): stdout/stderr are None, which crashes uvicorn and hides
# every error. Send them to helper.log next to this file so problems can be diagnosed.
if sys.stdout is None or sys.stderr is None:
    try:
        _log = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "helper.log"),
                    "w", buffering=1, encoding="utf-8")
    except OSError:
        _log = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel


NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash when started via pythonw


def find_exe(name):
    """Bundled copy (installed by setup), then PATH, then winget's folders."""
    here = os.path.dirname(os.path.abspath(__file__))
    bundled = os.path.join(here, "ffmpeg", name + ".exe")
    if os.path.isfile(bundled):
        return bundled
    found = shutil.which(name)
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    link = os.path.join(local, "Microsoft", "WinGet", "Links", name + ".exe")
    if os.path.isfile(link):
        return link
    hits = glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "**", "bin", name + ".exe"), recursive=True)
    return hits[0] if hits else None


FFMPEG = find_exe("ffmpeg")
FFPROBE = find_exe("ffprobe")

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


def _seconds(text):
    """Largest positive number in the text, else 0 ('N/A' and blanks are ignored)."""
    best = 0.0
    for line in str(text).splitlines():
        try:
            best = max(best, float(line.strip().split(",")[0]))
        except ValueError:
            pass
    return best


def get_video_duration(path):
    """Length in seconds, or 0 if it can't be worked out.

    Videos recorded in a browser (WebM) usually store no length at all, so ffprobe says N/A and a
    percentage can't be shown. In that case read the timestamps of the video packets (no decoding, fast).
    """
    probe = FFPROBE or "ffprobe"
    try:
        for entry in ("format=duration", "stream=duration"):
            out = subprocess.run(
                [probe, "-v", "error", "-show_entries", entry, "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=60, creationflags=NOWIN)
            d = _seconds(out.stdout)
            if d > 0:
                return d
    except Exception:
        pass
    try:
        proc = subprocess.Popen(
            [probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=NOWIN)
        last, deadline = 0.0, time.time() + 90
        for line in proc.stdout:
            try:
                last = max(last, float(line.strip().split(",")[0]))
            except ValueError:
                pass
            if time.time() > deadline:
                proc.kill()
                break
        proc.wait()
        return last
    except Exception:
        return 0


def _clock(seconds):
    seconds = int(max(0, seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def convert_to_mp4(src, preset, f):
    dst = src.replace(".webm", ".mp4")
    f["status"] = "converting"
    f["progress"], f["detail"] = -1, "checking video length…"
    duration = get_video_duration(src)
    f["progress"], f["detail"] = (0, f"0:00 / {_clock(duration)}") if duration > 0 else (-1, "0:00 of video converted")
    cmd = [FFMPEG or "ffmpeg", "-y", "-i", src, "-c:v", "libx264", "-crf", "23",
           "-preset", preset, "-c:a", "aac", "-progress", "pipe:1", dst]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            universal_newlines=True, bufsize=1, creationflags=NOWIN)
    for line in proc.stdout:
        if "out_time_ms=" in line or "out_time_us=" in line:
            try:
                secs = int(line.split("=")[1].strip()) / 1_000_000
            except ValueError:
                continue
            if duration > 0:
                f["progress"] = min(99, int(secs / duration * 100))      # 100 only when it has really finished
                f["detail"] = f"{_clock(secs)} / {_clock(duration)}"
            else:
                f["progress"] = -1                                       # length unknown: page shows a moving bar
                f["detail"] = f"{_clock(secs)} of video converted"
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
                        f["detail"] = f"{done / 1048576:.1f} / {total / 1048576:.1f} MB"
                    else:                                                # server did not say how big it is
                        f["progress"] = -1
                        f["detail"] = f"{done / 1048576:.1f} MB downloaded"

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
    if _ffmpeg_version is None and FFMPEG:
        try:
            out = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True, timeout=10, creationflags=NOWIN)
            _ffmpeg_version = out.stdout.splitlines()[0].split(" Copyright")[0] if out.stdout else ""
        except Exception:
            _ffmpeg_version = ""
    return _ffmpeg_version


@app.get("/api/ping")
def ping():
    return {
        "ok": True,
        "version": "1.7",
        "python": sys.version.split()[0],
        "ffmpeg": FFMPEG is not None,
        "ffmpeg_version": ffmpeg_version() or "",
        "ffprobe": FFPROBE is not None,
    }


# Only one folder window may exist at a time: extra Browse clicks bring it to the front
# instead of opening more windows.
_browse_lock = threading.Lock()
_browse_state = {"proc": None, "path": ""}

_DIALOG_SCRIPT = r'''
Add-Type -AssemblyName System.Windows.Forms
$title = __TITLE__
$start = __START__
$picked = ''
try {
    # Modern Explorer-style folder picker (address bar, search, Quick access, large folder icons).
    # No WinForms owner window: an unpumped owner can stop the picker from ever appearing.
    $cvSrc = @"
using System;
using System.Text;
using System.Threading;
using System.Runtime.InteropServices;
[ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")] class CvFileOpenDialog {}
[ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ICvShellItem {
    void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
    void GetParent(out ICvShellItem ppsi);
    void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
    void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
    void Compare(ICvShellItem psi, uint hint, out int piOrder);
}
[ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ICvFileOpenDialog {
    [PreserveSig] int Show(IntPtr parent);
    void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
    void SetFileTypeIndex(uint iFileType);
    void GetFileTypeIndex(out uint piFileType);
    void Advise(IntPtr pfde, out uint pdwCookie);
    void Unadvise(uint dwCookie);
    void SetOptions(uint fos);
    void GetOptions(out uint pfos);
    void SetDefaultFolder(ICvShellItem psi);
    void SetFolder(ICvShellItem psi);
    void GetFolder(out ICvShellItem ppsi);
    void GetCurrentSelection(out ICvShellItem ppsi);
    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
    void GetResult(out ICvShellItem ppsi);
    void AddPlace(ICvShellItem psi, int alignment);
    void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
    void Close(int hr);
    void SetClientGuid(ref Guid guid);
    void ClearClientData();
    void SetFilter(IntPtr pFilter);
    void GetResults(out IntPtr ppenum);
    void GetSelectedItems(out IntPtr ppsai);
}
public static class CvPicker {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHCreateItemFromParsingName(string path, IntPtr pbc, ref Guid riid, out ICvShellItem item);
    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);

    // Once the picker appears: make it roomy, center it, keep it above every other window, and focus it.
    static void PlaceWhenShown(string title) {
        var t = new Thread(() => {
            uint me = (uint)System.Diagnostics.Process.GetCurrentProcess().Id;
            for (int i = 0; i < 120; i++) {
                Thread.Sleep(50);
                IntPtr found = IntPtr.Zero;
                EnumWindows((h, l) => {
                    uint pid; GetWindowThreadProcessId(h, out pid);
                    if (pid == me && IsWindowVisible(h)) {
                        var sb = new StringBuilder(256); GetWindowText(h, sb, 256);
                        if (sb.ToString() == title) found = h;
                    }
                    return true;
                }, IntPtr.Zero);
                if (found != IntPtr.Zero) {
                    int sw = GetSystemMetrics(0), sh = GetSystemMetrics(1);
                    int w = Math.Min(1100, (int)(sw * 0.72)), hgt = Math.Min(700, (int)(sh * 0.75));
                    SetWindowPos(found, new IntPtr(-1), (sw - w) / 2, (sh - hgt) / 2, w, hgt, 0x0040);   // HWND_TOPMOST, SWP_SHOWWINDOW
                    keybd_event(0x12, 0, 0, UIntPtr.Zero); keybd_event(0x12, 0, 2, UIntPtr.Zero);        // lift the foreground lock
                    SetForegroundWindow(found);
                    break;
                }
            }
        });
        t.IsBackground = true; t.Start();
    }

    public static string Pick(string title, string start) {
        var dlg = (ICvFileOpenDialog)new CvFileOpenDialog();
        uint opts; dlg.GetOptions(out opts);
        dlg.SetOptions(opts | 0x20 | 0x40 | 0x800 | 0x8);   // pick folders, file system only, path must exist, keep cwd
        dlg.SetTitle(title);
        dlg.SetOkButtonLabel("Select folder");
        if (!string.IsNullOrEmpty(start)) {
            try {
                Guid iid = new Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE");
                ICvShellItem item; SHCreateItemFromParsingName(start, IntPtr.Zero, ref iid, out item);
                dlg.SetFolder(item);
            } catch { }
        }
        PlaceWhenShown(title);
        if (dlg.Show(IntPtr.Zero) != 0) return "";
        ICvShellItem res; dlg.GetResult(out res);
        string path; res.GetDisplayName(0x80058000, out path);   // SIGDN_FILESYSPATH
        return path;
    }
}
"@
    Add-Type -TypeDefinition $cvSrc
    $picked = [CvPicker]::Pick($title, $start)
} catch {
    # Fallback: classic tree-style dialog, kept on top by an invisible always-on-top owner window
    $o = New-Object System.Windows.Forms.Form
    $o.TopMost = $true; $o.ShowInTaskbar = $false; $o.Opacity = 0
    $o.StartPosition = 'CenterScreen'; $o.Size = New-Object System.Drawing.Size(1, 1)
    $o.Show(); $o.Activate()
    $f = New-Object System.Windows.Forms.FolderBrowserDialog
    $f.Description = $title
    $f.ShowNewFolderButton = $true
    if ($start -and (Test-Path -LiteralPath $start)) { $f.SelectedPath = $start }
    if ($f.ShowDialog($o) -eq 'OK') { $picked = $f.SelectedPath }
    $o.Dispose()
}
if ($picked) { [Console]::Out.Write($picked) }
'''


def _ps_str(value):
    """Quote text as a PowerShell single-quoted string literal."""
    return "'" + str(value).replace("'", "''") + "'"

_FRONT_SCRIPT = r'''
Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class CvRaise {
    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern int GetWindowTextLength(IntPtr h);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    public static void Raise(uint want) {
        keybd_event(0x12, 0, 0, UIntPtr.Zero); keybd_event(0x12, 0, 2, UIntPtr.Zero);
        EnumWindows((h, l) => {
            uint pid; GetWindowThreadProcessId(h, out pid);
            if (pid == want && IsWindowVisible(h) && GetWindowTextLength(h) > 0) {
                ShowWindow(h, 9);
                SetWindowPos(h, new IntPtr(-1), 0, 0, 0, 0, 0x0043);
                SetForegroundWindow(h);
            }
            return true;
        }, IntPtr.Zero);
    }
}
"@
[CvRaise]::Raise(__PID__)
'''


def _powershell(script, **kw):
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-EncodedCommand", encoded]


class BrowseRequest(BaseModel):
    start: str = ""   # folder the picker opens in (the one already chosen)
    title: str = ""


@app.post("/api/browse")
def browse(req: BrowseRequest | None = None):
    start = req.start.strip() if req and req.start else ""
    if start and not os.path.isdir(start):
        start = ""
    title = (req.title.strip() if req and req.title else "") or "Select a folder"
    if not _browse_lock.acquire(blocking=False):
        # A folder window is already open: raise it, then share its result
        proc = _browse_state["proc"]
        if proc is not None and proc.poll() is None:
            try:
                subprocess.run(_powershell(_FRONT_SCRIPT.replace("__PID__", str(proc.pid))),
                               capture_output=True, timeout=15, creationflags=NOWIN)
            except Exception:
                pass
        with _browse_lock:
            pass
        return {"path": _browse_state["path"]}
    try:
        script = _DIALOG_SCRIPT.replace("__TITLE__", _ps_str(title)).replace("__START__", _ps_str(start))
        proc = subprocess.Popen(_powershell(script), stdout=subprocess.PIPE,
                                text=True, creationflags=NOWIN)
        _browse_state["proc"], _browse_state["path"] = proc, ""
        out, _ = proc.communicate()
        _browse_state["path"] = out.strip()
        return {"path": _browse_state["path"]}
    finally:
        _browse_state["proc"] = None
        _browse_lock.release()


@app.post("/api/browse/raise")
def browse_raise():
    """Bring the open folder window to the front and return right away (never opens a window)."""
    proc = _browse_state["proc"]
    if proc is None or proc.poll() is not None:
        return {"raised": False}
    try:
        subprocess.run(_powershell(_FRONT_SCRIPT.replace("__PID__", str(proc.pid))),
                       capture_output=True, timeout=15, creationflags=NOWIN)
    except Exception:
        return {"raised": False}
    return {"raised": True}


@app.post("/api/jobs")
def create_job(req: JobRequest):
    if not req.folder or not os.path.isdir(req.folder):
        raise HTTPException(400, "Destination folder does not exist")
    if req.mode == "convert" and not FFMPEG:
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
                              "folder": cdir, "status": "queued", "progress": 0, "message": "", "detail": ""})

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
        "files": [{k: f[k] for k in ("cid", "kind", "status", "progress", "message", "detail")}
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
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", log_config=None)
