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

SEARCH_URL = "https://www.njuskalo.hr/ps5-konzole?sort=new"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
}

PROXY_HOST = os.environ.get("PROXY_HOST")
PROXY_PORT = os.environ.get("PROXY_PORT")
PROXY_USERNAME = os.environ.get("PROXY_USERNAME")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD")

PROXIES = None
if PROXY_HOST and PROXY_PORT and PROXY_USERNAME and PROXY_PASSWORD:
    proxy_url = f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"
    PROXIES = {"http": proxy_url, "https": proxy_url}

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
    with sync_playwright() as p:
        launch_kwargs = {"headless": True}
        if PLAYWRIGHT_PROXY:
            launch_kwargs["proxy"] = PLAYWRIGHT_PROXY

        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="hr-HR",
            viewport={"width": 1366, "height": 768},
        )
        page = context.new_page()
        page.goto(SEARCH_URL, timeout=45000, wait_until="domcontentloaded")

        try:
            page.wait_for_selector("li.EntityList-item", timeout=15000)
        except Exception:
            pass

        html = page.content()
        browser.close()

    log.info(
        "Debug: duzina_html=%d, sadrzi_EntityList=%s",
        len(html),
        "EntityList-item" in html,
    )
    if "EntityList-item" not in html:
        log.info("Debug HTML sadrzaj (prvih 1500 znakova): %s", html[:1500])
    soup = BeautifulSoup(html, "html.parser")

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
