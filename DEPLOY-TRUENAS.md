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

## Varianta A: registry v Gitea (doporučeno)

Gitea umí registry kontejnerů na stejné adrese jako Git, takže si image můžeš
pushnout k sobě a TrueNAS ho odtamtud bere jako z Docker Hubu. Aktualizace je
pak `docker push` + restart appky, žádné SSH na NAS.

**1. Token na balíčky.** V Gitea (účet `claude-bot`) → Nastavení → Aplikace →
nový token s oprávněním **`package: čtení i zápis`**. Ten na repozitáře nestačí.

**2. Postavit a pushnout** (odkudkoli, kde je Docker — klidně z tvého počítače):

```bash
git clone https://claude-bot:TOKEN@git.aleshulek.cz/Podcast_AI_Agent/Agent_app.git
cd Agent_app
docker login git.aleshulek.cz -u claude-bot            # heslo = ten token
docker build -t git.aleshulek.cz/podcast_ai_agent/agent-app:latest .
docker push git.aleshulek.cz/podcast_ai_agent/agent-app:latest
```

Jméno vlastníka v cestě musí být **malými písmeny** (`podcast_ai_agent`), i když
se organizace jmenuje `Podcast_AI_Agent`. Balíček se pak objeví v Gitea →
organizace → Packages.

**3. Přidat registry do TrueNASu.** Apps → Configuration → Manage Container
Images → Docker Registries → Add. V dialogu, co jsi poslal, rozbal **URI** a vyber
vlastní (ne Docker Hub):

| pole | hodnota |
|---|---|
| URI | `https://git.aleshulek.cz` |
| Username | `claude-bot` |
| Password | ten token s oprávněním `package` |

**4. Nainstalovat appku.** Apps → Discover Apps → **Install via YAML** (custom
app) → vlož [`TrueNasAPP.yaml`](TrueNasAPP.yaml). UI nečte `.env`, hodnoty uprav
rovnou v YAML.

**Bonus:** máš-li v Gitea runner pro Actions, může se image stavět sám po každém
pushi do `main` — hotový workflow je v [`.gitea/workflows/docker.yaml`](.gitea/workflows/docker.yaml).
Bez runneru se nic nestane, soubor jen leží v repu.

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
| registry | `docker build … && docker push …`, pak Apps → podcast-agent → Restart |
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
