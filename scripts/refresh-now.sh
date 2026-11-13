#!/usr/bin/env bash
# Ask the running scheduler to refresh now (fetch every feed, validate, load, swap) instead of waiting for midnight.
# It starts within a minute; follow it with: docker compose logs -f scheduler
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p work && touch work/REFRESH_NOW
echo "Refresh requested. Follow: docker compose logs -f scheduler   (result: work/last_refresh.txt)"
