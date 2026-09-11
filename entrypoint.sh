#!/bin/sh
# Bez RUN_AT vyrobí díl hned a skončí (hodí se na ladění i na cron zvenku).
# S RUN_AT=03:10 zůstane běžet a spustí se každý den v ten čas — žádný cron
# démon v kontejneru, jen spánek do dalšího termínu.
# S PODCAST_ADMIN_PASSWORD navíc běží administrace klíčů na ADMIN_PORT.
set -e

# Self-update z Gitu (nepovinné, stejně jako u OllamaProxy): s vyplněným
# REPO_URL si kontejner při startu stáhne kód do /app/src a udělá `git pull`,
# takže aktualizace = restart appky, ne přestavba image. Repo je soukromé, takže
# je potřeba token — v Gitea si vyrob další, jen pro čtení (repository: Přečtené).
if [ -n "${REPO_URL:-}" ]; then
  BRANCH="${REPO_BRANCH:-main}"
  URL="$REPO_URL"
  if [ -n "${GIT_TOKEN:-}" ]; then
    URL=$(echo "$REPO_URL" | sed "s#https://#https://${GIT_USER:-git}:${GIT_TOKEN}@#")
  fi
  mkdir -p /app/src
  if [ ! -d /app/src/.git ]; then
    echo "[git] klonuji $REPO_URL ($BRANCH)"
    git clone --branch "$BRANCH" "$URL" /tmp/repo && cp -a /tmp/repo/. /app/src/ && rm -rf /tmp/repo \
      || echo "[git] klon selhal, jedu s kódem z image"
  fi
  if [ -d /app/src/.git ]; then
    git config --global --add safe.directory /app/src
    cd /app/src
    git remote set-url origin "$URL" 2>/dev/null || true
    before=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
    git pull --ff-only origin "$BRANCH" >/dev/null 2>&1 || echo "[git] pull přeskočen (offline?)"
    after=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
    [ "$before" != "$after" ] && echo "[git] aktualizováno $before → $after"
    if [ -f requirements.txt ]; then
      sum=$(sha256sum requirements.txt | cut -c1-16)
      if [ "$(cat /app/.req-stamp 2>/dev/null)" != "$sum" ]; then
        echo "[pip] requirements.txt se změnil, doinstalovávám"
        pip install --no-cache-dir -q -r requirements.txt && echo "$sum" > /app/.req-stamp \
          || echo "[pip] instalace selhala, jedu s tím, co je v image"
      fi
    fi
  fi
fi

CONFIG="${PODCAST_CONFIG:-/data/config.yaml}"
if [ ! -f "$CONFIG" ]; then
  # vzor z běžícího kódu (po self-update je v /app/src), jinak ten z image
  EXAMPLE=./config.example.yaml
  [ -f "$EXAMPLE" ] || EXAMPLE=/app/config.example.yaml
  echo "[start] chybí $CONFIG, kopíruji vzor — uprav ho a restartuj"
  cp "$EXAMPLE" "$CONFIG"
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
    print('  HESLO DO ADMINISTRACE (ukazuje se, dokud si ho nezměníš):')
    print('')
    print('      ' + new)
    print('')
    print('  Přihlas se s ním a v administraci si ho změň — pak výpis zmizí.')
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
