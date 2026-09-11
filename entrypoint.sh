#!/bin/sh
# Bez RUN_AT vyrobí díl hned a skončí (hodí se na ladění i na cron zvenku).
# S RUN_AT=03:10 zůstane běžet a spustí se každý den v ten čas — žádný cron
# démon v kontejneru, jen spánek do dalšího termínu.
# S PODCAST_ADMIN_PASSWORD navíc běží administrace klíčů na ADMIN_PORT.
set -e

CONFIG="${PODCAST_CONFIG:-/data/config.yaml}"
if [ ! -f "$CONFIG" ]; then
  echo "[start] chybí $CONFIG, kopíruji vzor — uprav ho a restartuj"
  cp /app/config.example.yaml "$CONFIG"
  exit 1
fi

# Web = administrace (heslo) + feed s díly (token). Bez hesla neběží ani jedno,
# ať se hotové díly nikdy nevystaví bez ověření.
if [ -n "$PODCAST_ADMIN_PASSWORD" ]; then
  echo "[start] web na portu ${ADMIN_PORT:-8089} (administrace + feed)"
  uvicorn podcast.admin:app --host 0.0.0.0 --port "${ADMIN_PORT:-8089}" &
else
  echo "[start] web neběží: chybí PODCAST_ADMIN_PASSWORD (feed ani administrace nejsou dostupné)"
fi

run() {
  echo "[start] $(date '+%F %T') spouštím díl"
  python -m podcast.run ${RUN_ARGS:-} || echo "[start] díl selhal (návratový kód $?)"
}

if [ -z "$RUN_AT" ]; then
  run
  # s webem zůstat naživu: feed musí být k dispozici i po dokončení dílu
  [ -n "$PODCAST_ADMIN_PASSWORD" ] && wait
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
