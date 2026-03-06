#!/bin/bash
# Usage: recyclarr_sync.sh <clean_config> <recyclarr_config>
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLEAN_CONFIG="$1"
RECYCLARR_CONFIG="$2"

if [ -z "$CLEAN_CONFIG" ] || [ -z "$RECYCLARR_CONFIG" ]; then
  echo "Usage: $0 <clean_config.yml> <recyclarr_config.yml>"
  exit 1
fi

echo "=== Cleaning ==="
python flemmarr.py "$CLEAN_CONFIG"
if [ $? -ne 0 ]; then
  echo
  echo "Cleanup had errors. Check output above."
  read -p "Press Enter to continue anyway, or Ctrl+C to abort..."
fi

echo
echo "=== Clearing recyclarr state ==="
rm -rf "$APPDATA/recyclarr/state"

echo
echo "=== Syncing Recyclarr ==="
export RECYCLARR_CONFIG_DIR="$SCRIPT_DIR"
cmd //c "recyclarr sync --config $RECYCLARR_CONFIG"

echo
read -p "Press Enter to exit..."
