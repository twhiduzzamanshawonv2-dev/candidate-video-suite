#!/bin/bash
# Candidate Video Helper - one-time setup for macOS and Linux (no admin, no Homebrew needed).
# Run it by pasting into Terminal:
#   curl -fsSL https://candidate-video-suite.vercel.app/install.sh | bash
set -u

BASE="https://candidate-video-suite.vercel.app"
PORT=8765

step() { echo "[+] $*"; }
fail() { echo "[X] $*" >&2; exit 1; }

OS="$(uname -s)"
case "$OS" in
  Darwin)
    NAME="macOS"
    DIR="$HOME/Library/CandidateHelper"
    LABEL="com.candidatevideo.helper"
    PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
    ;;
  Linux)
    NAME="Linux"
    DIR="${XDG_DATA_HOME:-$HOME/.local/share}/CandidateHelper"
    AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/candidate-helper.desktop"
    ;;
  *) fail "This installer is for macOS and Linux. On Windows use Setup.bat." ;;
esac

echo "========================================"
echo "  Candidate Video Helper - One-time setup ($NAME)"
echo "  (no admin rights needed)"
echo "========================================"

# 1. Python 3.8 or newer
PY=""
if [ "$OS" = "Darwin" ]; then
  CANDIDATES="/opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /usr/bin/python3"
else
  CANDIDATES="$(command -v python3 2>/dev/null) /usr/bin/python3 /usr/local/bin/python3"
fi
for c in $CANDIDATES; do
  if [ -x "$c" ] && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[X] Python 3.8 or newer was not found."
  if [ "$OS" = "Darwin" ]; then
    echo "    A window may have appeared asking to install the Command Line Tools - click Install, wait for it to finish,"
    echo "    then paste the setup command again. (Or install Python from https://www.python.org/downloads/macos/)"
    xcode-select --install >/dev/null 2>&1 || true
  else
    echo "    Install it with your package manager (for example: sudo apt install python3 python3-venv), then paste the setup command again."
  fi
  exit 1
fi
step "Using $("$PY" --version) ($PY)"

mkdir -p "$DIR"
[ "$OS" = "Darwin" ] && mkdir -p "$HOME/Library/LaunchAgents"
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
if [ "$OS" = "Darwin" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
else
  pkill -f "CandidateHelper/helper.py" >/dev/null 2>&1 || true
fi
if command -v lsof >/dev/null 2>&1; then
  OLD="$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null || true)"
  [ -n "$OLD" ] && { step "Stopping old helper (pid $OLD)..."; kill $OLD 2>/dev/null || true; sleep 1; }
fi
cp "$TMP/helper.py" "$DIR/helper.py"

# 3. Private Python environment with the libraries
VPY="$DIR/venv/bin/python"
if [ ! -x "$VPY" ] || ! "$VPY" -c "import fastapi, uvicorn, requests, openpyxl" >/dev/null 2>&1; then
  rm -rf "$DIR/venv"
  step "Creating private Python environment..."
  if ! "$PY" -m venv "$DIR/venv"; then
    [ "$OS" = "Linux" ] && echo "    On Debian and Ubuntu this needs:  sudo apt install python3-venv" >&2
    fail "Could not create the Python environment."
  fi
  step "Installing libraries (fastapi, uvicorn, requests, openpyxl) - about 1 minute..."
  "$VPY" -m pip install --quiet --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
  "$VPY" -m pip install --quiet --disable-pip-version-check fastapi uvicorn requests openpyxl \
    || fail "Library installation failed. Check your internet connection and run setup again."
else
  step "Python and libraries already installed."
fi
"$VPY" -m py_compile "$DIR/helper.py" || fail "helper.py is damaged (bad download). Run setup again."

# 4. Start at login, and start it now
if [ "$OS" = "Darwin" ]; then
  # LaunchAgent: keeps the helper running
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
else
  # Desktop autostart entry: it runs inside your login session, so the folder window can open on your screen
  mkdir -p "$(dirname "$AUTOSTART")"
  cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Candidate Video Helper
Comment=Local helper for the Candidate Video Reviewer
Exec="$VPY" "$DIR/helper.py"
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
  step "Starting helper..."
  : > "$DIR/helper.log"
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup "$VPY" "$DIR/helper.py" >"$DIR/helper.log" 2>&1 < /dev/null &
  else
    nohup "$VPY" "$DIR/helper.py" >"$DIR/helper.log" 2>&1 < /dev/null &
  fi
fi

UP=""
for i in $(seq 1 40); do
  sleep 0.75
  R="$(curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/ping" 2>/dev/null || true)"
  [ -n "$R" ] && { UP=1; break; }
done

echo
if [ -n "$UP" ]; then
  echo "[+] Done. Helper is running: $R"
  echo "[+] It starts automatically at every login. Go back to the web page and reload it."
  if [ "$OS" = "Darwin" ]; then
    echo "    (If macOS asks to allow Python to find folders or files, click Allow.)"
  elif ! command -v zenity >/dev/null 2>&1 && ! command -v kdialog >/dev/null 2>&1; then
    echo "[!] No folder-window tool found, so Browse will ask you to type the folder path."
    echo "    For the folder window, install one:  sudo apt install zenity   (or your system's equivalent)"
  fi
else
  echo "[X] Installed, but the helper did not respond on port $PORT." >&2
  echo "    Try signing out and in again. --- helper.log ---" >&2
  tail -n 25 "$DIR/helper.log" 2>/dev/null | sed 's/^/    /' >&2
  exit 1
fi
