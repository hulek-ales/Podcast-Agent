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
| hlas | **GPU služba** v proxy, nebo komerční TTS | lokálně jeden díl = jeden dotaz (proxy kvůli němu uvolní Ollamu); komerčně se GPU nečeká |
| feed | agent | RSS 2.0 + iTunes, `<enclosure>` na mp3, za tokenem |

## Rychlý start

```bash
git clone https://github.com/hulek-ales/Podcast-Agent.git && cd Podcast-Agent
pip install -r requirements.txt
PODCAST_ADMIN_PASSWORD=… uvicorn podcast.admin:app --port 8089
# zbytek (klíč k proxy, modely, zdroje, rozvrh) se naklikáš v administraci
```

Žádný `config.yaml` být nemusí: agent naběhne na výchozích hodnotách a všechno
ostatní se nastavuje v administraci (**Nastavení → Chování agenta**). Soubor je
jen pro toho, kdo si nastavení chce verzovat — hodnota z administrace má vždy
přednost. Z příkazové řádky pak:

```bash
python -m podcast.run --check          # odpovídá proxy? vidí klíč všechny modely?
python -m podcast.run --show prehled-dne
```

V Dockeru (vedle proxy, síť `ollamaNet`):

```bash
docker compose up -d --build
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

### Text napřed, hlas až potom

Namluvení stojí čas i peníze, tak se dá díl vyrobit na dvakrát:

1. **napsat text** — projde sběr, shrnutí a scénář, ale nic se nenamlouvá;
2. **náhled** — scénář si přečteš v prohlížeči rozdělený po tématech, s odkazy
   na zdroje u každého a s upozorněním na věty, kde zůstaly číslice (ty TTS
   přečte po svém);
3. **namluvit a zveřejnit** — pustí syntézu na hotový text a díl se objeví ve feedu.
   Nebo **zahodit a napsat znovu**, když se scénář nepovedl.

### Hlas: lokální služba, nebo OpenAI

Než budeš mít vlastní TTS kontejner, dá se namlouvat přes OpenAI — jde to tou
samou proxy a GPU k tomu není potřeba:

```yaml
models:
  tts: gpt-4o-mini-tts
  tts_provider: openai      # slug poskytovatele v proxy
episode:
  voice: alloy              # alloy, nova, echo, …
```

Klíč agenta musí mít ten model v `allowed_models`. Deset minut mluvení vyjde na
jednotky korun. Komerční API má strop na délku vstupu (OpenAI 4096 znaků), takže
agent text rozdělí a kusy slepí — `tts.max_chars` to řídí.

S vlastní službou nech `tts_provider` prázdné: pak jde celý díl **jedním
dotazem** na lokální GPU službu a dělení textu si řeší ona sama (jinak by proxy
mezi kusy pouštěla na kartu Ollamu).

Tlačítko **celý díl** udělá obojí naráz. Běhy se řadí za sebe, protože si přes
proxy sahají na jednu GPU.

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
python -m podcast.run --show prehled-dne --resume  # dodělat zbytek (třeba jen namluvit)
python -m podcast.run --show prehled-dne --steps speak --resume --date 2026-09-11
python -m podcast.run --due                        # co má zrovna čas (dělá plánovač sám)
```

Vedle každého dílu leží `<datum>.md` se scénářem **a odkazy na zdroje**. Když ti
v autě něco přijde divné, ověříš to za deset vteřin — u automaticky psaných zpráv
to není luxus, ale nutnost.

## Nastavení

Všechno se nastavuje v administraci, do souborů sahat nemusíš:

| kde | co |
|---|---|
| **Pořady** → upravit | zdroje a jejich váha, styl, délka, počet témat, rozvrh, hlas, zadání pro scénáristu |
| **Nastavení** → Chování agenta | modely, fronta úloh, hlas, veřejná adresa, autor feedu, timeouty |
| **Nastavení** → Klíče | klíč k proxy a její adresa |
| **Díly** | co se vyrobilo, průběh běhu, mazání |

Hodnota se hledá ve třech vrstvách: **administrace → `config.yaml` → výchozí
hodnoty v `podcast/settings.py`**. Prázdné pole v administraci znamená „neřeším
to“ a propadne o patro níž, takže nová verze může výchozí hodnoty vylepšit, aniž
by se držely ty staré. U každého pole je vidět, co zrovna platí a odkud to je.

Co se mění nejčastěji:

