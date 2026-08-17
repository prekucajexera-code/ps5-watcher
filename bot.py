"""
Njuškalo PS5 Watcher Bot
-------------------------
Prati Njuškalo kategoriju "PlayStation 5 konzole" (pokriva Slim/Digital/
Disc/Pro) i šalje Telegram notifikaciju kad se pojavi nov oglas ispod
zadane cijene.

ENV varijable (postavi ih u Railway -> Variables):
  TELEGRAM_BOT_TOKEN   - token bota (od @BotFather)
  TELEGRAM_CHAT_ID     - tvoj chat ID
  PRICE_THRESHOLD      - npr. 400 (EUR) - default 400
  CHECK_INTERVAL_SEC   - npr. 180 - default 180
"""

import os
import re
import time
import json
import logging
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ps5-watcher")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PRICE_THRESHOLD = float(os.environ.get("PRICE_THRESHOLD", "400"))
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "180"))

SEEN_FILE = "seen_ads.json"

# Kategorija "PlayStation 5 konzole" - pokriva sve varijante (Slim/Digital/
# Disc/Pro), sortirano po najnovijem prvo.
SEARCH_URL = "https://www.njuskalo.hr/ps5-konzole?sort=new"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Rezidencijalni proxy (DataImpulse) - postavi ove ENV varijable u Railway
# ako Njuškalo blokira direktne zahtjeve (ShieldSquare captcha).
# Format: host:port + username/password autentikacija.
PROXY_HOST = os.environ.get("PROXY_HOST")  # npr. gw.dataimpulse.com
PROXY_PORT = os.environ.get("PROXY_PORT")  # npr. 823
PROXY_USERNAME = os.environ.get("PROXY_USERNAME")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD")

PROXIES = None
if PROXY_HOST and PROXY_PORT and PROXY_USERNAME and PROXY_PASSWORD:
    proxy_url = f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"
    PROXIES = {"http": proxy_url, "https": proxy_url}

# Playwright koristi drukciji format za proxy (server/username/password odvojeno)
PLAYWRIGHT_PROXY = None
if PROXY_HOST and PROXY_PORT and PROXY_USERNAME and PROXY_PASSWORD:
    PLAYWRIGHT_PROXY = {
        "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
        "username": PROXY_USERNAME,
        "password": PROXY_PASSWORD,
    }


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f)


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=15,
    )
    if not resp.ok:
        log.error("Telegram slanje nije uspjelo: %s", resp.text)


def parse_price(raw: str):
    """'1.234,00 EUR' / '399 €' -> 399.0 ili None ako se ne da parsirati (npr. 'po dogovoru')"""
    if not raw:
        return None
    raw = raw.strip()
    # makni sve osim znamenki, zareza i tocke
    cleaned = re.sub(r"[^\d.,]", "", raw)
    if not cleaned:
        return None
    # hrvatski format: 1.234,56 -> 1234.56
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_ads(browser):
    context = browser.new_context(
        user_agent=HEADERS["User-Agent"],
        locale="hr-HR",
        viewport={"width": 1366, "height": 768},
    )
    try:
        page = context.new_page()
        page.goto(SEARCH_URL, timeout=45000, wait_until="domcontentloaded")

        # ShieldSquare cesto radi kratku provjeru u pozadini (par sekundi) prije
        # nego pusti pravu stranicu - pricekaj da se pojavi stvarni sadrzaj
        # ili da prođe razuman timeout.
        try:
            page.wait_for_selector("li.EntityList-item", timeout=15000)
        except Exception:
            pass  # ako se ne pojavi, nastavljamo i logiramo sto je stiglo

        html = page.content()
    finally:
        context.close()  # oslobodi memoriju/procese ove sesije, ali browser ostaje ziv

    log.info(
        "Debug: duzina_html=%d, sadrzi_EntityList=%s",
        len(html),
        "EntityList-item" in html,
    )
    if "EntityList-item" not in html:
        log.info("Debug HTML sadrzaj (prvih 1500 znakova): %s", html[:1500])
    soup = BeautifulSoup(html, "html.parser")

    ads = []
    # Umjesto oslanjanja na section/klase (koje se ponavljaju za razne widgete
    # na stranici i lako uhvate krivi sadrzaj), filtriramo direktno po URL-u:
    # jedino pouzdano obiljezje pravog PS5 oglasa je da URL pocinje s /ps5-konzole/.
    items = soup.select("li.EntityList-item")

    for item in items:
        article = item.select_one("article.entity-body")
        if not article:
            continue  # preskoci bannere/CTA stavke koje nisu pravi oglasi

        link_tag = article.select_one("h3.entity-title a.link")
        if not link_tag or not link_tag.get("href"):
            continue
        href = link_tag["href"]

        # KLJUCNI FILTER: samo oglasi iz PS5 kategorije, ignoriraj sve drugo
        # (nakit, auto dijelovi, nekretnine... iz "Posljednji oglasi" widgeta)
        if not href.startswith("/ps5-konzole/"):
            continue

        # ad id je broj nakon zadnjeg "-" u URL-u, npr. ...-oglas-51211819 -> 51211819
        ad_id = href.rstrip("/").split("-")[-1]

        title = link_tag.get_text(strip=True)

        price_tag = article.select_one(".entity-prices strong.price")
        price_raw = price_tag.get_text(strip=True) if price_tag else None
        price = parse_price(price_raw)

        ads.append(
            {
                "id": ad_id,
                "title": title,
                "url": href if href.startswith("http") else f"https://www.njuskalo.hr{href}",
                "price": price,
                "price_raw": price_raw,
            }
        )
    return ads


