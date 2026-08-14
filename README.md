# Njuškalo PS5 Watcher

Prati Njuškalo oglase za PlayStation 5 i šalje ti Telegram poruku kad
se pojavi novi oglas ispod zadane cijene.

## 1. Napravi Telegram bota

1. U Telegramu otvori **@BotFather**, pošalji `/newbot`, prati upute.
2. Dobit ćeš **TELEGRAM_BOT_TOKEN** (izgleda kao `123456789:AAExxxxxxx`).
3. Pošalji svom novom botu bilo koju poruku (npr. "hej") da ga "aktiviraš".
4. Otvori u browseru:
   `https://api.telegram.org/bot<TVOJ_TOKEN>/getUpdates`
   i pronađi `"chat":{"id": ...}` — to ti je **TELEGRAM_CHAT_ID**.

## 2. Testiraj lokalno (opcionalno)

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
export PRICE_THRESHOLD="400"
python bot.py
```

Ako scraper ne pronađe oglase, Njuškalo je vjerojatno promijenio HTML
strukturu — otvori `njuskalo.hr` pretragu u browseru, desni klik →
"View Page Source", i uspoređi CSS klase s onima u `fetch_ads()`
funkciji u `bot.py` (trenutno cilja `article.EntityList-item`).

## 3. Deploy na Railway

1. Napravi novi repo na GitHubu i pushaj ove fajlove.
2. Na [railway.app](https://railway.app) → New Project → Deploy from GitHub repo.
3. Odaberi repo. Railway će prepoznati `Procfile`.
4. Idi u **Variables** i dodaj:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `PRICE_THRESHOLD` (npr. `400`)
   - `CHECK_INTERVAL_SEC` (npr. `180`)
   - `SEARCH_QUERY` (default `playstation 5`)
5. Deploy. Provjeri **Logs** da vidiš je li krenulo bez grešaka.

## Napomene

- `seen_ads.json` pamti već viđene oglase da ne dobivaš duple poruke.
  Na Railwayu se filesystem briše kod redeploya — ako ti to smeta,
  javi pa prebacimo na Railway Volume ili vanjsku bazu (npr. SQLite
  na perzistentnom volumeu).
- Ako želiš dva praga (350€ i 400€) s različitim porukama, lako se
  doda — javi.
- Budi umjeren s `CHECK_INTERVAL_SEC` (ne ispod ~60s) da te Njuškalo
  ne blokira zbog previše zahtjeva.
