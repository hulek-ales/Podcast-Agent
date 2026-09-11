# Aby TrueNAS sám bral nové verze z Gitu

Cíl: pushnu kód a na TrueNASu za chvíli běží nová verze. Bez SSH, bez ručního
`docker build`, u každého dalšího projektu stejně — a hlavně bez toho, abych
doma hostoval a hlídal build server.

**Doporučení: GitHub + GHCR.** Build běží na strojích
GitHubu, doma neběží nic navíc. Na TrueNASu se **jednou** přidají přihlašovací
údaje ke `ghcr.io` a platí pro všechny appky.

```
  git push  ──►  GitHub  ──►  Actions postaví image  ──►  ghcr.io (balíček)
                                                                              │
                                                        TrueNAS app  ◄────────┘  pull_policy: always
```

## 1. Jednorázově na TrueNASu (jen u soukromých repozitářů)

Dokud jsou repa veřejná, balíček v GHCR je veřejný a TrueNAS ho stáhne bez
přihlašování — tuhle kapitolu přeskoč. Až je zamkneš, vrať se sem.


Token: GitHub → Settings → Developer settings → Personal access tokens →
**Tokens (classic)** → oprávnění **`read:packages`** a **`repo`** (to druhé je
potřeba, protože balíček je navázaný na soukromé repo). Fine-grained tokeny
u GHCR spolehlivě nefungují, drž se klasického.

TrueNAS → Apps → Configuration → Manage Container Images → **Docker Registries**
→ Add (ten dialog, co jsi posílal; v URI vyber vlastní):

| pole | hodnota |
|---|---|
| URI | `https://ghcr.io` |
| Username | tvoje GitHub jméno |
| Password | ten classic PAT |

**Tohle je ta globální věc, cos chtěl** — přihlášení je na úrovni Dockeru na
NASu, ne appky. Každý další projekt pak jen odkáže na svůj image a stáhne se bez
dalšího nastavování.

Ověř si to hned, ať nehádáš později:

```bash
docker pull ghcr.io/<ty>/<balicek>:latest
```

## 2. Co dostane každý projekt

**`.github/workflows/docker.yml`** — nic se nenastavuje, `GITHUB_TOKEN` dostane
workflow automaticky:

```yaml
name: docker
on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ghcr.io/${{ github.repository }}:latest
```

**Compose pro TrueNAS** s `image: ghcr.io/<ty>/<repo>:latest` a
`pull_policy: always` — vzor je [`TrueNasAPP.yaml`](../TrueNasAPP.yaml).

Balíček zdědí soukromí repozitáře, takže zůstane neveřejný. Zkontroluj si to
u prvního projektu: GitHub → repo → Packages → Package settings.

## 3. Limity, na které se dá narazit

Free plán má pro **soukromé** balíčky 500 MB úložiště a **1 GB přenosu za
měsíc**, k tomu 2000 minut Actions. Pro pár domácích projektů to stačí, ale je
dobré vědět, co je sní:

- **Tagovat každý commit** (`:latest` i `:<sha>`) úložiště sežere nejrychleji.
  Proto je ve vzoru jen `:latest`. Když budeš chtít i verze, přidej si úklid
  starých (`actions/delete-package-versions`).
- **Přenos** ve skutečnosti bývá malý: Docker stahuje jen změněné vrstvy, a když
  se mění jen tvůj kód, jde o jednotky MB, ne o celý image. Proto v `Dockerfile`
  kopíruj `requirements.txt` a instaluj závislosti **před** kopírováním kódu —
  vrstva se závislostmi se pak nemění a nestahuje.
- **Malý základ.** `python:3.12-slim` místo plného `python:3.12` je rozdíl
  stovek MB.
- **Actions minuty**: build tohohle agenta je jednotky minut, takže ani při
  denním pushi na 2000 nedosáhneš.

Když by ti kvóta začala vadit a v image není nic citlivého, jde **balíček
zveřejnit, i když repozitář zůstane soukromý** — limity se pak neuplatní vůbec
a TrueNAS ho stáhne bez přihlášení. Cenou je, že si postavený image (a tím
i tvůj kód v něm) může stáhnout kdokoli. U tohohle projektu bych to nedělal.

## 4. Aby se to i samo nasadilo

Po pushi je nový image v registry, ale TrueNAS pořád běží na starém:

**a) Restart appky ručně** (Apps → appka → Restart). S `pull_policy: always` si
stáhne nový image. Průhledné, u projektu nasazovaného jednou za čas to stačí.

**b) Watchtower** — kontejner, který sleduje registry a restartuje sám. Omez ho
štítkem, ať nesahá na ostatní appky:

```yaml
services:
  watchtower:
    image: containrrr/watchtower
    restart: unless-stopped
    command: --interval 300 --label-enable --cleanup
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```

a v hlídané appce `labels: ["com.centurylinklabs.watchtower.enable=true"]`.
TrueNAS si appky spravuje sám, takže to nejdřív vyzkoušej na jedné — UI může po
cizím restartu chvíli hlásit nesoulad.

Doporučuju začít **(a)** a přejít na (b), až tě to začne otravovat.

## 5. Kde co je

Projekt žije na GitHubu:

- [`hulek-ales/Podcast-Agent`](https://github.com/hulek-ales/Podcast-Agent) — agent
- [`hulek-ales/podcast-tts`](https://github.com/hulek-ales/podcast-tts) — TTS služba

Zatím **veřejná**, ať se odlaďuje bez tokenů; balíčky v GHCR jsou pak taky veřejné
a limity Free plánu se neuplatní. Až se repa zamknou, platí všechno z kapitoly 3
a v TrueNASu je potřeba přidat přihlášení ke `ghcr.io` (kapitola 1).

Přenést repo jinam (třeba zpátky do Gitea) jde bez ztráty historie:

```bash
git remote add jinam https://…/repo.git
git push jinam main
git remote set-url origin https://…/repo.git   # ať je nový hlavní
```

## Alternativy, které jsem zvážil

**Gitea + vlastní runner.** Funguje a data neopustí dům, ale runner potřebuje
Docker, takže buď `privileged` kontejner s vlastním démonem, nebo připojený
hostitelský socket — což je fakticky root na NASu. K tomu ho musíš aktualizovat
a hlídat. Pro jeden dům je to práce navíc bez užitku, proto to nedoporučuju.

**Bez registry vůbec.** U pythonních projektů může kontejner kód klonovat při
startu a aktualizace je restart appky (tak to má OllamaProxy, agent to umí přes
`REPO_URL`). Nulové nastavení; cenou je, že na NASu běží kód, který nikdo
nepostavil ani neotestoval, a rozbitá závislost se pozná až z logu.

| | GitHub + GHCR | Gitea + runner | self-update |
|---|---|---|---|
| co hostuješ doma | nic | runner (privileged) | nic |
| kde je kód | u GitHubu (soukromě) | jen doma | dle repa |
| nasadí se | co prošlo CI | co prošlo CI | poslední commit |
| nastavení | token + registry v TrueNASu | hodina práce | dvě proměnné |