def check_once(seen, browser):
    try:
        ads = fetch_ads(browser)
    except Exception as e:
        log.error("Greška pri dohvaćanju oglasa: %s", e)
        return seen, False

    new_count = 0
    for ad in ads:
        if ad["id"] in seen:
            continue
        seen.add(ad["id"])

        # cijena "po dogovoru" ili neparsirana - preskoci filter, ali javi da provjeris rucno
        if ad["price"] is not None and ad["price"] > PRICE_THRESHOLD:
            continue

        new_count += 1
        price_display = ad["price_raw"] or "cijena nepoznata"
        text = (
            f"🎮 <b>Novi PS5 oglas ispod {int(PRICE_THRESHOLD)}€!</b>\n\n"
            f"{ad['title']}\n"
            f"💶 {price_display}\n"
            f"{ad['url']}"
        )
        log.info("Novi oglas: %s (%s)", ad["title"], price_display)
        send_telegram(text)

    if new_count == 0:
        log.info("Provjereno %d oglasa, nema novih ispod praga.", len(ads))

    return seen, True


def ensure_browser_installed():
    """Instalira Chromium ako jos nije prisutan (prvi put pri pokretanju)."""
    import subprocess
    log.info("Provjeravam/instaliram Playwright Chromium...")
    result = subprocess.run(
        ["playwright", "install", "--with-deps", "chromium"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("Instalacija Chromiuma nije uspjela: %s", result.stderr[-1000:])
    else:
        log.info("Chromium spreman.")


def launch_browser(p):
    launch_kwargs = {"headless": True}
    if PLAYWRIGHT_PROXY:
        launch_kwargs["proxy"] = PLAYWRIGHT_PROXY
    return p.chromium.launch(**launch_kwargs)


def main():
    ensure_browser_installed()
    log.info(
        "Pokrećem watcher | url=%s | prag=%.0f€ | interval=%ds | proxy=%s",
        SEARCH_URL,
        PRICE_THRESHOLD,
        CHECK_INTERVAL_SEC,
        "DA" if PROXIES else "NE",
    )
    seen = load_seen()

    # Browser se pokrece JEDNOM i ponovno koristi za sve cikluse - pokretanje
    # novog browsera svaki ciklus iscrpi resurse (thread/proces limit) na
    # manjim serverima i s vremenom uzrokuje "Target crashed" greske.
    with sync_playwright() as p:
        browser = launch_browser(p)
        consecutive_failures = 0

        while True:
            seen, ok = check_once(seen, browser)
            save_seen(seen)

            if ok:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                # Ako par puta zaredom pukne, browser je vjerojatno u lošem
                # stanju (crash/closed) - ugasi ga i pokreni novi.
                if consecutive_failures >= 2:
                    log.error("Ponovno pokrecem browser nakon uzastopnih gresaka...")
                    try:
                        browser.close()
                    except Exception:
                        pass
                    try:
                        browser = launch_browser(p)
                        consecutive_failures = 0
                    except Exception as e:
                        log.error("Ponovno pokretanje browsera nije uspjelo: %s", e)

            time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
