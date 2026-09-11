# Podcastový agent

Z RSS udělá mluvený přehled dne: posbírá články, slepí je po tématech, nechá je
shrnout lokálním modelem, napíše z nich scénář, nechá ho namluvit a vystaví jako
**podcast feed**, který si přidáš do AntennaPodu nebo Pocket Casts. Telefon si
epizodu stáhne přes noc sám.

Všechnu práci s modely dělá přes [OllamaProxy](https://github.com/hulek-ales/OllamaProxy)
— agent nemá GPU, neví o VRAM a neřeší fronty. Proxy rozhoduje, kdy co pustí na
kartu, a agent jen posílá dotazy a čeká.

```
RSS ──► sběr ──► shluky ──► shrnutí ──► scénář ──► hlas ──► feed.xml + mp3
                 (embed)    (fronta     (komerční  (GPU      (nginx → telefon)
                            úloh)       model)     služba)
```

| krok | kde běží | proč tam |
|---|---|---|
| sběr | agent | feedparser + trafilatura, žádné placené vyhledávání |
| shluky | **lokálně** přes proxy (`nomic-embed-text`) | levné, 300 MB VRAM; velikost shluku = důležitost |
| shrnutí | **lokálně**, přes frontu úloh (`/mgmt/v1/jobs`) | objemová práce, styl nerozhoduje; počká na volnou GPU |
| scénář | **komerčně** přes proxy (`/providers/openai/…`) | jediné místo, kde záleží na češtině; haléře na díl |
| hlas | **GPU služba** v proxy (`/v1/audio/speech`) | jeden díl = jeden dotaz, proxy kvůli němu uvolní Ollamu |
| feed | agent | RSS 2.0 + iTunes, `<enclosure>` na mp3, za tokenem |

## Rychlý start

```bash
git clone https://github.com/hulek-ales/Podcast-Agent.git && cd Podcast-Agent
cp config.example.yaml config.yaml     # uprav zdroje, modely a output.base_url
export PODCAST_PROXY_KEY=opx_…         # klíč z proxy (GUI → API klíče)
pip install -r requirements.txt

python -m podcast.run --check          # odpovídá proxy? vidí klíč všechny modely?
python -m podcast.run                  # vyrobí díl
```

V Dockeru (vedle proxy, síť `ollamaNet`):

```bash
docker compose up -d --build
# poprvé se do svazku zkopíruje vzor konfigurace a kontejner skončí — uprav ji a restartuj
docker compose restart podcast-agent
docker compose logs podcast-agent | grep -A 3 HESLO    # heslo do administrace
```

Na TrueNAS SCALE: **[DEPLOY-TRUENAS.md](DEPLOY-TRUENAS.md)**. Apps neumí stavět
image, takže ho staví GitHub Actions a TrueNAS si ho bere z GHCR — jak to zapojit
jednou pro všechny projekty: **[docs/DEPLOY-CI.md](docs/DEPLOY-CI.md)**.

Adresu feedu i s tokenem pak najdeš v administraci na `http://server:8089`.

## Web: administrace a feed

Agent má jednu webovou aplikaci na portu 8089 a **nic v ní není veřejné**:

| cesta | co to je | čím je chráněná |
|---|---|---|
| `/` | administrace klíčů | heslo + sezení v podepsané cookie |
| `/feed.xml`, `/media/…` | podcastový feed a mp3 | token v URL |

**Žádné tajemství není v compose ani v konfiguraci.** Heslo do administrace
i klíč k proxy se zadávají tady na stránce a leží v `/data` (`state.json`,
`keys.json`, práva 600).

Při prvním startu si agent vyrobí náhodné heslo a **vypíše ho jednou do logu**:

```bash
docker compose up -d
docker compose logs podcast-agent | grep -A 3 HESLO
```

Přihlas se s ním a hned si ho v administraci změň. Žádné výchozí heslo, které
by šlo uhodnout, neexistuje. (Kdo si chce heslo nastavit předem, může při prvním
startu vyplnit `PODCAST_ADMIN_PASSWORD`; jakmile si ho jednou změníš, proměnná
se ignoruje.)

Ručně mimo Docker:

```bash
python -c "from podcast import auth; print(auth.bootstrap() or 'heslo už existuje')"
uvicorn podcast.admin:app --host 0.0.0.0 --port 8089
```

Zapomenuté heslo se řeší smazáním `admin_password` ze `state.json` — při dalším
startu se vyrobí nové a vypíše do logu.

### Administrace klíčů

Klíč je tajemství, takže nepatří do `config.yaml`, který se verzuje. Stránka umí:

- **nastavit adresu proxy** a **změnit heslo** do administrace;
- **přidat** klíč `opx_…`, pojmenovat ho a případně mu dát vlastní adresu proxy;
- **otestovat** ho — vypíše, které modely s ním agent uvidí a který z potřebných
  mu chybí, takže nemusíš hádat, proč běh spadl na 403;
- **přepínat** mezi uloženými klíči (denní, testovací, starý před rotací) a mazat je;
- **nechat proxy vyrobit nový klíč** pro agenta: vložíš svůj *admin* klíč, ona z něj
  udělá klíč role `client` omezený přesně na modely z konfigurace. Admin klíč se
  nikam neuloží, použije se jednou a zapomene.

Klíče leží v `keys.json` vedle konfigurace s právy 600 a na stránce se ukazují jen
maskované (`opx_denni_…`).

**Odkud se bere klíč a adresa** (první nalezené vyhrává):

1. administrace — aktivní klíč a uložená adresa,
2. `PODCAST_PROXY_KEY` / `PODCAST_PROXY_URL` z prostředí,
3. `proxy.key` / `proxy.url` v `config.yaml`.

Administrace je schválně první: je to poslední vědomá volba člověka. Stránka
i `python -m podcast.run --check` ukážou, odkud hodnota přišla.

Ať děláš klíč ručně nebo přes tlačítko, omez ho jen na to, co agent používá:

```
allowed_models: nomic-embed-text, gemma4:12b, gpt-5-mini, tts-cs
max_jobs: 50        strop čekajících úloh
rate_per_min: 60
```

### Feed a token

Čtečky podcastů se neumí přihlašovat, takže díly chrání **token v URL**:

```
http://server:8089/feed.xml?token=…
```

Přesnou adresu i s tokenem najdeš v administraci, odkud se dá zkopírovat do
AntennaPodu. Token je i v odkazech na mp3 uvnitř feedu, jinak by je čtečka
nestáhla. Když ho přegeneruješ (tlačítko v administraci), starý okamžitě
přestane platit a feed musíš v telefonu přidat znovu.

Token se vyrobí sám při prvním spuštění a leží v `state.json` (práva 600);
vlastní si můžeš vynutit přes `PODCAST_FEED_TOKEN`.

**Token v URL má svoje meze:** objeví se v logu reverzní proxy i v historii
prohlížeče. Je to standardní řešení pro privátní podcasty (takhle fungují
i placené), ale počítej s tím, že kdo se k adrese dostane, má i díly.

## Vystavení do internetu

Feed má smysl mít dostupný i mimo domov, jinak si epizodu nestáhneš na cestě.
Aplikace na to je připravená, ale pár věcí musíš nastavit ty.

### Co udělá aplikace sama

- **Zamykání po špatných heslech.** Od pátého pokusu se adresa zamkne na 30 s
  a každý další pokus dobu zdvojnásobí (max hodina). Neúspěchy jdou do logu
  i s adresou.
- **Bezpečnostní hlavičky** na každé odpovědi: `Referrer-Policy: no-referrer`
  (jinak by prohlížeč vynesl token z URL v `Referer`), `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, přísné CSP a u feedu `Cache-Control: private`.
- **Cookie jen po HTTPS** (`Secure`), jakmile aplikace za HTTPS běží, a HSTS.
- **Varování u slabého hesla** při startu.

### Co musíš nastavit

```yaml
environment:
  PODCAST_BEHIND_PROXY: "1"            # věřit X-Forwarded-For/-Proto
  TRUSTED_PROXY_IPS: "172.16.0.5"      # adresa tvé reverzní proxy, NE "*"
  PODCAST_ADMIN_ALLOW: "192.168.1.0/24"  # administrace jen z domova (feed zůstává venku)
```

Tyhle tři zůstávají v prostředí schválně, i když všechno ostatní je ve webu:
musí platit dřív, než aplikace naběhne, a kdo je může přepnout, obejde zamykání
po špatných heslech i omezení sítí. Do stránky, kterou chrání právě ony, nepatří.

`PODCAST_ADMIN_ALLOW` je ta nejúčinnější věc: **feed ven, administrace ne.**
Klíče k proxy spravuješ z domova a z internetu je vidět jen `/feed.xml`
a `/media/…`, kde je co ztratit nesrovnatelně míň. Prázdná hodnota = odkudkoli.

`TRUSTED_PROXY_IPS` nedávej na `*`. S tím by si `X-Forwarded-For` vymyslel
kdokoli, obešel tím zamykání i omezení sítí.

### Reverzní proxy

Caddy (certifikáty řeší sám):

```
podcast.example.cz {
    reverse_proxy podcast-agent:8089
}
```

nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name podcast.example.cz;
    # ssl_certificate …;

    location / {
        proxy_pass http://podcast-agent:8089;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Chceš-li administraci nechat mimo internet i na úrovni proxy (pásek navíc
k `PODCAST_ADMIN_ALLOW`), pusť ven jen feed:

```nginx
    location ~ ^/(feed\.xml|media/) { proxy_pass http://podcast-agent:8089; }
    location / { return 404; }
```

A do `output.base_url` dej veřejnou adresu (`https://podcast.example.cz`),
jinak budou odkazy ve feedu ukazovat do LAN a telefon si venku nic nestáhne.

### Na co si dát pozor

- **Token se dostane do logů** reverzní proxy. Když ti to vadí, loguj bez
  query řetězce (`log_format` bez `$query_string`).
- **Po změně veřejné adresy** přegeneruj token a přidej feed v telefonu znovu —
  staré URL by ukazovala jinam.
- **Aktualizace.** Vystavená aplikace chce občas `docker compose build --pull`,
  ať má záplatovaný základ.

## Pořady

Jeden agent dělá **víc pořadů**. Každý má vlastní zdroje, styl, délku, rozvrh
i vlastní podcastový feed, takže si je v telefonu přidáš jako samostatné podcasty:

| | Přehled dne | Tech týdeník |
|---|---|---|
| zdroje | zpravodajské | Ars Technica, HN |
| styl | moderátor, 9 min | brief, 4 min |
| kdy | denně 03:10 | neděle 08:00 |
| zadání | — | „zaměř se na technologie, vynech politiku“ |
| feed | `…/prehled-dne/feed.xml?token=…` | `…/tech-tydenik/feed.xml?token=…` |

Zakládají se v administraci (**Pořady**) a leží v `shows.json` vedle konfigurace.
Tlačítko **vyrobit teď** udělá díl mimo rozvrh; běhy se řadí za sebe, protože
si přes proxy sahají na GPU.

**Zadání pro pořad** je volný text, který se přilepí do pokynů scénáristovi —
„mluv neformálně“, „na konci shrň tři věty, co si odnést“, „vynech sport“.
Tím se říká, co má pořad být, aniž by se sahalo do kódu.

Plánovač běží uvnitř aplikace; kontejner nepotřebuje cron ani `RUN_AT`. Pokud
už máš zdroje v `config.yaml`, udělá se z nich při prvním startu pořad
`prehled-dne`, takže se nic neztratí.

## Kroky zvlášť

Mezivýsledky se ukládají do `work/<datum>/`, takže se nemusí opakovat to, co už
proběhlo (a co stálo peníze):

```bash
python -m podcast.run --list                       # pořady a jejich rozvrh
python -m podcast.run --show prehled-dne           # vyrobit díl
python -m podcast.run --show prehled-dne --steps collect,cluster,summarize,script   # bez namluvení
python -m podcast.run --show prehled-dne --resume  # dodělat zbytek
python -m podcast.run --due                        # co má zrovna čas (dělá plánovač sám)
```

Vedle každého dílu leží `<datum>.md` se scénářem **a odkazy na zdroje**. Když ti
v autě něco přijde divné, ověříš to za deset vteřin — u automaticky psaných zpráv
to není luxus, ale nutnost.

## Konfigurace

Celá je v `config.yaml` (vzor v `config.example.yaml`), komentovaná. Co se mění
nejčastěji:

- `feeds` — zdroje a jejich váha. Deset ověřených tam je předvyplněno.
- `episode.stories` / `episode.minutes` — kolik témat a jak dlouhý díl.
- `episode.style` — `anchor` (jeden moderátor), `brief` (3 minuty headlinů),
  `duo` (dva hlasy).
- `episode.similarity` — práh shlukování. Když se ti slévají různá témata, zvyš;
  když se stejná zpráva objeví dvakrát, sniž.
- `models.*` — které modely na co. `script` a `script_provider` míří na
  komerční API, zbytek je lokální.
- `tts.mode` — `job` (přes frontu, doporučeno) nebo `direct` (drží spojení).

## Co je dobré vědět

**Čísla.** Rozepisuje je model do scénáře („deset procent“), protože česká
číslovka se skloňuje podle pádu. Symboly a zkratky dorovná `normalize_for_speech`.
Co zůstane jako číslice, agent po sobě vypíše — uvidíš, co TTS dostane syrové.

**Halucinace.** V promptech je tvrdé pravidlo „co není ve zdrojovém textu, do
výstupu nepatří“ a u každého tématu se nahlas říká zdroj. Scénář se ukládá vedle
zvuku i s odkazy, ať se dá ověřit.

**Jeden díl = jeden dotaz na TTS.** Posílat to po odstavcích by znamenalo, že
proxy mezi nimi pustí na kartu Ollamu a model se bude přehazovat. Dělení textu,
opakování vadných kusů i slepení patří dovnitř GPU služby.

**Právně.** RSS jsou publikované k odběru a shrnutí faktů chráněné není. Nepřepisuj
celé články doslova a feed nešiř dál — tohle je osobní přehled, ne vydavatelství.

## Vývoj

```bash
pip install -r requirements.txt pytest
python -m pytest -q          # testy nesahají na síť ani na proxy
```

`podcast/opx.py` je kopie `clients/opx_client.py` z repa OllamaProxy; při změně
API proxy sem zkopíruj novou verzi.
