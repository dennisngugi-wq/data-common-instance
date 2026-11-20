#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
mkdir -p pipeline/work && touch pipeline/work/REFRESH_NOW
echo "Refresh requested. Follow: tail -f pipeline/logs/pipeline.log   (result: pipeline/logs/last_refresh.txt)"
