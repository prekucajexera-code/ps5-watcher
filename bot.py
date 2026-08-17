import os
import time
import logging
import traceback

from playwright.sync_api import sync_playwright
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# --- Konfiguracija iz env varijabli (Railway) ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PRICE_THRESHOLD = float(os.environ.get("PRICE_THRESHOLD", "400"))

PROXY_HOST = os.environ["PROXY_HOST"]
PROXY_PORT = os.environ["PROXY_PORT"]
PROXY_USERNAME = os.environ["PROXY_USERNAME"]
PROXY_PASSWORD = os.environ["PROXY_PASSWORD"]

URL = "https://www.njuskalo.hr/ps5-konzole?sort=new"
CHECK_INTERVAL_SECONDS = 10 * 60  # 10 minuta

SEEN_FILE = "seen_ads.txt"


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        f.write("\n".join(seen))


def send_telegram(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
    except Exception:
        log.error("Greška pri slanju Telegram poruke:\n%s", traceback.format_exc())


def parse_price(text):
    """'350,00 EUR' -> 350.0"""
    try:
        cleaned = text.replace("EUR", "").replace(".", "").replace(",", ".").strip()
        return float(cleaned)
    except Exception:
        return None


def check_listings(context, seen):
    """Otvori NOVU stranicu unutar postojećeg browser konteksta, provjeri oglase, zatvori stranicu."""
    page = context.new_page()
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)  # kratko čekanje da se JS sadržaj slegne

        links = page.query_selector_all("a[href*='/ps5-konzole/']")
        log.info("Provjereno %d linkova na stranici.", len(links))

        new_count = 0
        for link in links:
            href = link.get_attribute("href")
            if not href or href in seen:
                continue

            # Pokušaj naći cijenu u istom oglasnom "kartu" (roditeljski element)
            price_el = link.query_selector(
                "xpath=ancestor::*[contains(@class,'entity')][1]//*[contains(@class,'price')]"
            )
            price_text = price_el.inner_text() if price_el else ""
            price = parse_price(price_text)

            seen.add(href)

            if price is not None and price <= PRICE_THRESHOLD:
                new_count += 1
                msg = f"Novi PS5 oglas ispod {PRICE_THRESHOLD} EUR!\nCijena: {price} EUR\n{href}"
                log.info(msg.replace("\n", " | "))
                send_telegram(msg)

        if new_count == 0:
            log.info("Nema novih oglasa ispod praga.")

    finally:
        page.close()  # bitno: zatvori stranicu, ne cijeli browser


def main():
    seen = load_seen()
    log.info("Pokrenuto. Učitano %d već viđenih oglasa.", len(seen))

    proxy = {
        "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
        "username": PROXY_USERNAME,
        "password": PROXY_PASSWORD,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, proxy=proxy)
        context = browser.new_context()

        try:
            while True:
                try:
                    check_listings(context, seen)
                    save_seen(seen)
                except Exception:
                    log.error("Greška u ciklusu provjere:\n%s", traceback.format_exc())
                    # Ne rušimo cijeli proces zbog jedne greške — browser ostaje živ,
                    # samo preskačemo ovaj ciklus i pokušavamo opet za 10 min.

                log.info("Spavam %d sekundi do sljedeće provjere.", CHECK_INTERVAL_SECONDS)
                time.sleep(CHECK_INTERVAL_SECONDS)
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