- **zdroje** a jejich váha (u pořadu). Deset ověřených je v novém pořadu předvyplněno.
- **počet témat** a **délka** dílu (u pořadu).
- **styl** — `anchor` (jeden moderátor), `brief` (3 minuty headlinů), `duo` (dva hlasy).
- **práh shlukování** — když se ti slévají různá témata, zvyš; když se stejná
  zpráva objeví dvakrát, sniž.
- **modely** — `script` míří na komerční API, zbytek je lokální.
- **veřejná adresa agenta** — z ní se skládají odkazy na zvuk ve feedu. Bez ní si
  telefon mimo domov díl nestáhne.

## Hlas přes OpenAI (nejrychlejší cesta ke zvuku)

Než bude lokální TTS, umí agent namluvit díl komerčním API — GPU nepotřebuje,
takže se s Ollamou nepere o kartu a fronta se neřeší.

1. **V proxy** doplň agentovu klíči do povolených modelů `gpt-4o-mini-tts`
   (GUI → API klíče → upravit).
2. **V administraci agenta** → Nastavení → Chování agenta:
   - *Hlas (model)* = `gpt-4o-mini-tts`
   - *Poskytovatel hlasu* = `openai`
   - *Pokyn k přednesu* — věta, kterou se modelu řekne, jak číst (a že česky).
     Výchozí je zpravodajský tón; klidně si ho uprav.
3. **U pořadu** nastav *Hlas* na některý z hlasů OpenAI (`alloy`, `nova`, `onyx`,
   `shimmer`, …). Prázdné = použije se `alloy`.

Komerční API má strop 4096 znaků na dotaz, takže agent text rozdělí po větách,
namluví po kusech a slepí je do jednoho souboru. Devítiminutový díl je zhruba
osm až deset kusů.

Pozor na pole `language`: lokální GPU služba ho chce, OpenAI ho nezná a na
neznámé pole vrátí HTTP 400. Agent proto posílá každé straně jen to, čemu rozumí
— u komerčního API se jazyk říká větou v *Pokynu k přednesu*.

### Doladění přízvuku

Hlasy OpenAI jsou trénované hlavně na angličtině, takže čeština z nich leze
s náběhem. Úplně to nezmizí, ale posunout se to dá dvěma pákami — a obě se
zkoušejí uchem, ne úvahou.

Nastavení → **Zkouška hlasu** namluví krátkou větu s českými jmény, datem
a hláskami, na kterých se cizí hlas prozradí (ř, č, ě, dlouhé samohlásky).
Trvá to pár vteřin, stojí to pár haléřů a hlas si pro zkoušku přepíšeš, aniž
bys ho ukládal — takže jde projet všechny hlasy za sebou a porovnat je.

1. **Hlas.** `nova`, `onyx`, `sage`, `coral`, `ash`, `marin`, `cedar` zní na
   češtině každý jinak. Vyber ten, co ti na ukázce sedí, a teprve pak ho ulož
   u pořadu.
2. **Pokyn k přednesu.** Ve výchozím textu je „mluvíš česky jako rodilý mluvčí“
   a připomínka, že **přízvuk patří na první slabiku** — tam se cizí hlas
   prozradí nejdřív. Klidně si ho uprav dál; je to obyčejná věta modelu.

