#!/bin/sh
# Bez RUN_AT vyrobí díl hned a skončí (hodí se na ladění i na cron zvenku).
# S RUN_AT=03:10 zůstane běžet a spustí se každý den v ten čas — žádný cron
# démon v kontejneru, jen spánek do dalšího termínu.
set -e

if [ ! -f "${PODCAST_CONFIG:-/data/config.yaml}" ]; then
  echo "[start] chybí ${PODCAST_CONFIG:-/data/config.yaml}, kopíruji vzor — uprav ho a restartuj"
  cp /app/config.example.yaml "${PODCAST_CONFIG:-/data/config.yaml}"
  exit 1
fi

run() {
  echo "[start] $(date '+%F %T') spouštím díl"
  python -m podcast.run ${RUN_ARGS:-} || echo "[start] díl selhal (návratový kód $?)"
}

if [ -z "$RUN_AT" ]; then
  run
  exit 0
fi

echo "[start] denní běh v $RUN_AT (TZ=$TZ)"
while true; do
  now=$(date +%s)
  next=$(date -d "today $RUN_AT" +%s 2>/dev/null || date -d "$RUN_AT" +%s)
  [ "$next" -le "$now" ] && next=$(date -d "tomorrow $RUN_AT" +%s)
  sleep $((next - now))
  run
done
