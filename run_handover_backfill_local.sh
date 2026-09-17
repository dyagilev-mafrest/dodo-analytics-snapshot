#!/usr/bin/env bash
# One-off local backfill for handover_time_stats (2024-01-15 -> today), run
# alongside the still-in-progress hourly-revenue backfill — concurrency in
# get_handover_time_stats.py is capped at 7 (per-unit) to share API/network
# load rather than compound it.
set -uo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a

LOG=handover_backfill_local.log
echo "=== Started $(date) ===" >> "$LOG"

FROM=2024-01-15
TO=$(date +%Y-%m-%d)
MAX_RETRIES=5
attempt=1

while :; do
  echo "" >> "$LOG"
  echo "[$(date)] Running get_handover_time_stats.py $FROM -> $TO (attempt $attempt/$MAX_RETRIES)" >> "$LOG"
  if python get_handover_time_stats.py --from-date "$FROM" --to-date "$TO" >> "$LOG" 2>&1; then
    echo "[$(date)] OK" >> "$LOG"
    break
  fi
  echo "[$(date)] FAILED (attempt $attempt)" >> "$LOG"
  if [ "$attempt" -ge "$MAX_RETRIES" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
      --data-urlencode "text=⚠️ Локальный бэкфилл handover_time_stats упал после ${MAX_RETRIES} попыток, смотри handover_backfill_local.log"
    exit 1
  fi
  attempt=$((attempt+1))
  sleep 30
done

curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=✅ Локальный бэкфилл handover_time_stats (2024-01-15 → сегодня) завершён."
