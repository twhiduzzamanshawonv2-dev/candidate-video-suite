"""
Local helper for the Candidate Video Reviewer web page.

Runs on the user's PC (http://127.0.0.1:8765). The web page talks to it for
everything that needs the local machine: the folder window, streaming the
videos, saving judgements and syncing them to the team's Google Sheet.
"""
import base64
import glob
import hashlib
import importlib.util
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from typing import Optional

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

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


@app.get("/api/ping")
def ping():
    return {
        "ok": True,
        "version": "1.11",
        "python": sys.version.split()[0],
        "platform": "windows" if IS_WIN else "mac" if IS_MAC else "linux",
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


_MAC_PICK = '''on run argv
    set promptText to item 1 of argv
    set startPath to item 2 of argv
    tell application "System Events"
        activate
        try
            if startPath is "" then
                set f to choose folder with prompt promptText
            else
                set f to choose folder with prompt promptText default location (POSIX file startPath)
            end if
        on error
            return ""
        end try
    end tell
    return POSIX path of f
end run'''


def _mac_pick(title, start):
    proc = subprocess.Popen(["osascript", "-e", _MAC_PICK, title, start],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    _browse_state["proc"] = proc
    out, _ = proc.communicate()
    path = out.strip()
    return path.rstrip("/") if len(path) > 1 else path


def _mac_raise():
    subprocess.run(["osascript", "-e", 'tell application "System Events" to activate'],
                   capture_output=True, timeout=15)


def _raise_picker(proc):
    if IS_MAC:
        _mac_raise()
    elif IS_WIN:
        subprocess.run(_powershell(_FRONT_SCRIPT.replace("__PID__", str(proc.pid))),
                       capture_output=True, timeout=15, creationflags=NOWIN)


def _powershell(script, **kw):
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-EncodedCommand", encoded]


def _win_pick(title, start):
    script = _DIALOG_SCRIPT.replace("__TITLE__", _ps_str(title)).replace("__START__", _ps_str(start))
    proc = subprocess.Popen(_powershell(script), stdout=subprocess.PIPE, text=True, creationflags=NOWIN)
    _browse_state["proc"] = proc
    out, _ = proc.communicate()
    return out.strip()


# Linux has no single folder window: use whichever tool the desktop has (zenity on GNOME and most
# desktops, kdialog on KDE), else Python's own Tk dialog. Nothing is installed by the helper.
_TK_PICK = '''import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
sys.stdout.write(filedialog.askdirectory(title=sys.argv[1], initialdir=sys.argv[2] or None) or "")'''


def _linux_command(title, start):
    home = os.path.expanduser("~")
    if shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", "--title", title]
        return cmd + (["--filename", start.rstrip("/") + "/"] if start else [])
    if shutil.which("kdialog"):
        return ["kdialog", "--title", title, "--getexistingdirectory", start or home]
    if importlib.util.find_spec("tkinter"):
        return [sys.executable, "-c", _TK_PICK, title, start]
    return None


def _linux_pick(title, start):
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise HTTPException(501, "The helper has no desktop session to show a folder window in "
                                 "(it was probably started outside your desktop). Type the folder path instead.")
    cmd = _linux_command(title, start)
    if cmd is None:
        raise HTTPException(501, "No folder window is available on this computer. Install zenity "
                                 "(for example: sudo apt install zenity), or type the folder path instead.")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    _browse_state["proc"] = proc
    out, _ = proc.communicate()
    path = out.strip()          # cancelling the window gives an empty answer
    return path.rstrip("/") if len(path) > 1 else path


class BrowseRequest(BaseModel):
    start: str = ""   # folder the picker opens in (the one already chosen)
    title: str = ""


@app.post("/api/browse")
def browse(req: Optional[BrowseRequest] = None):
    start = req.start.strip() if req and req.start else ""
    if start and not os.path.isdir(start):
        start = ""
    title = (req.title.strip() if req and req.title else "") or "Select a folder"
    if not _browse_lock.acquire(blocking=False):
        # A folder window is already open: raise it, then share its result
        proc = _browse_state["proc"]
        if proc is not None and proc.poll() is None:
            try:
                _raise_picker(proc)
            except Exception:
                pass
        with _browse_lock:
            pass
        return {"path": _browse_state["path"]}
    try:
        pick = _mac_pick if IS_MAC else _win_pick if IS_WIN else _linux_pick
        _browse_state["path"] = ""
        _browse_state["path"] = pick(title, start)
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
        _raise_picker(proc)
    except Exception:
        return {"raised": False}
    return {"raised": True}


# ── Candidates and video streaming ─────────────────────────────────────────────

VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v")


def pick_videos(cid, names):
    """Exact <id>_screenrecord.* / <id>_webcam.* first, else any video named like screen / webcam (.mp4 preferred)."""
    vids = [n for n in names if os.path.splitext(n)[1].lower() in VIDEO_EXT]
    rank = lambda n: VIDEO_EXT.index(os.path.splitext(n)[1].lower())

    def find(kind, rx):
        exact = [n for n in vids if n.lower().startswith(f"{cid}_{kind}".lower())]
        loose = [n for n in vids if re.search(rx, n, re.I)]
        pool = sorted(exact or loose, key=rank)
        return pool[0] if pool else None

    return find("screenrecord", r"screen"), find("webcam", r"web ?cam|camera")


@app.get("/api/candidates")
def candidates(folder: str):
    if not os.path.isdir(folder):
        return {"candidates": []}
    result = []
    for d in sorted(os.listdir(folder)):
        p = os.path.join(folder, d)
        if os.path.isdir(p):
            names = [n for n in os.listdir(p) if os.path.isfile(os.path.join(p, n))]
            screen, webcam = pick_videos(d, names)
            result.append({"name": d, "screen": screen, "webcam": webcam, "files": names})
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


# ── Candidate review: verdicts, Google Sheet sync, Excel export ───────────────
#
# Every judgement is written to a small file on this PC first (the backup), then
# queued and sent to the team's Google Sheet by a background thread. If the
# internet is down the queue simply waits and is sent later, in order.

_HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW_DIR = os.path.join(_HERE, "reviews")
CONFIG_PATH = os.path.join(_HERE, "review_config.json")
QUEUE_PATH = os.path.join(REVIEW_DIR, "queue.json")
FAILED_PATH = os.path.join(REVIEW_DIR, "failed.json")
VERDICT_LABEL = {"cheat": "Cheater", "maybe": "Unsure", "clean": "Not a cheater"}
SHEET_URL_RE = re.compile(os.environ.get("HELPER_SHEET_REGEX", r"https://script\.google\.com/macros/s/[A-Za-z0-9_\-]+/exec"))
CAND_BAD = re.compile(r'[\\/:*?"<>|]')

_rv_lock = threading.RLock()
_rv_state = {"last_ok": None, "last_error": None, "online": None}
_rv_wake = threading.Event()
_rv_worker_started = False


class ScriptError(Exception):
    """The Google script answered, but refused the request (e.g. wrong key)."""


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


_io_lock = threading.RLock()   # reads and writes of these small files take turns (Windows refuses to replace an open file)


def _jload(path, default):
    with _io_lock:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default


def _jsave(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _io_lock:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        for attempt in range(20):       # antivirus or an indexer may hold the file for a moment
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)


def _cfg():
    return _jload(CONFIG_PATH, {})


def _cfg_save(cfg):
    _jsave(CONFIG_PATH, cfg)


def _sheet():
    """(script url, secret key) from the saved Google Sheet link, or (None, None)."""
    link = _cfg().get("sheet") or ""
    m = SHEET_URL_RE.match(link)
    if not m:
        return None, None
    q = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    return m.group(0), (q.get("key") or [""])[0]


def _safe(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:50] or "x"


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _clean_exam(name):
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name or len(name) > 80 or re.search(r'[\\/:*?"<>|\[\]]', name):
        raise HTTPException(400, "Exam name is empty, too long, or contains one of these characters: \\ / : * ? \" < > | [ ]")
    return name


def _script_call(payload, timeout=(5, 45)):
    url, key = _sheet()
    if not url:
        raise ScriptError("Google Sheet is not connected")
    r = requests.post(url, json={**payload, "key": key}, timeout=timeout)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        raise ScriptError("The Google Sheet link did not answer correctly. Check that the web app is deployed with access for 'Anyone'.")
    if not data.get("ok"):
        raise ScriptError(data.get("error") or "The Google Sheet refused the request")
    return data


# queue ---------------------------------------------------------------------

def _queue_load():
    return _jload(QUEUE_PATH, [])


def _enqueue(item):
    if not _sheet()[0]:
        return              # not connected: the local backup is enough (it is uploaded when a sheet is connected)
    item["id"] = uuid.uuid4().hex
    with _rv_lock:
        q = _queue_load()
        if item["action"] in ("save", "clear"):
            q = [x for x in q if not (x["action"] in ("save", "clear") and x["exam"] == item["exam"]
                                      and x["row"]["candidate"] == item["row"]["candidate"])]
        q.append(item)
        _jsave(QUEUE_PATH, q)
    _rv_ensure_worker()


def _rv_flush():
    """Send queued items in order. Stops at the first network problem."""
    if not _sheet()[0]:
        return
    while True:
        with _rv_lock:
            q = _queue_load()
            if not q:
                return
            item = q[0]
        try:
            _script_call({k: v for k, v in item.items() if k != "id"})
        except ScriptError as e:
            _rv_state.update(last_error=str(e), online=True)
            if str(e).startswith("Wrong key"):
                return                      # nothing will work until the link is fixed
            with _rv_lock:                  # this one item is refused: park it, carry on with the rest
                failed = _jload(FAILED_PATH, [])
                failed.append({**item, "error": str(e)})
                _jsave(FAILED_PATH, failed[-200:])
        except Exception:
            _rv_state.update(last_error="Offline or Google is not reachable", online=False)
            return
        with _rv_lock:
            _jsave(QUEUE_PATH, [x for x in _queue_load() if x.get("id") != item["id"]])
        _rv_state.update(last_ok=_now(), last_error=None, online=True)


def _rv_worker():
    while True:
        try:
            _rv_flush()
        except Exception:
            pass
        _rv_wake.wait(timeout=8 if _queue_load() else 25)
        _rv_wake.clear()


def _rv_ensure_worker():
    global _rv_worker_started
    with _rv_lock:
        if not _rv_worker_started:
            _rv_worker_started = True
            threading.Thread(target=_rv_worker, daemon=True).start()
    _rv_wake.set()


# local backup files --------------------------------------------------------

def _local_file(exam, reviewer):
    h = hashlib.md5(_norm(exam).encode("utf-8")).hexdigest()[:6]
    return os.path.join(REVIEW_DIR, f"review_{_safe(exam)}_{h}_{_safe(reviewer)}.json")


def _local_put(exam, reviewer, row, delete=False):
    with _rv_lock:
        p = _local_file(exam, reviewer)
        d = _jload(p, {"exam": exam, "reviewer": reviewer, "rows": {}})
        if delete:
            d["rows"].pop(row["candidate"], None)
        else:
            d["rows"][row["candidate"]] = row
        _jsave(p, d)


def _local_files():
    for p in glob.glob(os.path.join(REVIEW_DIR, "review_*.json")):
        d = _jload(p, {})
        if d.get("exam"):
            yield d


def _local_rows(exam):
    out = {}
    for d in _local_files():
        if _norm(d["exam"]) != _norm(exam):
            continue
        for cand, r in d.get("rows", {}).items():
            if cand not in out or r.get("at", "") >= out[cand].get("at", ""):
                out[cand] = r
    return out


# API -----------------------------------------------------------------------

class ReviewIn(BaseModel):
    exam: str
    candidate: str
    reviewer: str
    verdict: str = ""          # cheat | maybe | clean | "" (clear this judgement)
    pct: int = 0
    comment: str = ""


class SheetIn(BaseModel):
    sheet: str = ""


class ExamIn(BaseModel):
    name: str
    reviewer: str = ""


def _mask(url):
    return url[:40] + "…" + url[-9:] if url and len(url) > 56 else (url or "")


@app.get("/api/review/config")
def rv_get_config():
    url, key = _sheet()
    return {"configured": bool(url), "masked": _mask(url), "has_key": bool(key)}


@app.post("/api/review/config")
def rv_set_config(body: SheetIn):
    link = body.sheet.strip()
    cfg = _cfg()
    if not link:
        cfg.pop("sheet", None)
        _cfg_save(cfg)
        return {**rv_get_config(), "verified": False}
    m = SHEET_URL_RE.match(link)
    if not m:
        raise HTTPException(400, "That is not a Google Apps Script link. It should look like https://script.google.com/macros/s/.../exec?key=YOUR_SECRET")
    if not (urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("key") or [""])[0]:
        raise HTTPException(400, "Add  ?key=YOUR_SECRET  at the end of the link (the secret you set in the script).")
    old = cfg.get("sheet")
    cfg["sheet"] = link
    _cfg_save(cfg)
    verified = False
    try:
        _script_call({"action": "ping"}, (5, 40))
        verified = True
    except ScriptError as e:                      # wrong key / wrong deployment: undo
        if old:
            cfg["sheet"] = old
        else:
            cfg.pop("sheet", None)
        _cfg_save(cfg)
        raise HTTPException(400, str(e))
    except Exception:
        pass                                      # offline: keep it, verify later
    # judgements made before the sheet was connected go up too (saving again is harmless)
    for d in _local_files():
        for r in d.get("rows", {}).values():
            _enqueue({"action": "save", "exam": d["exam"], "row": r})
    return {**rv_get_config(), "verified": verified}


@app.get("/api/review/status")
def rv_status():
    return {"configured": bool(_sheet()[0]), "queued": len(_queue_load()), "online": _rv_state["online"],
            "last_ok": _rv_state["last_ok"], "last_error": _rv_state["last_error"]}


@app.get("/api/review/exams")
def rv_exams():
    exams, source = [], "local"
    if _sheet()[0]:
        try:
            exams = _script_call({"action": "listExams"}, (5, 40)).get("exams", [])
            cfg = _cfg()
            cfg["exams_cache"] = exams
            _cfg_save(cfg)
            source = "sheet"
        except Exception:
            exams, source = _cfg().get("exams_cache", []), "cache"
    names = {_norm(e["name"]) for e in exams}
    queued = [x["exam"] for x in _queue_load() if x.get("exam")]
    if source == "sheet":
        # the sheet answered, so it is the truth: an exam deleted there must disappear here too.
        # Only exams still waiting to be uploaded may be shown although the sheet does not have them yet.
        qn = {_norm(n) for n in queued}
        cfg = _cfg()
        kept = [n for n in cfg.get("exams", []) if _norm(n) in names or _norm(n) in qn]
        if kept != cfg.get("exams", []):
            cfg["exams"] = kept
            _cfg_save(cfg)
        extra = queued
    else:
        extra = list(_cfg().get("exams", [])) + [d["exam"] for d in _local_files()] + queued
    for n in extra:                             # created or judged here, not on the sheet (yet)
        if _norm(n) not in names:
            names.add(_norm(n))
            exams.append({"name": n, "status": "Active"})
    exams.sort(key=lambda e: e["name"].lower())
    return {"exams": exams, "source": source}


@app.post("/api/review/exams")
def rv_new_exam(body: ExamIn):
    name = _clean_exam(body.name)
    for e in rv_exams()["exams"]:
        if _norm(e["name"]) == _norm(name):
            return {"name": e["name"], "existing": True}
    cfg = _cfg()
    cfg["exams"] = sorted(set(cfg.get("exams", [])) | {name})
    _cfg_save(cfg)
    if _sheet()[0]:
        _enqueue({"action": "createExam", "exam": name, "by": body.reviewer[:40]})
    return {"name": name, "existing": False}


@app.post("/api/review")
def rv_save(body: ReviewIn):
    exam = _clean_exam(body.exam)
    cand = body.candidate.strip()
    reviewer = re.sub(r"\s+", " ", body.reviewer).strip()[:40]
    if not cand or len(cand) > 60 or CAND_BAD.search(cand) or cand in (".", ".."):
        raise HTTPException(400, f"Invalid candidate number: {body.candidate!r}")
    if not reviewer:
        raise HTTPException(400, "Enter your name first")
    if body.verdict and body.verdict not in VERDICT_LABEL:
        raise HTTPException(400, "Unknown verdict")
    if not body.verdict and not body.comment.strip() and body.pct == 0:
        row = {"candidate": cand, "reviewer": reviewer, "at": _now()}
        _local_put(exam, reviewer, row, delete=True)
        _enqueue({"action": "clear", "exam": exam, "row": row})
        return {"saved": True, "cleared": True}
    row = {"candidate": cand, "verdict": VERDICT_LABEL.get(body.verdict, ""), "pct": max(0, min(100, int(body.pct))),
           "comment": body.comment.strip()[:4000], "reviewer": reviewer, "at": _now()}
    _local_put(exam, reviewer, row)
    _enqueue({"action": "save", "exam": exam, "row": row})
    return {"saved": True, "row": row}


def _natural(r):
    c = r["candidate"]
    return (0, int(c), c) if c.isdigit() else (1, 0, c)


def _exam_rows(exam):
    """(rows, stale, sheet_url) for one exam: the sheet's rows plus anything not uploaded yet."""
    stale, sheet_url = False, None
    cache = os.path.join(REVIEW_DIR, "cache_" + _safe(exam) + "_" + hashlib.md5(_norm(exam).encode()).hexdigest()[:6] + ".json")
    if _sheet()[0]:
        try:
            data = _script_call({"action": "rows", "exam": exam}, (5, 45))
            rows = {r["candidate"]: r for r in data.get("rows", [])}
            sheet_url = data.get("sheetUrl")
            _jsave(cache, {"rows": rows, "sheetUrl": sheet_url})
        except Exception:
            c = _jload(cache, {})
            rows, sheet_url, stale = c.get("rows", {}), c.get("sheetUrl"), True
    else:
        rows = _local_rows(exam)
    for item in _queue_load():
        if item.get("exam") != exam or "row" not in item:
            continue
        if item["action"] == "save":
            rows[item["row"]["candidate"]] = item["row"]
        elif item["action"] == "clear":
            rows.pop(item["row"]["candidate"], None)
    return sorted(rows.values(), key=_natural), stale, sheet_url


@app.get("/api/review/rows")
def rv_rows(exam: str):
    rows, stale, sheet_url = _exam_rows(_clean_exam(exam))
    return {"rows": rows, "stale": stale, "sheet_url": sheet_url}


_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@app.get("/api/review/export")
def rv_export(exams: str = ""):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise HTTPException(500, "The Excel library (openpyxl) is not installed yet. Close the launcher window and run it again.")
    names = [n.strip() for n in exams.split("|") if n.strip()] or [e["name"] for e in rv_exams()["exams"]]
    if not names:
        raise HTTPException(400, "There are no exams to export yet")

    def put(ws, r, c, v):
        cell = ws.cell(row=r, column=c)
        if isinstance(v, str):
            v = _ILLEGAL.sub("", v)
            cell.value = v
            cell.data_type = "s"            # text, never a formula (a comment may start with "=")
        else:
            cell.value = v
        return cell

    tone = {"Cheater": ("FDECEB", "C2271D"), "Unsure": ("FFF3D9", "A76A00"), "Not a cheater": ("E6F5EC", "14804A")}
    head_fill = PatternFill("solid", fgColor="111318")
    head_font = Font(bold=True, color="FFFFFF")
    wb = Workbook()
    wb.remove(wb.active)
    used, summary, by_reviewer = set(), [], []
    for name in names:
        rows, _, _ = _exam_rows(name)
        title = re.sub(r"[\[\]:*?/\\]", "-", name)[:31]
        base, k = title, 2
        while title.lower() in used or title.lower() == "summary":
            title = f"{base[:28]}-{k}"
            k += 1
        used.add(title.lower())
        ws = wb.create_sheet(title)
        for c, h in enumerate(["Candidate no.", "Cheater?", "Fraud %", "Comments", "Reviewed by", "Reviewed at"], 1):
            cell = put(ws, 1, c, h)
            cell.fill, cell.font = head_fill, head_font
        for i, r in enumerate(rows, 2):
            put(ws, i, 1, r["candidate"])
            v = put(ws, i, 2, r.get("verdict", ""))
            if r.get("verdict") in tone:
                v.fill = PatternFill("solid", fgColor=tone[r["verdict"]][0])
                v.font = Font(bold=True, color=tone[r["verdict"]][1])
            p = put(ws, i, 3, int(r.get("pct") or 0))
            p.number_format = '0"%"'
            put(ws, i, 4, r.get("comment", "")).alignment = Alignment(wrap_text=True, vertical="top")
            put(ws, i, 5, r.get("reviewer", ""))
            put(ws, i, 6, r.get("at", ""))
        for c, w in enumerate([15, 16, 10, 70, 18, 18], 1):
            ws.column_dimensions[get_column_letter(c)].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:F{max(2, len(rows) + 1)}"
        cnt = {k: sum(1 for r in rows if r.get("verdict") == k) for k in tone}
        pcts = [int(r.get("pct") or 0) for r in rows]
        summary.append([name, len(rows), cnt["Cheater"], cnt["Unsure"], cnt["Not a cheater"],
                        round(sum(pcts) / len(pcts), 1) if pcts else 0])
        who = {}
        for r in rows:
            who[r.get("reviewer", "")] = who.get(r.get("reviewer", ""), 0) + 1
        by_reviewer += [[name, w, n] for w, n in sorted(who.items())]

    ws = wb.create_sheet("Summary")
    for c, h in enumerate(["Exam", "Judged", "Cheater", "Unsure", "Not a cheater", "Average fraud %"], 1):
        cell = put(ws, 1, c, h)
        cell.fill, cell.font = head_fill, head_font
    for i, row in enumerate(summary, 2):
        for c, v in enumerate(row, 1):
            put(ws, i, c, v)
    start = len(summary) + 4
    for c, h in enumerate(["Exam", "Reviewer", "Candidates judged"], 1):
        cell = put(ws, start, c, h)
        cell.fill, cell.font = head_fill, head_font
    for i, row in enumerate(by_reviewer, start + 1):
        for c, v in enumerate(row, 1):
            put(ws, i, c, v)
    for c, w in enumerate([30, 18, 18, 12, 16, 18], 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A2"
    if len(names) > 1:
        wb.move_sheet(ws, offset=-len(names))

    buf = io.BytesIO()
    wb.save(buf)
    fname = (_safe(names[0]) if len(names) == 1 else "candidates_review_" + datetime.now().strftime("%Y-%m-%d")) + ".xlsx"
    return Response(buf.getvalue(), headers={
        "Content-Disposition": f'attachment; filename="{fname}"',
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})


if __name__ == "__main__":
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex((HOST, PORT)) == 0:
            sys.exit(0)  # already running (e.g. auto-started at login)
    _rv_ensure_worker()                    # sends judgements queued while offline
    print(f"Helper running on http://{HOST}:{PORT}  (keep this window open)")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", log_config=None)
