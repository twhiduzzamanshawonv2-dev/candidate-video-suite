# Candidate Video Suite

Browser app for downloading, converting (FFmpeg.wasm) and reviewing candidate screen/webcam recordings.
Works in Chrome/Edge on desktop; nothing to install.

- `web/` - the site (static; deployed on Vercel via `vercel.json`)
- `run_local.bat` - serve the site locally at http://localhost:8000
- `helper.py`, `helper_setup/`, `start_helper.bat` - local helper: downloads straight to the PC when source servers block browsers (CORS). Users run `web/CandidateHelperSetup.zip` once (rebuild it from `helper_setup/` after editing `helper.py`). It only accepts requests from https://candidate-video-suite.vercel.app and localhost (override with `HELPER_ORIGIN_REGEX`).


