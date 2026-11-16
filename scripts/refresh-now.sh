#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p work && touch work/REFRESH_NOW
echo "Refresh requested. Follow: tail -f logs/pipeline.log   (result: logs/last_refresh.txt)"
