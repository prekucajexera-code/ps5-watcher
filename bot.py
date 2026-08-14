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

SEARCH_URL = "https://www.njuskalo.hr/ps5-konzole?sort=new"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
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
    if not raw:
        return None
    raw = raw.strip()
    cleaned = re.sub(r"[^\d.,]", "", raw)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_ads():
    resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    log.info(
        "Debug: status=%d, duzina_html=%d, sadrzi_EntityList=%s",
        resp.status_code,
        len(resp.text),
        "EntityList-item" in resp.text,
    )
    if "EntityList-item" not in resp.text:
        log.info("Debug HTML sadrzaj (prvih 2000 znakova): %s", resp.text[:2000])
    soup = BeautifulSoup(resp.text, "html.parser")

    ads = []
    items = soup.select("li.EntityList-item")

    for item in items:
        article = item.select_one("article.entity-body")
        if not article:
            continue

        link_tag = article.select_one("h3.entity-title a.link")
        if not link_tag or not link_tag.get("href"):
            continue
        href = link_tag["href"]
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


def check_once(seen):
    try:
        ads = fetch_ads()
    except Exception as e:
        log.error("Greška pri dohvaćanju oglasa: %s", e)
        return seen

    new_count = 0
    for ad in ads:
        if ad["id"] in seen:
            continue
        seen.add(ad["id"])

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

    return seen


def main():
    log.info(
        "Pokrećem watcher | url=%s | prag=%.0f€ | interval=%ds",
        SEARCH_URL,
        PRICE_THRESHOLD,
        CHECK_INTERVAL_SEC,
    )
    seen = load_seen()
    while True:
        seen = check_once(seen)
        save_seen(seen)
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
