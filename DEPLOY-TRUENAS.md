# Nasazení na TrueNAS SCALE

TrueNAS Apps spustí hotový image, ale **neumí ho postavit z repozitáře** — nemá
kde vzít `Dockerfile` ani zbytek kódu. Image se proto někde postaví a TrueNAS si
ho jen stáhne.

## Dvě chyby, na které se narazí jako první

```
failed to solve: failed to read dockerfile: open Dockerfile: no such file or directory
```
V compose bylo `build: .`, ale TrueNAS nemá build kontext. Použij
`TrueNasAPP.yaml` (nebo `TrueNasAPP-local.yaml`), kde je `image:` místo `build:`.

```
pull access denied for podcast-agent, repository does not exist
```
Image nikde v registru není. Buď ho pushni do Gitea registry (níž), nebo ho
postav lokálně a použij `pull_policy: never`.

Když ve výpisu vidíš `podcast-web Pulled`, pouštíš **starý compose** s nginxem;
ten už v repu není — stáhni si aktuální `main`.

## Varianta A: GitHub Actions + GHCR (doporučeno)

Image staví GitHub na svých strojích, TrueNAS si ho stáhne z GHCR. Doma neběží
žádný build server a přihlášení k registry se v TrueNASu nastavuje **jednou pro
všechny appky**. Celý postup i s limity Free plánu: **[docs/DEPLOY-CI.md](docs/DEPLOY-CI.md)**.

Ve zkratce:

1. Repo na GitHubu (soukromé), workflow už je v repu:
   [`.github/workflows/docker.yml`](.github/workflows/docker.yml). Nic se v něm
   nenastavuje.
2. GitHub → classic PAT s `read:packages` + `repo`.
3. TrueNAS → Apps → Configuration → Manage Container Images → Docker Registries
   → Add: URI `https://ghcr.io`, uživatel a ten PAT.
4. Apps → Discover Apps → **Install via YAML** → vlož
   [`TrueNasAPP.yaml`](TrueNasAPP.yaml). UI nečte `.env`, hodnoty uprav v YAML.

Zůstáváš-li u Gitea, funguje totéž s jejím registry — v `TrueNasAPP.yaml` přepiš
řádek `image:` a v TrueNASu přidej `https://git.aleshulek.cz` s tokenem, který má
oprávnění `package`. Aby se tam image stavěl sám, potřebuješ vlastní runner; proč
to nedoporučuju, je v [docs/DEPLOY-CI.md](docs/DEPLOY-CI.md).

## Varianta B: postavit image na NASu

Bez tokenů na balíčky, ale aktualizace znamená přestavět image po SSH.

```bash
cd /mnt/tank/apps                     # do datasetu, ne do /root
git clone https://claude-bot:TOKEN@git.aleshulek.cz/Podcast_AI_Agent/Agent_app.git podcast-agent
cd podcast-agent
docker build -t podcast-agent:latest .
docker images | grep podcast-agent    # kontrola
```

Pak Install via YAML s [`TrueNasAPP-local.yaml`](TrueNasAPP-local.yaml).

## Po instalaci

Síť `ollamaNet` musí existovat (visí na ní Open WebUI a ollama-proxy). Kdyby ne:

```bash
docker network create ollamaNet
```

**Heslo do administrace** se vyrobí při prvním startu a vypíše se do logu —
a vypisuje se při každém startu, dokud si ho nezměníš:

```bash
docker logs podcast-agent 2>&1 | grep -A 3 HESLO
```

Otevři `http://truenas:8089`, přihlas se, **změň heslo** (výpis tím zmizí)
a zadej adresu proxy `http://ollama-proxy:11435` a klíč — nebo ho nech vyrobit
z admin klíče proxy.

**Konfigurace** (zdroje, modely, délka dílu) je v `config.yaml` ve svazku
`podcast_data`. Při prvním startu se tam zkopíruje vzor a kontejner skončí
s hláškou, ať ho upravíš:

```bash
docker cp podcast-agent:/data/config.yaml ./config.yaml   # upravit
docker cp ./config.yaml podcast-agent:/data/config.yaml
```

## Aktualizace

| varianta | postup |
|---|---|
| GHCR | `git push` → Actions postaví image → Apps → podcast-agent → Restart |
| lokální image | `git pull && docker build -t podcast-agent:latest .`, pak Restart |
| self-update | vyplň `REPO_URL`, `GIT_USER`, `GIT_TOKEN` (token jen pro čtení) — kontejner si při každém restartu udělá `git pull` a doinstaluje závislosti; přestavba je pak nutná jen při změně `Dockerfile` |

## Vystavení do internetu

Reverzní proxy na `podcast-agent:8089` a v appce doplň:

```yaml
PODCAST_BEHIND_PROXY: '1'
TRUSTED_PROXY_IPS: <adresa té proxy>
PODCAST_ADMIN_ALLOW: 192.168.1.0/24     # administrace jen z domova, feed zůstává venku
```

a v `config.yaml` dej `output.base_url` na veřejnou adresu. Podrobně v README,
sekce *Vystavení do internetu*.
