#!/usr/bin/env bash
#
# Build an ad-hoc-signed macOS app bundle and launch its ShellBot2 daemon.
#
# Usage:
#   bash scripts/build_macos_app.sh

set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
    echo "This script only builds macOS app bundles." >&2
    exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="ShellBot2"
APP_PATH="$ROOT_DIR/dist/$APP_NAME.app"
SHELLBOT_DATADIR="${SHELLBOT_DATADIR:-$HOME/.shellbot2}"
PID_FILE="$SHELLBOT_DATADIR/daemon.pid"

cd "$ROOT_DIR"
export SHELLBOT_DATADIR

if [[ -s "$PID_FILE" ]]; then
    existing_pid="$(<"$PID_FILE")"
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" 2>/dev/null; then
        echo "ShellBot2 daemon is already running (PID $existing_pid)." >&2
        exit 1
    fi
fi

uv run --with pyinstaller pyinstaller \
    --clean \
    --noconfirm \
    --windowed \
    --name "$APP_NAME" \
    --osx-bundle-identifier "com.shellbot2.app" \
    --paths "$ROOT_DIR/src" \
    --collect-submodules shellbot2.tools \
    --collect-submodules shellbot2.sensors \
    --collect-all desktop_notifier \
    --recursive-copy-metadata pydantic-ai \
    "$ROOT_DIR/src/shellbot2/__init__.py"

codesign --force --deep --sign - "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

echo "Built and signed $APP_PATH"
if ! open -n "$APP_PATH"; then
    echo "macOS reported an app-launch error; checking daemon readiness anyway." >&2
fi

for _ in {1..80}; do
    if [[ -s "$PID_FILE" ]]; then
        daemon_pid="$(<"$PID_FILE")"
        if [[ "$daemon_pid" =~ ^[0-9]+$ ]] && kill -0 "$daemon_pid" 2>/dev/null; then
            echo "ShellBot2 daemon started (PID $daemon_pid)."
            exit 0
        fi
    fi
    sleep 0.25
done

echo "ShellBot2.app exited before its daemon became ready." >&2
echo "Run \"$APP_PATH/Contents/MacOS/$APP_NAME\" in a terminal to see its startup error." >&2
exit 1
