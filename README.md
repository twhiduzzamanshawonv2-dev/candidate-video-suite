# Candidate Video Suite

Browser app for downloading, converting (FFmpeg.wasm) and reviewing candidate screen/webcam recordings.
Works in Chrome/Edge on desktop; nothing to install.

- `web/` - the site (static; deployed on Vercel via `vercel.json`)
- `run_local.bat` - serve the site locally at http://localhost:8000
- `helper.py`, `start_helper.bat` - optional local helper (native FFmpeg, bypasses CORS). Set `HELPER_ORIGIN_REGEX` to your Vercel domain.
