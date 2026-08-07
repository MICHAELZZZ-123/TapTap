#!/bin/bash
# PACKAGING: This launcher targets the generated Linux artifact, not TapTap.exe.
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/dist/TapTap" "$@"
