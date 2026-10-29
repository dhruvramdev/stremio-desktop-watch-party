#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Building native Mach-O interposition shim..."
make clean
make
echo "Shim built successfully: $SCRIPT_DIR/libstremio-mpv-ipc-shim.dylib"

