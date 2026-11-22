# Candidate Video Reviewer

Browser app for reviewing candidate screen and webcam recordings side by side, judging each candidate (verdict, fraud %, timestamped comments) and sharing the results through a team Google Sheet, with an Excel download.
Works in Chrome, Chromium or Edge on desktop (Windows, macOS and Linux).

**Full documentation: [DOCUMENTATION.md](DOCUMENTATION.md)** (setup, how it works, helper API, Google Sheet backend, deployment, troubleshooting).

- Watching videos needs nothing installed: **Browse** uses the browser's own folder picker.
- Judging (verdicts, Google Sheet, Excel) needs the small local helper below.

## Files

- `web/index.html` - the page (static; deployed on Vercel via `vercel.json`)
- `web/helper.py` - the local helper: folder window, video streaming, saving judgements, Google Sheet sync, Excel export
- `web/install.ps1`, `web/Setup.bat` - one-time Windows installer (private Python, no admin rights); the helper then starts at every login. To install from this folder without the website, run `web\install.ps1` in PowerShell
- `web/install.sh` - the same for macOS and Linux
- `web/google_script.gs.txt` - the Google Apps Script that turns a Google Sheet into the team's exam list (the page's **Setup guide** walks through it)

The helper only accepts requests from https://candidate-video-suite.vercel.app and localhost (override with `HELPER_ORIGIN_REGEX`).

## How judging is saved

Every judgement is written to a small file on the PC first, then queued and sent to the exam's Google Sheet by a background thread. Offline, the queue waits and uploads later. The helper keeps its data (`review_config.json` with the Google Sheet link and key, and the `reviews` folder) next to `helper.py`, which is `%LOCALAPPDATA%\CandidateHelper` on Windows and `~/Library/CandidateHelper` on a Mac. Those files are git-ignored.

## The Browse button

| | With the helper running | Without the helper |
|---|---|---|
| Windows | Explorer-style folder window | the browser's folder picker (Chrome or Edge) |
| macOS | native folder window | the browser's folder picker (Chrome or Edge) |
| Linux | `zenity`, else `kdialog`, else Python's Tk window | the browser's folder picker (Chrome or Edge) |

On Linux, if none of those tools is installed, the page says so and you can type the folder path instead (for example `sudo apt install zenity` adds the window). Firefox and Safari cannot pick folders without the helper.

## macOS

Use Chrome or Edge (Safari cannot pick folders or talk to a local helper). If the helper is missing, the page shows a **Copy setup command** button: paste it into Terminal and press Enter. It runs `web/install.sh`, which creates a private Python environment in `~/Library/CandidateHelper` and installs a LaunchAgent so the helper starts at every login. The Browse button opens the native macOS folder window. Remove it with `launchctl bootout gui/$(id -u)/com.candidatevideo.helper; rm -rf ~/Library/CandidateHelper ~/Library/LaunchAgents/com.candidatevideo.helper.plist`.

## Linux

Use Chrome, Chromium or Edge. Without the helper, **Browse** opens the browser's own folder picker, which already works. For judging, the page shows a **Copy setup command** button: paste it into Terminal and press Enter. It runs `web/install.sh`, which needs Python 3.8 or newer (on Debian and Ubuntu also `python3-venv`), creates a private environment in `~/.local/share/CandidateHelper`, adds a desktop autostart entry (`~/.config/autostart/candidate-helper.desktop`) and starts the helper. The folder window uses `zenity`, else `kdialog`, else Python's Tk; with none of them installed, type the folder path instead. Remove it with `pkill -f CandidateHelper/helper.py; rm -rf ~/.local/share/CandidateHelper ~/.config/autostart/candidate-helper.desktop`.

## Deploying on Vercel

`vercel.json` publishes the `web` folder as a static site, so `index.html`, `helper.py`, `install.ps1`, `install.sh`, `Setup.bat` and `google_script.gs.txt` are all served from your Vercel address. The installers download `helper.py` from there. The helper only answers requests from one web address, so if your Vercel address is not `https://candidate-video-suite.vercel.app`, change it in `web/helper.py` (`ORIGIN_REGEX`), `web/install.ps1` (`$Base`), `web/install.sh` (`BASE`) and `web/Setup.bat`.
