# Candidate Video Suite

Browser app for downloading, converting (FFmpeg.wasm) and reviewing candidate screen/webcam recordings.
Works in Chrome/Edge on desktop (Windows and macOS); nothing to install.

- `web/` - the site (static; deployed on Vercel via `vercel.json`)
- `run_local.bat` - serve the site locally at http://localhost:8000
- `web/helper.py`, `web/install.ps1`, `web/Setup.bat` - local helper + one-time installer (served by the site; downloads straight to the PC when source servers block browsers). `start_helper.bat` runs the helper by hand. It only accepts requests from https://candidate-video-suite.vercel.app and localhost (override with `HELPER_ORIGIN_REGEX`).

## macOS

Use Chrome or Edge (Safari cannot pick folders or talk to a local helper). On the site, open **System check** and click **Copy setup command**, paste it into Terminal and press Enter. It runs `web/install.sh`, which creates a private Python environment in `~/Library/CandidateHelper`, sets up FFmpeg (an existing one, e.g. from Homebrew, is reused), and installs a LaunchAgent so the helper starts at every login. The Browse button opens the native macOS folder window. Remove it with `launchctl bootout gui/$(id -u)/com.candidatevideo.helper; rm -rf ~/Library/CandidateHelper ~/Library/LaunchAgents/com.candidatevideo.helper.plist`.
