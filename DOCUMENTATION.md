# Candidate Video Reviewer: full documentation

A browser tool for reviewing candidate exam recordings. A reviewer opens one candidate at a time, watches the screen recording and the webcam recording side by side in sync, records a verdict, a fraud probability and timestamped comments, and the whole team's judgements collect in one Google Sheet per exam, with an Excel download.

**Contents**

1. [At a glance](#1-at-a-glance)
2. [Quick start](#2-quick-start)
3. [How the system fits together](#3-how-the-system-fits-together)
4. [The reviewer page](#4-the-reviewer-page)
5. [Video folder rules](#5-video-folder-rules)
6. [The local helper](#6-the-local-helper)
7. [Google Sheet backend](#7-google-sheet-backend)
8. [Helper API reference](#8-helper-api-reference)
9. [Data, privacy and security](#9-data-privacy-and-security)
10. [Deploying on Vercel](#10-deploying-on-vercel)
11. [Project structure](#11-project-structure)
12. [Maintenance and development](#12-maintenance-and-development)
13. [Troubleshooting](#13-troubleshooting)
14. [Limits and behaviours to know](#14-limits-and-behaviours-to-know)

---

## 1. At a glance

| | |
|---|---|
| **What it does** | Plays a candidate's screen and webcam recordings together, collects a verdict (Cheater, Unsure, Not a cheater), a fraud % and comments, and syncs them to Google Sheets. |
| **Who uses it** | **Reviewers** judge candidates. An **admin** sets up the Google Sheet once. A **maintainer** deploys and updates the site. |
| **Browsers** | Desktop Chrome, Chromium or Edge. Firefox and Safari cannot pick folders. |
| **Systems** | Windows, macOS and Linux. |
| **Two parts** | The **page** (static website, hosted on Vercel) and a small **local helper** on each reviewer's computer. |
| **Where videos live** | On the reviewer's own computer. They are never uploaded anywhere. |
| **Where judgements go** | A small file on the reviewer's computer first, then the team's Google Sheet. |

**What works without the helper:** pick a folder with the browser's own folder picker and watch the videos.
**What needs the helper:** typing a folder path, the system folder window, saving verdicts and comments, the Google Sheet, the final report and the Excel download.

---

## 2. Quick start

### Reviewer (first time on a computer)

1. Open the site in Chrome or Edge.
2. If the page shows **Judging needs the local helper**:
   - **Windows:** click **download Setup.bat**, double-click it, wait about 2 minutes until it says Done.
   - **macOS or Linux:** click **Copy setup command**, open Terminal, paste, press Enter.
3. Reload the page. The top right says **Local helper connected**.
4. Type **Your name** (saved with every judgement) and choose the **Exam**.
5. Click **Browse** and choose the folder that holds the candidate folders (see [section 5](#5-video-folder-rules)).
6. Pick a candidate, press **Space** to play, judge, press **Ctrl+Enter** to save and go to the next one.

If Chrome asks to allow access to devices on your local network, click **Allow**. The page needs this to talk to the helper on your own computer.

### Admin (once per team)

Follow [section 7.1](#71-one-time-setup). You end with one link (the Web app URL plus `?key=` and a secret). Give that link to every reviewer. Each reviewer pastes it into **Google Sheet link** and presses **Connect** once. The page remembers it on that computer.

### Maintainer

Deploy the `web` folder to Vercel ([section 10](#10-deploying-on-vercel)). Make sure the site's address matches the one in the helper and the installers.

---

## 3. How the system fits together

```
 Reviewer's computer                                            Internet
┌─────────────────────────────────────────────┐
│  Browser (Chrome / Edge)                    │   page files   ┌──────────────┐
│  ┌───────────────────────────────────────┐  │ ◄──────────────│ Vercel       │
│  │ Reviewer page (index.html)            │  │                │ (static site)│
│  └──────────────┬────────────────────────┘  │                └──────────────┘
│                 │ http://127.0.0.1:8765     │
│  ┌──────────────▼────────────────────────┐  │  judgements    ┌──────────────┐
│  │ Local helper (helper.py)              │──┼───────────────►│ Google Apps  │
│  │  - folder window                      │  │  (background,  │ Script web   │
│  │  - streams videos to the page         │  │   queued if    │ app          │
│  │  - saves judgements, queues uploads   │  │   offline)     └──────┬───────┘
│  │  - Excel export                       │  │                       │
│  └──────────────┬────────────────────────┘  │                ┌──────▼───────┐
│                 │                           │                │ Google Sheet │
│  Video folders + review files on disk       │                │ (one per exam)│
└─────────────────────────────────────────────┘                └──────────────┘
```

**Saving a judgement, step by step**

1. The reviewer clicks a verdict or types a comment. The page waits 700 ms after typing stops (a verdict click saves immediately) and sends the judgement to the helper.
2. The helper writes it to a small file on the computer (the local backup) and returns "saved".
3. The helper puts it on an upload queue. A background thread sends queued items to the Google script in order, and the sheet row is added or replaced.
4. If the internet is down the queue waits and sends later, by itself. The page shows **N waiting to upload**.
5. The page polls the helper every 4 seconds and shows the state in the **Synced** pill.

---

## 4. The reviewer page

The page fits one screen on a window at least 1020 px wide and 600 px tall. On smaller windows it becomes a normal scrolling page.

### 4.1 Layout

| Area | What it holds |
|---|---|
| **Top bar** | Title, light/dark button, helper status (Local helper connected, Checking helper…, Helper not running, Helper not installed, Unsupported browser). |
| **Setup strip** | **Exam**, **Your name**, **Google Sheet link** with Connect, the **Synced** pill, **Setup guide**. Only shown when the helper is connected. |
| **Navigation strip** | Folder path box, **Browse**, **Reload**, **Prev**, candidate dropdown, **Next**, **Next Unreviewed**, **Mark Reviewed**. |
| **Players** | **Screen record** and **Webcam record**, each with a PLAYING/PAUSED badge, timecode, mute button and volume. A "reviewed" tick shows on both when the candidate is marked reviewed. |
| **Transport** | Play/pause, back 5 s, forward 5 s, current time, seek bar, total length, speed (0.5×, 1×, 1.5×, 2×), a sync indicator. |
| **Judgement panel** | Candidate number, saved state, a progress ring, verdict, fraud probability, comments, quick tags, **Save & next**, **Clear**, **Final report**. Only shown when the helper is connected and a candidate is selected. |
| **Final report** | A sheet over the page listing every judged candidate of the exam, with **Open in Google Sheets** and **Download Excel**. |

### 4.2 Watching

- The two recordings play together from one set of controls. The page keeps them in sync: if they drift more than 0.15 s it re-aligns the webcam and the sync indicator shows the drift.
- If the recordings differ in length, the longer one sets the timeline. When one ends it shows **Video ended** while the other continues.
- **Mark Reviewed** is a personal tick saved in this browser for this folder. It is separate from judging. Saving a verdict also marks the candidate reviewed.
- **Next Unreviewed** jumps to the next candidate without a tick.

### 4.3 Judging

| Field | Details |
|---|---|
| **Verdict** | Cheater, Unsure, Not a cheater. Clicking the active verdict again clears it. |
| **Fraud probability** | 0 to 100, from the slider (steps of 5) or typed in the number box. The label shows Low (under 30), Medium (30 to 69) or High (70 and up). |
| **Comments** | Free text, up to 4000 characters. **+ Time** adds the current screen-recording time as `[hh:mm:ss]`. The tag buttons (Looking away, Second person, Phone) add `[time] text`. |
| **Seek bar dots** | Every `[hh:mm:ss]` in the comments appears as an amber dot on the seek bar. |
| **Clear** | Empties the judgement. If verdict, comment and fraud % are all empty, the candidate's row is removed from the sheet. |
| **Save & next** | Saves, then opens the next candidate. It asks for a verdict first. |
| **Your name** | Required before anything can be saved. Up to 40 characters. |

Judgements save by themselves (700 ms after typing, immediately on a verdict click). The status beside the candidate number reads **Saving…**, then **Saved · hh:mm**.

### 4.4 Keyboard

| Key | Action |
|---|---|
| **Space** | Play or pause |
| **←** / **→** | Back or forward 5 seconds |
| **1** / **2** / **3** | Cheater / Unsure / Not a cheater (press again to clear) |
| **Ctrl+Enter** (⌘+Enter on a Mac) | Save and go to the next candidate |
| **Esc** | Close the final report or the setup guide |

Shortcuts are ignored while typing in a box. On the fraud slider the arrow keys move the slider.

### 4.5 Final report and Excel

**Final report** lists every judged candidate for the exam: candidate number, verdict, fraud %, comments, reviewer and time. It refreshes every 20 seconds while open. **LIVE** means it is reading the exam's Google Sheet. **Open in Google Sheets** opens the sheet itself.

**Download Excel** exports the exam to `.xlsx`: one worksheet named after the exam plus a **Summary** worksheet (counts per verdict, average fraud %, and judgements per reviewer).

### 4.6 What the page remembers in the browser

| Key | Holds |
|---|---|
| `theme` | `light` or `dark` once chosen (before that it follows the system). |
| `helperSeen` | Whether this browser has ever reached the helper (changes the status wording). |
| `folder` | Last video folder used with the helper. |
| `rv:exam` | Selected exam. |
| `rv:name` | Your name. |
| `rv:folder:<exam>` | Each exam remembers its own video folder. |
| `reviewed:<folder>` | The Mark Reviewed ticks for a folder. |
| `helperUrl` | Optional override of the helper address (default `http://127.0.0.1:8765`). |

---

## 5. Video folder rules

Choose the folder that **contains one folder per candidate**. Do not choose a candidate's own folder.

```
Exam videos/                 ← choose this folder
├── 5736/
│   ├── 5736_screenrecord.mp4
│   └── 5736_webcam.mp4
├── 5753/
│   ├── 5753_screenrecord.mp4
│   └── 5753_webcam.mp4
└── 5809/ ...
```

- The **folder name is the candidate number**. It cannot contain `\ / : * ? " < > |`, is at most 60 characters, and cannot be `.` or `..`.
- The screen recording is the video whose name starts with `<id>_screenrecord`. If none matches, any video whose name contains `screen` is used.
- The webcam recording is the video whose name starts with `<id>_webcam`. If none matches, any video whose name contains `webcam`, `web cam` or `camera` is used.
- Accepted types: `.mp4` (preferred), `.webm`, `.mov`, `.m4v`.
- A candidate missing either recording shows **Missing files for …** with the files that were found.
- Whether a file plays depends on the browser's codecs (H.264 `.mp4` and VP9 `.webm` are safest). The system does not convert videos.

---

## 6. The local helper

A small Python program (`web/helper.py`, FastAPI and Uvicorn) that listens on `http://127.0.0.1:8765`, only on the same computer. It is version **1.11**; the page needs 1.10 or newer for judging.

### 6.1 Installing

| System | How | Installed to | Starts at login by |
|---|---|---|---|
| **Windows** | Setup.bat (or `web\install.ps1` in PowerShell) | `%LOCALAPPDATA%\CandidateHelper` | A `CandidateHelper` entry in `HKCU\...\CurrentVersion\Run` |
| **macOS** | `curl -fsSL <site>/install.sh \| bash` | `~/Library/CandidateHelper` | A LaunchAgent (`com.candidatevideo.helper`) |
| **Linux** | the same `install.sh` command | `~/.local/share/CandidateHelper` | `~/.config/autostart/candidate-helper.desktop` |

All three installers need no admin rights. They:

1. take `helper.py` from the folder they run in, or download it from the site;
2. stop any running helper (including whatever holds port 8765);
3. create a private Python (Windows: an embedded Python 3.12; macOS and Linux: a virtual environment from your Python 3.8 or newer) and install `fastapi`, `uvicorn`, `requests`, `openpyxl`;
4. register the login start and start the helper now;
5. confirm it answers on port 8765.

Re-running the installer updates the helper. Linux and macOS need `python3` (Debian and Ubuntu also `python3-venv`).

### 6.2 Files the helper keeps

In its own folder (the install folders above):

| File | Holds |
|---|---|
| `review_config.json` | The Google Sheet link **including the secret key**, the list of exams, and a cached exam list. |
| `reviews/review_<exam>_<hash>_<reviewer>.json` | The local backup of every judgement one reviewer made in one exam. |
| `reviews/queue.json` | Judgements waiting to upload. |
| `reviews/failed.json` | Items the Google script refused (the last 200, with the error). |
| `reviews/cache_<exam>_<hash>.json` | The last report rows, used when offline. |
| `helper.log` | Errors, only when started without a console. |

### 6.3 The Browse button

| | With the helper | Without the helper |
|---|---|---|
| **Windows** | Explorer-style folder window | Browser folder picker (read-only) |
| **macOS** | Native folder window | Browser folder picker |
| **Linux** | `zenity`, else `kdialog`, else Python's Tk window | Browser folder picker |

- The folder window opens on the computer the helper runs on, starting in the folder already chosen.
- Only one folder window exists at a time. Clicking Browse again brings it to the front.
- On Linux with none of those tools, or with no desktop session, the helper answers with a clear message and you can type the path (`sudo apt install zenity` adds the window).
- Without the helper the folder box is read-only, because only the helper can open a typed path.

### 6.4 Settings (environment variables)

| Variable | Default | Use |
|---|---|---|
| `HELPER_PORT` | `8765` | Port to listen on. The page expects 8765 unless `helperUrl` is set in the browser. |
| `HELPER_ORIGIN_REGEX` | the Vercel site and `http://localhost` / `127.0.0.1` on any port | Which web addresses may call the helper. |
| `HELPER_SHEET_REGEX` | `https://script.google.com/macros/s/<id>/exec` | The only kind of link accepted as the Google Sheet link. |

### 6.5 Uninstalling

- **Windows:** end `pythonw.exe` running `CandidateHelper\helper.py`, delete the `CandidateHelper` value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, delete `%LOCALAPPDATA%\CandidateHelper`.
- **macOS:** `launchctl bootout gui/$(id -u)/com.candidatevideo.helper; rm -rf ~/Library/CandidateHelper ~/Library/LaunchAgents/com.candidatevideo.helper.plist`
- **Linux:** `pkill -f CandidateHelper/helper.py; rm -rf ~/.local/share/CandidateHelper ~/.config/autostart/candidate-helper.desktop`

Deleting the folder also deletes the local backups. Upload or export first if you still need them.

---

## 7. Google Sheet backend

The helper does not talk to Google Sheets directly. It calls a small **Google Apps Script web app** (`web/google_script.gs.txt`) that you own. The script reads and writes the sheets, so reviewers need no access to them.

### 7.1 One-time setup

The page's **Setup guide** shows the same steps.

1. Create a new empty Google Sheet. It becomes the **Exams** index.
2. In it open **Extensions → Apps Script**. Delete the sample code and paste `google_script.gs.txt`.
3. Change `SECRET` to a long random text (letters and digits) and save.
4. Choose the function `setup`, press **Run**, and allow the permissions.
5. **Deploy → New deployment → Web app**. Execute as **Me**, access **Anyone**. Copy the Web app URL.
6. The link every reviewer pastes is the URL with `?key=` and your secret on the end: `https://script.google.com/macros/s/XXXX/exec?key=YOUR_SECRET`

### 7.2 What the script creates

- **Exams** tab (in the index sheet): `Exam`, `Status`, `Sheet ID`, `Sheet link`, `Created by`, `Created on`. Set Status to `Closed` to hide an exam from reviewers' dropdowns.
- **One new Google Sheet per exam**, in the same Drive folder, with tabs:
  - **Reviews**: `Candidate no.`, `Cheater?`, `Fraud %`, `Comments`, `Reviewed by`, `Reviewed at`, one row per candidate, with colours for the three verdicts.
  - **Summary**: counts per verdict and the average fraud %.
  - **Change log**: every save and clear with time and reviewer.

All cells are plain text, so a comment starting with `=` is never treated as a formula.

### 7.3 Script actions

The helper sends JSON `POST` requests with the secret in `key`:

| `action` | Does |
|---|---|
| `ping` | Checks the link and key. |
| `listExams` | Returns the exams from the index. |
| `createExam` | Creates the exam's sheet and indexes it. |
| `save` | Adds or replaces the candidate's row. |
| `clear` | Deletes the candidate's row. |
| `rows` | Returns all rows of an exam and its sheet link. |

The script serialises requests with a lock, so two reviewers saving at once do not collide.

### 7.4 Changing the script later

Edit it in Apps Script, then **Deploy → Manage deployments → edit → New version**. The URL stays the same. To rotate the secret, change `SECRET`, redeploy, and give everyone the new link.

---

## 8. Helper API reference

Base address `http://127.0.0.1:8765`. Used by the page; documented here for maintenance.

| Method and path | Purpose | Notes |
|---|---|---|
| `GET /api/ping` | Health check | `{ok, version, python, platform}` (`windows`, `mac` or `linux`) |
| `POST /api/browse` | Open the folder window | Body `{start, title}`. Waits until the window closes. Returns `{path}` (empty if cancelled). `501` if the computer has no folder window. |
| `POST /api/browse/raise` | Bring the open folder window to the front | `{raised}`; never opens a window. |
| `GET /api/candidates?folder=` | List candidate folders | `{candidates:[{name, screen, webcam, files}]}` |
| `GET /video?folder=&path=` | Stream a video | Supports HTTP Range (seeking); `404` if the path leaves the folder. |
| `GET /api/review/config` | Is a sheet connected | `{configured, masked, has_key}` |
| `POST /api/review/config` | Connect or disconnect | Body `{sheet}` (empty disconnects). `400` for a bad link, missing key or wrong key. Replies `{…, verified}`. |
| `GET /api/review/status` | Sync state | `{configured, queued, online, last_ok, last_error}` |
| `GET /api/review/exams` | List exams | `{exams:[{name,status}], source}` where source is `sheet`, `cache` or `local`. |
| `POST /api/review/exams` | Create an exam | Body `{name, reviewer}`. Returns `{name, existing}`. |
| `POST /api/review` | Save or clear a judgement | Body `{exam, candidate, reviewer, verdict, pct, comment}` where verdict is `cheat`, `maybe`, `clean` or empty. |
| `GET /api/review/rows?exam=` | Rows for the report | `{rows, stale, sheet_url}`; `stale` means offline, showing the last copy. |
| `GET /api/review/export?exams=a\|b` | Excel file | Empty `exams` exports every exam. |

A row is `{candidate, verdict, pct, comment, reviewer, at}` with `verdict` stored as `Cheater`, `Unsure` or `Not a cheater`.

---

## 9. Data, privacy and security

**Where data lives**

| Data | Location |
|---|---|
| Videos | The reviewer's computer. Never uploaded. |
| Judgements | The reviewer's computer (backup), then the team's Google Sheet. |
| Google Sheet link and secret | `review_config.json` on each reviewer's computer. |
| Preferences and review ticks | The reviewer's browser (`localStorage`). |

**Protections**

- The helper listens on `127.0.0.1` only, so other computers cannot reach it.
- It answers only requests from allowed web addresses (the Vercel site and localhost). Anything else gets `403`.
- Video requests cannot leave the chosen folder (path traversal is blocked).
- The Google link must look like an Apps Script `/exec` link.

**Treat as sensitive**

- **The Google Sheet link contains the secret.** The script allows access for **Anyone**, so anyone holding the link can read and write the data. Share it only with reviewers, and rotate `SECRET` if it leaks.
- **Keep the allowed addresses narrow.** The helper streams files from any folder a page asks for, so never widen `ORIGIN_REGEX` to a wildcard such as all of `*.vercel.app`.
- `review_config.json` is git-ignored. Do not commit or deploy it.

---

## 10. Deploying on Vercel

`vercel.json` publishes the `web` folder as a static site with no build step:

```json
{ "buildCommand": null, "framework": null, "outputDirectory": "web" }
```

Everything in `web/` is served from your address: the page, `helper.py`, `install.ps1`, `install.sh`, `Setup.bat` and `google_script.gs.txt`. The installers download `helper.py` from there. Files outside `web/` (this document, the README) are not published.

**Your site address must match.** The helper only answers requests from one address, and the installers download from one address. The default is `https://candidate-video-suite.vercel.app`. If yours differs, change it in all four places:

| File | Setting |
|---|---|
| `web/helper.py` | `ORIGIN_REGEX` |
| `web/install.ps1` | `$Base` |
| `web/install.sh` | `BASE` (and the example in the comment) |
| `web/Setup.bat` | the URL in the PowerShell command |

Reviewers who already installed the helper must run the installer again after you change the address.

**The page must be served over http(s).** Opening `index.html` from disk (a `file://` address) does not work with the helper, because the browser sends no usable origin and the helper refuses it.

---

## 11. Project structure

```
.
├── README.md                  short overview
├── DOCUMENTATION.md           this document
├── vercel.json                publishes web/ as a static site
├── .gitignore
└── web/
    ├── index.html             the reviewer page (HTML, CSS and JavaScript in one file)
    ├── helper.py              the local helper
    ├── install.ps1            Windows installer
    ├── Setup.bat              starts install.ps1 from the site
    ├── install.sh             macOS and Linux installer
    └── google_script.gs.txt   the Google Apps Script
```

**`index.html`** has four scripts, in order: the theme set-up in the head, then the core (helper connection, theme, toast, folder and Browse, candidate list, player), then the judging code (exams, saving, report, Excel, setup guide), then a short start-up call. The player and the judging code talk through a few hooks: `rvEnter`, `rvOnList`, `rvOnSelect`, `rvTag`, `rvMarks`.

**`helper.py`** is organised as: imports and settings, origin guard, ping, folder windows (Windows, macOS, Linux), candidates and video streaming, then the review section (config, queue, local backup, API, Excel).

---

## 12. Maintenance and development

### Updating

- **Page:** commit and push; Vercel redeploys. Reviewers get it on their next reload.
- **Helper:** reviewers re-run the installer (Setup.bat or the setup command). Raise the version string in `helper.py` (`ping`) when you change behaviour the page depends on, and the matching check in the judging code of `index.html` (`verOk`).

### Running locally for development

```
pip install fastapi uvicorn requests openpyxl
python web/helper.py                                   # the helper on port 8765
python -m http.server 8000 --directory web             # the page at http://localhost:8000
```

`localhost` on any port is an allowed origin. Stop an installed helper first, because port 8765 is single-use.

### Checks worth running after a change

- `python -m pyflakes web/helper.py` and `bash -n web/install.sh` for syntax and unused code.
- Open the page with the helper running and with it stopped. Both must render without console errors.
- Pick a folder, play both videos, seek, change speed, mute, save a verdict, reload (the verdict must come back), open the final report, download the Excel file.
- With the internet off, save a judgement, then reconnect: the **waiting to upload** count must drop to zero.

### Design notes

- The page works as a static file with no build step and no libraries. Keep it that way.
- Colours, spacing and fonts are CSS variables at the top of the stylesheet.
- All JavaScript state reachable from the other scripts is deliberately global (the scripts are classic, not modules).

---

## 13. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Status says **Helper not installed** or **Helper not running** | Install the helper (section 2), or start it. If it is installed and running, the site address probably does not match the helper's allowed address (section 10). |
| The judgement panel and setup strip are missing | The helper is not connected, or is older than 1.10. A banner says so. Re-run the installer. A candidate must also be selected for the judgement panel. |
| Folder box is read-only | No helper. Use **Browse**, or install the helper to type paths. |
| **Missing files for …** | The wrong level was chosen, or the file names do not match (section 5). The message lists what was found. |
| A video stays black or will not play | The browser cannot decode that file's codec. Re-export the video as H.264 `.mp4`. |
| **Wrong key in the Google Sheet link** | The secret after `?key=` does not match `SECRET` in the script. Paste the link again. |
| **N waiting to upload** does not drop | The computer is offline, or Google is unreachable. It uploads by itself when the connection returns. |
| The report shows **offline: showing the last saved copy** | The helper could not reach the Google script. Judgements are safe locally. |
| **Download Excel** fails | The `openpyxl` library is missing. Re-run the installer. |
| Chrome asks about local network access | Click **Allow**. Without it the page cannot reach the helper. |
| No folder window on Linux | Install `zenity`, and start the helper from your desktop session (the installer's autostart entry does). Otherwise type the path. |
| Page is blank or never connects when opened from disk | Open it from the site or a local web server, not as a file. |
| The helper does not start after install | Look at `helper.log` in the helper folder. Antivirus or a firewall blocking Python is the usual cause. |

---

## 14. Limits and behaviours to know

- **One row per candidate per exam.** If two reviewers judge the same candidate, the later save replaces the earlier row in the **Reviews** tab. Earlier versions stay in the **Change log** tab. Assign each candidate to one reviewer to avoid overwriting.
- **Names:** exam names up to 80 characters without `\ / : * ? " < > | [ ]`; reviewer names up to 40; candidate numbers up to 60 (see section 5); comments up to 4000.
- **Fraud %** is a whole number from 0 to 100.
- **Excel sheet names** are cut to 31 characters with forbidden characters replaced by `-`.
- **Timestamps** in comments use the screen recording's clock.
- **Mark Reviewed ticks** live in one browser and are not shared; the sheet's judgements are what the team shares.
- **Offline use:** the page itself loads from Vercel, so the first load needs the internet. After that, judging works offline and uploads later.
- **The system no longer downloads or converts videos.** Videos must already be on the computer.
