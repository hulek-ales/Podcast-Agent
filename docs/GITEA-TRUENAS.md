# Aby TrueNAS viděl do Gitea

Cíl: pushnu kód do Gitea a na TrueNASu za chvíli běží nová verze — bez SSH,
bez ručního `docker build`, u každého dalšího projektu stejně.

```
  git push  ──►  Gitea  ──►  runner postaví image  ──►  registry v Gitea
                                                              │
                                        TrueNAS app  ◄────────┘  pull_policy: always
```

Všechno kromě runneru už máš: Gitea 1.26 umí Actions i registry kontejnerů na
téže adrese (`https://git.aleshulek.cz/v2/` odpovídá). **Chybí jen runner** —
jeden kontejner, který workflow spouští. Postaví se jednou a slouží všem
projektům.

## 1. Runner (jednorázově)

**Registrační token**: Gitea → Správa webu (Site Administration) → Actions →
Runners → *Create new runner* → zkopíruj token.

Apps → Install via YAML → custom app:

```yaml
services:
  act-runner:
    image: gitea/act_runner:latest-dind-rootless
    container_name: gitea-runner
    restart: unless-stopped
    privileged: true        # kvůli vlastnímu Dockeru uvnitř (viz poznámka níž)
    environment:
      GITEA_INSTANCE_URL: https://git.aleshulek.cz
      GITEA_RUNNER_REGISTRATION_TOKEN: '<token ze správy webu>'
      GITEA_RUNNER_NAME: truenas
      GITEA_RUNNER_LABELS: ubuntu-latest:docker://gitea/runner-images:ubuntu-latest
    volumes:
      - type: volume
        source: runner-data
        target: /data

volumes:
  runner-data:
    name: gitea_runner_data
```

Hotovo poznáš tak, že se runner objeví v Gitea → Správa webu → Actions →
Runners jako *online*. Pokud ne, podívej se do logu appky — registrace se dělá
při prvním startu a token se dá použít jen jednou.

**K té `privileged`**: runner potřebuje Docker, aby mohl stavět image. Varianta
`dind-rootless` si pustí vlastní Docker uvnitř kontejneru; alternativa je
připojit hostitelský `/var/run/docker.sock`, což je ale fakticky root na NASu
pro cokoli, co v runneru běží. Když si v Gitea pouštíš jen vlastní kód, je
rozdíl akademický — jakmile bys tam měl cizí repozitáře, tohle si rozmysli.

## 2. Actions zapnout u repozitáře

U každého repa: Nastavení → Advanced → zaškrtnout **Actions**. (Globálně jdou
zapnout v `app.ini`, `[actions] ENABLED = true` — v 1.26 už bývá výchozí.)

## 3. Co dostane každý projekt

Dva soubory. U agenta už jsou, u dalšího projektu je zkopíruješ a přepíšeš jméno.

**`.gitea/workflows/docker.yaml`** — postaví image a pushne do registry:

```yaml
name: docker
on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "${{ secrets.REGISTRY_TOKEN }}" | docker login git.aleshulek.cz -u "${{ github.actor }}" --password-stdin
      - run: |
          IMAGE=git.aleshulek.cz/$(echo "${{ github.repository_owner }}" | tr '[:upper:]' '[:lower:]')/agent-app
          docker build -t "$IMAGE:latest" -t "$IMAGE:${{ github.sha }}" .
          docker push "$IMAGE:latest"
          docker push "$IMAGE:${{ github.sha }}"
```

`REGISTRY_TOKEN` přidáš v repu: Nastavení → Secrets → token uživatele
s oprávněním **`package: čtení i zápis`**. Jeden token na všechny projekty stačí.

**Compose pro TrueNAS** s `image:` mířícím do registry a `pull_policy: always`
(vzor: [`TrueNasAPP.yaml`](../TrueNasAPP.yaml)).

Do TrueNASu ještě jednorázově přidej registry: Apps → Configuration → Manage
Container Images → Docker Registries → Add, URI `https://git.aleshulek.cz`,
uživatel a ten samý token.

## 4. Aby se to i samo nasadilo

Po pushi máš nový image v registry, ale TrueNAS ještě běží na starém. Tři
možnosti, vzestupně podle pohodlí:

**a) Restart ručně.** Apps → appka → Restart. S `pull_policy: always` si stáhne
nový image. Poctivé a průhledné; u projektu, který nasazuješ jednou za týden,
úplně stačí.

**b) Watchtower.** Kontejner, který sleduje registry a sám restartuje, co se
změnilo. Omez ho štítkem, ať nesahá na appky, které aktualizovat nechceš:

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
Pozor: TrueNAS si svoje appky spravuje sám, takže po restartu přes Watchtower
může UI chvíli hlásit nesoulad. Ověř si to na jedné appce, než to zapneš plošně.

**c) Krok ve workflow, který si řekne TrueNASu o nasazení** přes jeho API. Je to
nejčistší (nasazuje se jen to, co prošlo CI), ale chce to API klíč TrueNASu
uložený v Gitea a odladit volání proti tvé verzi SCALE.

Doporučuju začít u **(a)** a přejít na (b) až u projektu, kde tě to začne
otravovat.

## Lehčí varianta bez runneru

Pro čistě pythonní projekty (jako tenhle agent) jde celý build vynechat: image
nese jen Python a závislosti, kód se při startu klonuje z Gitu a aktualizace je
restart appky. Tak to má OllamaProxy a agent to umí taky — stačí vyplnit
`REPO_URL`, `GIT_USER` a `GIT_TOKEN` (token jen pro čtení).

| | runner + registry | self-update z Gitu |
|---|---|---|
| první nastavení | hodina | nic |
| aktualizace | push → restart appky | restart appky |
| co se nasadí | přesně to, co prošlo CI | poslední commit ve větvi |
| funguje pro | cokoli, i kompilované | interpretované jazyky |
| kde se staví | v runneru | závislosti se doinstalují v kontejneru |

Nevýhoda self-update: na NASu běží kód, který nikdo nepostavil ani neotestoval,
a když se nepovede `pip install`, zjistíš to až z logu. Pro domácí projekt to
nevadí, pro něco, na čem ti záleží, chceš runner.
