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
| feed | agent | RSS 2.0 + iTunes, `<enclosure>` na mp3 |

## Rychlý start

```bash
git clone https://git.aleshulek.cz/Podcast_AI_Agent/Agent_app.git && cd Agent_app
cp config.example.yaml config.yaml     # uprav zdroje, modely a output.base_url
export PODCAST_PROXY_KEY=opx_…         # klíč z proxy (GUI → API klíče)
pip install -r requirements.txt

python -m podcast.run --check          # odpovídá proxy? vidí klíč všechny modely?
python -m podcast.run                  # vyrobí díl
```

V Dockeru (vedle proxy, síť `ollamaNet`):

```bash
PODCAST_ADMIN_PASSWORD=… docker compose up -d --build
# poprvé se do svazku zkopíruje vzor konfigurace a kontejner skončí — uprav ji a restartuj
docker compose restart podcast-agent
```

Feed pak přidáš v AntennaPodu jako `http://server:8088/feed.xml`.

## Klíče k proxy a administrace

Klíč je tajemství, takže nepatří do `config.yaml`, který se verzuje. Agent má
na správu klíčů vlastní stránku:

```bash
PODCAST_ADMIN_PASSWORD=… uvicorn podcast.admin:app --host 0.0.0.0 --port 8089
```

V Dockeru naběhne sama, jakmile je `PODCAST_ADMIN_PASSWORD` vyplněné
(`http://server:8089`, přihlášení `admin` + to heslo). Umí:

- **přidat** klíč `opx_…`, pojmenovat ho a případně mu dát vlastní adresu proxy;
- **otestovat** ho — vypíše, které modely s ním agent uvidí a který z potřebných
  mu chybí, takže nemusíš hádat, proč běh spadl na 403;
- **přepínat** mezi uloženými klíči (denní, testovací, starý před rotací) a mazat je;
- **nechat proxy vyrobit nový klíč** pro agenta: vložíš svůj *admin* klíč, ona z něj
  udělá klíč role `client` omezený přesně na modely z konfigurace. Admin klíč se
  nikam neuloží, použije se jednou a zapomene.

Klíče leží v `keys.json` vedle konfigurace s právy 600 a na stránce se ukazují jen
maskované (`opx_denni_…`).

**Odkud se bere klíč**, když je jich víc (první, který existuje, vyhrává):

1. aktivní klíč z administrace,
2. `PODCAST_PROXY_KEY` z prostředí,
3. `proxy.key` v `config.yaml`.

Když nastavíš klíč v administraci a v compose zůstane starý, platí ten z administrace
— stránka na to upozorní, ať to není záhada. `python -m podcast.run --check` vypíše,
odkud klíč přišel.

Ať děláš klíč ručně nebo přes tlačítko, omez ho jen na to, co agent používá:

```
allowed_models: nomic-embed-text, gemma4:12b, gpt-5-mini, tts-cs
max_jobs: 50        strop čekajících úloh
rate_per_min: 60
```

**Administrace patří jen do domácí sítě.** Zobrazuje a vyrábí klíče k proxy; bez
hesla v `PODCAST_ADMIN_PASSWORD` vůbec nenaběhne, ale port nevystavuj do internetu.

## Kroky zvlášť

Mezivýsledky se ukládají do `work/<datum>/`, takže se nemusí opakovat to, co už
proběhlo (a co stálo peníze):

```bash
python -m podcast.run --steps collect,cluster,summarize,script   # bez namluvení
python -m podcast.run --resume                                   # dodělat zbytek
python -m podcast.run --date 2026-09-10 --steps feed             # jen přegenerovat feed
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
