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

# Web = administrace (heslo) + feed s díly (token). Heslo si agent drží sám;
# při prvním startu vyrobí náhodné a vypíše ho sem do logu.
python -c "
from podcast import auth
new = auth.bootstrap()
if new:
    print('')
    print('  ' + '=' * 66)
    print('  HESLO DO ADMINISTRACE (vypisuje se jen teď, pak už nikde):')
    print('')
    print('      ' + new)
    print('')
    print('  Přihlas se s ním a v administraci si ho změň.')
    print('  ' + '=' * 66)
    print('')
else:
    problem = auth.weak_password()
    if problem:
        print('[start] POZOR: heslo z PODCAST_ADMIN_PASSWORD — ' + problem)
"
echo "[start] web na portu ${ADMIN_PORT:-8089} (administrace + feed)"
# --proxy-headers: za reverzní proxou je skutečná adresa v X-Forwarded-For.
# --forwarded-allow-ips nastav na adresu té proxy, ne na '*', jinak si hlavičku
# může vymyslet kdokoli a obejít zamykání po špatných heslech.
uvicorn podcast.admin:app --host 0.0.0.0 --port "${ADMIN_PORT:-8089}" \
  --no-server-header \
  ${PODCAST_BEHIND_PROXY:+--proxy-headers --forwarded-allow-ips="${TRUSTED_PROXY_IPS:-127.0.0.1}"} &

run() {
  echo "[start] $(date '+%F %T') spouštím díl"
  python -m podcast.run ${RUN_ARGS:-} || echo "[start] díl selhal (návratový kód $?)"
}

if [ -z "$RUN_AT" ]; then
  run
  wait          # web musí zůstat naživu: feed je potřeba i po dokončení dílu
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