Když ti ani jedno nestačí, zbývá lokální model s českými váhami
(viz [podcast-tts](https://github.com/hulek-ales/podcast-tts)) — tam je přízvuk
z principu český, jen za cenu stavění image a sdílení karty s Ollamou.

## Aplikace na mobilu

Administrace se dá přidat na plochu telefonu a chová se pak jako appka — vlastní
ikona, celá obrazovka, žádný adresní řádek. Není to nic k instalaci z obchodu,
jen webová stránka s manifestem a service workerem.

- **Android / Chrome:** otevři adresu agenta → menu ⋮ → *Přidat na plochu*
  (nebo *Nainstalovat aplikaci*, když ji prohlížeč nabídne sám).
- **iPhone / Safari:** ikona sdílení → *Přidat na plochu*.

Podmínkou je HTTPS — přes `http://` prohlížeč instalaci nenabídne.

Service worker schválně **nic necachuje**. Administrace ukazuje stav běhů a
fronty a odpověď ze cache by lhala o tom, co se zrovna děje; díly si stejně
stahuje čtečka podcastů, ne tahle stránka.

## Tematické díly

Pořad nemusí být jen přehled zpráv. V druhu pořadu přepni na **tematický** a místo
RSS zadej téma — „Vyhynutí dinosaurů", „Jak funguje kvantový počítač", „Historie
šifrování". Agent si k němu najde podklady a napíše souvislý díl.

Podklady se **nevymýšlejí z hlavy modelu**. Platí tu stejné pravidlo jako
u zpráv: napřed sežeň text, pak z něj piš. Díl o dinosaurech poskládaný z paměti
modelu zní stejně sebejistě, ať jsou fakta správně, nebo ne, a ověřit to nejde.
Zdroje si agent hledá sám, ve třech krocích:

0. **odborné studie.** K tématu se stáhnou abstrakty z **Europe PMC** a
   **Crossref** — oboje veřejné, bez klíče. Tohle je to, co odliší díl od
   převyprávěné encyklopedie: Wikipedie shrnuje, co se ustálilo, kdežto spory,
   čerstvé nálezy a hypotézy, které zrovna někdo zkoumá, jsou ve studiích.
   Abstrakty jsou anglicky, což nevadí — čte je model, který z nich píše česky —
   a veze se s nimi časopis a rok, ať jde v dílu říct „studie z Nature z roku
   dva tisíce dvacet čtyři“ místo anonymního „vědci zjistili“. Vypne se
   nastavením *Odborných studií* na nulu.
1. **rozmyslí si, na co se ptát.** Z tématu („Jak funguje kvantový počítač“)
   nechá model udělat pár konkrétních dotazů a názvů hesel. Doslovná otázka je
   pro vyhledávání mizerný vstup, pojmy z ní dobrý — na téhle jedné větě záleží
   víc než na čemkoli dalším v tomhle kroku.
2. **Wikipedie.** Na každý dotaz se vyhledá heslo a stáhne jeho holý text. Bez
   klíče, s odkazem, který se dá v dílu přiznat.
3. **web** — jen když je v nastavení adresa vyhledávače (viz níž). Bez ní se
   krok tiše přeskočí.

K tomu **vlastní odkazy** — cokoli přidáš u pořadu; text z nich dotáhne trafilatura.

### Prohledávání webu (nepovinné)

Prohledat web se nedá „jen tak“: buď se platí API, nebo se scrapují cizí
výsledky (křehké a proti podmínkám), nebo si člověk vyhledávač postaví. Agent
umí to třetí — mluví s **[SearXNG](https://github.com/searxng/searxng)**, což je
metavyhledávač na jeden kontejner. Sám se ptá desítek vyhledávačů, nic
nesleduje a nepotřebuje klíč.

```yaml
services:
  searxng:
    image: searxng/searxng:latest
    container_name: searxng
    restart: unless-stopped
    environment:
      SEARXNG_BASE_URL: http://searxng:8080/
    volumes:
      - /mnt/tank/apps/searxng:/etc/searxng
    networks: [ollamaNet]
networks:
  ollamaNet: {name: ollamaNet, external: true}
```

Po prvním startu se do svazku vygeneruje `settings.yml`. **Doplň v něm JSON**,
jinak agent dostane HTML a nic z toho nepřečte:

```yaml
search:
  formats:
    - html
    - json
```

Pak v administraci agenta Nastavení → *Adresa vyhledávače* = `http://searxng:8080`
a *Výsledků na dotaz* třeba 3. Port ven vystavovat nemusíš, volá ho jen agent.

Některé vyhledávače (typicky DuckDuckGo) na dotazy z domácí IP odpovídají CAPTCHOU
a v logu SearXNG z toho padá `SearxEngineCaptchaException`. Není to chyba
nastavení a nezabíjí to hledání — ostatní vyhledávače odpovídají dál a SearXNG
si ten problémový na chvíli sám odstaví. Když se to opakuje, vypni ho
v `settings.yml`:

```yaml
engines:
  - name: duckduckgo
    disabled: true
```

a nech zapnuté ty, co odpovídají (pro češtinu se osvědčí `seznam`, `mojeek`,
`brave`, `wikipedia`).

Ověření: v administraci Nastavení → **Zkouška vyhledávače** → *Otestovat*.
Buď vypíše nalezené odkazy, nebo řekne, co je špatně. Z příkazové řádky totéž:
`curl 'http://searxng:8080/search?q=test&format=json' | head -c 200` — když přijde
HTML, chybí `json` v `search.formats`; když 429, odmítl to limiter
(`server.limiter: false` pro instanci, na kterou se zvenku nikdo nedostane).

Dál běží stejná roura: lokální model udělá z každého podkladu hutný výtah (přes
frontu úloh), komerční model z výtahů napíše díl rozvržený do kapitol, a pak hlas
a feed. Scénárista má jiné zadání než u zpráv — vede posluchače příběhem, odborný
termín při prvním použití vysvětlí, a co je sporné, řekne jako sporné.

Tematický pořad **nejede podle rozvrhu**: napíšeš téma, dáš *napsat text*,
přečteš si ho a pustíš *namluvit*. Pak přepíšeš téma a uděláš další díl — všechny
zůstávají v jednom feedu, takže v telefonu je to jeden podcast, ke kterému
přibývají díly, kdykoli tě něco napadne.

### Forma: rozhovor dvou hlasů

Dobré populárně-naučné podcasty většinou nejsou přednáška, ale **rozhovor**.
Jeden se ptá za posluchače — diví se, nesouhlasí, shrnuje vlastními slovy —
a druhý odpovídá, protože si to nastudoval. Monolog o tomtéž zní jako výklad
u tabule, i když má stejný obsah.

Styl **duo** to umí doopravdy: scénárista značí repliky `[A]` a `[B]`, agent
podle nich text rozdělí a každou repliku namluví jiným hlasem. Nastavíš to
u pořadu — *Hlas* a *Druhý hlas* (třeba `nova` a `onyx`). Když druhý hlas
necháš prázdný, namluví se díl jedním, jak dosud.

Funguje to s komerčním TTS i s lokální GPU službou. U té se repliky pošlou
**jako jedna dávka úloh**, ne po jedné: pravidlo „jeden díl = jeden dotaz“ je
totiž o tom, aby se uprostřed dílu nepřehodila karta, a to dávka splní —
pracovník fronty v proxy je k modelu lepivý, takže úlohy pro hlas, který kartu
drží, bere za sebou bez přepínání. Navíc se mezi replikami může protáhnout
člověk v chatu, což jeden dlouhý dotaz neumožní.

Kusy se slepují podle formátu: MP3 stačí spojit po bajtech, u WAV se rozeberou
rámce a složí nová hlavička — jinak by se přehrála jen první replika, protože
délka je v hlavičce.

## Díly a průběh

Záložka **Díly** je deník výroby: jeden řádek na každý den každého pořadu —
hotový díl ve feedu, rozepsaný text, i den, který se rozbil v půlce a nic po sobě
nenechal. Detail dne ukáže **každý dotaz do proxy** (model, co se poslalo, jak
dlouho to trvalo, kolik tokenů to stálo) a hlavně poslední dotaz, na který ještě
nepřišla odpověď — to je přesně to místo, kde běh visí.

Odtud se taky spouští: *napsat text znovu*, *namluvit z hotového textu*, *vyrobit
celý díl znovu*, a maže: rozdělaná práce, nebo hotový díl i s přestavěním feedu.

Stopa se zapisuje po řádcích do `work/<pořad>/<datum>/trace.jsonl`, takže jde
číst i během běhu a pád procesu nezničí, co už se stihlo.

## Aktualizace tlačítkem

Nastavení → **Verze a aktualizace** ukazuje, co běží, a umí stáhnout novou verzi
bez chození do TrueNASu. *Zjistit novou verzi* udělá `git fetch` a vypíše, co
přibylo; *Aktualizovat a restartovat* stáhne kód a ukončí proces — kontejner se
podle `restart: unless-stopped` nastartuje sám a při startu doinstaluje i
změněné závislosti, což by pouhé znovunačtení kódu neumělo.

Podmínkou je, že kód běží z gitového klonu, tedy zapnutý self-update: v compose
vyplň `REPO_URL` (a u soukromého repa `GIT_TOKEN`) a nech připojený svazek na
`/app/src`. Když appka běží z hotového image, panel to řekne a poradí, jak to
zapnout — nová verze tam totiž může přijít jen novým image.

Aktualizace se odmítne, když se zrovna vyrábí díl: restart by rozdělanou práci
zahodil. Token z adresy repozitáře se na stránku nedostane, maskuje se.

## Která verze běží

Patička každé stránky administrace hlásí commit, čas buildu a čas startu:

```
verze a1b2c3d4 · postaveno 2026-09-13 08:49 · image · start 13.09. 09:16:51
```

Commit se do image zapéká při buildu v GitHub Actions. Po restartu appky tedy
stačí načíst administraci a porovnat: když se commit i čas startu změnily, natáhl
se nový image; když se změnil jen čas startu, TrueNAS pustil ten starý (zkontroluj
`pull_policy: always`, nebo v Apps dej *Pull image*).

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
