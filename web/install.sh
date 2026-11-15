#!/bin/bash
# Candidate Video Helper - one-time setup for macOS (no admin, no Homebrew needed).
# Run it by pasting into Terminal:
#   curl -fsSL https://candidate-video-suite.vercel.app/install.sh | bash
set -u

BASE="https://candidate-video-suite.vercel.app"
DIR="$HOME/Library/CandidateHelper"
LABEL="com.candidatevideo.helper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT=8765

step() { echo "[+] $*"; }
fail() { echo "[X] $*" >&2; exit 1; }

echo "========================================"
echo "  Candidate Video Helper - One-time setup (macOS)"
echo "  (no admin rights or Homebrew needed)"
echo "========================================"

[ "$(uname -s)" = "Darwin" ] || fail "This installer is for macOS. On Windows use Setup.bat."

# 1. Python 3 (macOS ships one with the Command Line Tools)
PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /usr/bin/python3; do
  if [ -x "$c" ] && "$c" -c 'import sys,venv; sys.exit(0 if sys.version_info >= (3,8) else 1)' >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[X] Python 3 was not found."
  echo "    A window may have appeared asking to install the Command Line Tools - click Install, wait for it to finish,"
  echo "    then paste the setup command again. (Or install Python from https://www.python.org/downloads/macos/)"
  xcode-select --install >/dev/null 2>&1 || true
  exit 1
fi
step "Using $("$PY" --version) ($PY)"

mkdir -p "$DIR" "$HOME/Library/LaunchAgents"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 2. Helper script first, so a failed download never leaves you without a working helper
HERE=""
[ -f "${BASH_SOURCE[0]:-}" ] && HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "$HERE" ] && [ -f "$HERE/helper.py" ]; then
  step "Using local helper.py"; cp "$HERE/helper.py" "$TMP/helper.py"
else
  step "Downloading helper..."
  curl -fsSL --retry 5 --connect-timeout 20 -o "$TMP/helper.py" "$BASE/helper.py" || fail "Could not download helper.py - check your internet connection and run setup again."
fi

# Stop a running copy so files can be replaced
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
OLD="$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null || true)"
[ -n "$OLD" ] && { step "Stopping old helper (pid $OLD)..."; kill $OLD 2>/dev/null || true; sleep 1; }
cp "$TMP/helper.py" "$DIR/helper.py"

# 3. Private Python environment with the libraries (and a bundled FFmpeg build)
VPY="$DIR/venv/bin/python"
if [ ! -x "$VPY" ] || ! "$VPY" -c "import fastapi, uvicorn, requests, imageio_ffmpeg" >/dev/null 2>&1; then
  rm -rf "$DIR/venv"
  step "Creating private Python environment..."
  "$PY" -m venv "$DIR/venv" || fail "Could not create the Python environment."
  step "Installing libraries (fastapi, uvicorn, requests, ffmpeg) - about 1 minute..."
  "$VPY" -m pip install --quiet --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
  "$VPY" -m pip install --quiet --disable-pip-version-check fastapi uvicorn requests imageio-ffmpeg \
    || fail "Library installation failed. Check your internet connection and run setup again."
else
  step "Python and libraries already installed."
fi
"$VPY" -m py_compile "$DIR/helper.py" || fail "helper.py is damaged (bad download). Run setup again."

# 4. FFmpeg: use one that is already installed, otherwise the build that came with imageio-ffmpeg
if command -v ffmpeg >/dev/null 2>&1 || [ -x /opt/homebrew/bin/ffmpeg ] || [ -x /usr/local/bin/ffmpeg ] || [ -x "$DIR/ffmpeg/ffmpeg" ]; then
  step "FFmpeg already available."
else
  step "Setting up FFmpeg..."
  SRC="$("$VPY" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())' 2>/dev/null || true)"
  if [ -n "$SRC" ] && [ -f "$SRC" ]; then
    mkdir -p "$DIR/ffmpeg"; cp "$SRC" "$DIR/ffmpeg/ffmpeg"; chmod +x "$DIR/ffmpeg/ffmpeg"
    xattr -dr com.apple.quarantine "$DIR/ffmpeg" 2>/dev/null || true
  else
    echo "[!] FFmpeg could not be set up. Downloads still work; Convert (.mp4) needs FFmpeg (brew install ffmpeg)."
  fi
fi

# 5. Start at login (and keep it running)
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$VPY</string><string>$DIR/helper.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$DIR/helper.log</string>
  <key>StandardErrorPath</key><string>$DIR/helper.log</string>
</dict>
</plist>
EOF

step "Starting helper..."
: > "$DIR/helper.log"
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST" 2>/dev/null || true
launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

UP=""
for i in $(seq 1 40); do
  sleep 0.75
  R="$(curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/ping" 2>/dev/null || true)"
  [ -n "$R" ] && { UP=1; break; }
done

echo
if [ -n "$UP" ]; then
  echo "[+] Done. Helper is running: $R"
  echo "[+] It starts automatically at every login. Go back to the web page and click Re-check."
  echo "    (If macOS asks to allow Python to find folders or files, click Allow.)"
else
  echo "[X] Installed, but the helper did not respond on port $PORT." >&2
  echo "    Try signing out and in again. --- helper.log ---" >&2
  tail -n 25 "$DIR/helper.log" 2>/dev/null | sed 's/^/    /' >&2
  exit 1
fi
