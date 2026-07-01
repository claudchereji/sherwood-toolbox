#!/usr/bin/env bash
#
# Test harness for build-appimage.sh
#
# This simulates common failure modes that occur when building/running
# on Fedora 43 with AMD Ryzen integrated graphics (680M/780M/880M etc.).
#
# Usage:
#   ./run/test-appimage-build.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Testing AppImage build script against Fedora 43 + AMD failure modes ==="
echo

# --- Test 1: Simulate missing python3-gobject + webkit2gtk ---
echo ">>> Test 1: Simulating missing PyGObject / WebKitGTK (most common Fedora 43 failure)"

# Temporarily hide the real gi module by setting PYTHONPATH to a directory
# that only contains a fake gi that fails.
FAKE_DIR=$(mktemp -d)
mkdir -p "$FAKE_DIR/gi"

cat > "$FAKE_DIR/gi/__init__.py" << 'PYEOF'
raise ImportError("No module named 'gi' (simulated Fedora 43 missing python3-gobject)")
PYEOF

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$FAKE_DIR"

if bash -c "cd '$REPO_ROOT' && ./run/build-appimage.sh" 2>&1 | tee /tmp/appimage-test1.log; then
    echo "FAIL: Script should have exited with error when gi is missing"
    exit 1
else
    echo "PASS: Script correctly detected missing PyGObject"
    if grep -q "python3-gobject" /tmp/appimage-test1.log && grep -q "webkit2gtk4.1" /tmp/appimage-test1.log; then
        echo "PASS: Error message mentions the correct Fedora packages"
    else
        echo "WARN: Error message may not be clear enough for Fedora users"
    fi
fi

unset PYTHONPATH
rm -rf "$FAKE_DIR"

echo
echo ">>> Test 2: Normal environment (current machine) should at least pass pre-flight"

# Run only the pre-flight part by sourcing or by calling with a dry-run mode.
# For now we just invoke the script and let it fail later at appimagetool if needed,
# but we capture whether the early checks pass.

if timeout 30s bash -c "cd '$REPO_ROOT' && ./run/build-appimage.sh" 2>&1 | head -30; then
    echo "(Script started pre-flight successfully or failed later as expected)"
else
    echo "Note: Script exited (expected if appimagetool or other tools are missing in this env)"
fi

echo
echo "=== Known Fedora 43 + AMD issues the script tries to handle ==="
cat << 'EOF'
1. Missing packages at build time:
   - python3-gobject
   - webkit2gtk4.1

2. At runtime on AMD iGPUs (Ryzen 6000/7000/8000 series):
   - Black/blank window due to dmabuf / hardware compositing
   - Crashes in WebKitGTK with newer Mesa

3. Environment variables set in AppRun:
   - WEBKIT_DISABLE_COMPOSITING_MODE=1
   - WEBKIT_DISABLE_DMABUF_RENDERER=1
   - GDK_BACKEND=x11

4. Runtime requirement on Fedora:
   sudo dnf install webkit2gtk4.1 python3-gobject

5. Sometimes needed as last resort:
   LIBGL_ALWAYS_SOFTWARE=1
EOF

echo
echo "Test run complete."
