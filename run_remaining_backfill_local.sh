#!/usr/bin/env bash
# One-off local runner for the remaining hourly_revenue_by_channel/_by_sector backfill
# (2024-07-15 -> 2026-06-23), moved off GitHub Actions because the account hit a
# billing/spending-limit block there. Mirrors backfill_hourly_revenue_orchestrator.yml's
# chunk plan, just run serially on this machine instead of self-dispatched workflow runs.
set -uo pipefail
cd "$(dirname "$0")"
source .env 2>/dev/null || true
set -a; source .env; set +a

LOG=backfill_remaining_local.log
echo "=== Started $(date) ===" >> "$LOG"

# Resuming after a network-reset crash (WinError 10054) on 2025-07-30 13:30, which
# got through 2025-01-11 -> ~2025-05-19 of the product_sales chunk before dying.
PLAN=(
  "get_product_sales.py 2025-05-20 2025-07-09"
  "get_sector_revenue.py 2025-01-11 2025-07-09"
  "get_product_sales.py 2025-07-10 2026-01-05"
  "get_sector_revenue.py 2025-07-10 2026-01-05"
  "get_product_sales.py 2026-01-06 2026-06-23"
  "get_sector_revenue.py 2026-01-06 2026-06-23"
)

notify() {
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=$1" > /dev/null
}

MAX_RETRIES=5

for step in "${PLAN[@]}"; do
  read -r SCRIPT FROM TO <<< "$step"
  attempt=1
  while :; do
    echo "" >> "$LOG"
    echo "[$(date)] Running $SCRIPT $FROM -> $TO (attempt $attempt/$MAX_RETRIES)" >> "$LOG"
    if python "$SCRIPT" --from-date "$FROM" --to-date "$TO" >> "$LOG" 2>&1; then
      echo "[$(date)] OK: $SCRIPT $FROM -> $TO" >> "$LOG"
      break
    fi
    echo "[$(date)] FAILED (attempt $attempt): $SCRIPT $FROM -> $TO" >> "$LOG"
    if [ "$attempt" -ge "$MAX_RETRIES" ]; then
      notify "⚠️ Локальный бэкфилл упал на ${SCRIPT} ${FROM}->${TO} после ${MAX_RETRIES} попыток, смотри backfill_remaining_local.log"
      exit 1
    fi
    attempt=$((attempt+1))
    sleep 30
  done
done

echo "=== All chunks done $(date) ===" >> "$LOG"
notify "✅ Локальный бэкфилл hourly_revenue_by_channel/_by_sector (2024-07-15 → 2026-06-23) завершён — все чанки прошли."
