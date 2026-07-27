#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
if command -v x-terminal-emulator &>/dev/null; then
    x-terminal-emulator -e "$DIR/dist/TapTap" &
elif command -v gnome-terminal &>/dev/null; then
    gnome-terminal -- "$DIR/dist/TapTap" &
else
    "$DIR/dist/TapTap" &
    echo "App started. Open http://127.0.0.1:5000 in your browser."
    echo "Press Enter to close this window."
    read
fi
