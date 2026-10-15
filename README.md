# Candidate Video Suite

Browser app for downloading, converting (FFmpeg.wasm) and reviewing candidate screen/webcam recordings.
Works in Chrome/Edge on desktop; nothing to install.

- `web/` - the site (static; deployed on Vercel via `vercel.json`)
- `run_local.bat` - serve the site locally at http://localhost:8000
- `web/helper.py`, `web/install.ps1`, `web/Setup.bat` - local helper + one-time installer (served by the site; downloads straight to the PC when source servers block browsers). `start_helper.bat` runs the helper by hand. It only accepts requests from https://candidate-video-suite.vercel.app and localhost (override with `HELPER_ORIGIN_REGEX`).


