#!/usr/bin/env python3
"""Monitor nowych ogłoszeń na OLX.pl z powiadomieniami na Discord.

Parsowanie OLX pochodzi z https://github.com/mar0ls/olx-monitor (MIT, (c) 2026 mar0ls).

Użycie:
    python olx_scraper.py             # jedno sprawdzenie + powiadomienia
    python olx_scraper.py --dry-run   # tylko wypisz nowe ogłoszenia
    python olx_scraper.py --reset     # zapomnij widziane ogłoszenia
"""

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

import http_client
import notify

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  KONFIGURACJA
# ─────────────────────────────────────────────────────────────
# Ustaw filtry na olx.pl (kategoria, miasto, promień, cena...) i wklej tu adres
# strony z wynikami. Sortowanie od najnowszych jest wymuszane automatycznie.
SEARCH_URLS = [
    "https://www.olx.pl/nieruchomosci/hale-magazyny/wynajem/bialystok/?search%5Bdist%5D=5&search%5Bfilter_float_price%3Ato%5D=2000&search%5Border%5D=created_at%3Adesc",
    "https://www.olx.pl/nieruchomosci/biura-lokale/wynajem/bialystok/?search%5Bdist%5D=5&search%5Border%5D=created_at:desc&search%5Bfilter_float_price:to%5D=2000",
]
# Wyniki są od najnowszych, więc 1–2 strony (~40 ogłoszeń każda) wystarczą.
MAX_PAGES = 2
# ─────────────────────────────────────────────────────────────

SEEN_FILE = Path(__file__).parent / "data" / "seen.json"
REQUEST_TIMEOUT = 15
DELAY_BETWEEN_PAGES = 2
# Gdy wyników jest mało, OLX po tym napisie dokleja ogłoszenia spoza wybranej lokalizacji.
EXTENDED_RESULTS_MARKER = re.compile(r"większej odległości", re.I)
# Parametry linków OLX, które nie są filtrami (np. z powiadomień o obserwowanym wyszukiwaniu).
IGNORED_PARAMS = {"min_id", "reason", "page", "search[order]"}
PHOTO_SIZE = "640x480"
EMBED_COLOR = 0x23E5DB


def normalize_search_url(url: str) -> str:
    parts = urlsplit(url.strip())
    params = [(k, v) for k, v in parse_qsl(parts.query) if k not in IGNORED_PARAMS]
    params.append(("search[order]", "created_at:desc"))
    return urlunsplit(parts._replace(query=urlencode(sorted(params)), fragment=""))


def fetch_page(url: str) -> BeautifulSoup:
    resp = http_client.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def extract_id_from_url(url: str) -> str:
    """'.../oferta/...-CID3-ID19HnE4.html' → 'ID19HnE4'"""
    m = re.search(r"-(ID[A-Za-z0-9]+)\.html", url)
    if m:
        return m.group(1)
    return url.strip("/").split("/")[-1][-12:]


def parse_price(text: str) -> int | None:
    """'3 000 zł', '3000 PLN', '4 500 złdo negocjacji' → int"""
    t = text.lower().replace("\xa0", " ")
    unit = re.search(r"(\d[\d\s]*)(?:z[łl]|pln|z?[łl]otych)", t)
    if unit:
        digits = re.sub(r"[^\d]", "", unit.group(1))
        return int(digits) if digits else None
    digits = re.sub(r"[^\d]", "", t)
    if digits:
        value = int(digits)
        return value if value <= 99_999 else None
    return None


def parse_metraz(text: str) -> float | None:
    """'65 m²' lub '65,5m2' → 65.0"""
    m = re.search(r"([\d]+[,\.]?[\d]*)\s*m", text)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            pass
    return None


def _extended_results_divider(soup: BeautifulSoup):
    # Ten sam napis jest też w JSON-ie z tłumaczeniami w <script>, więc szukamy tylko w <p>.
    return soup.find("p", string=EXTENDED_RESULTS_MARKER)


def _photos_by_id(soup: BeautifulSoup) -> dict[str, str]:
    # Przeglądarka doładowuje zdjęcia dalszych kart dopiero przy przewijaniu,
    # więc bierzemy je z JSON-a osadzonego w stronie.
    script = soup.find("script", string=re.compile(r"__PRERENDERED_STATE__"))
    m = script and re.search(r'__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*")', script.string)
    if not m:
        return {}
    try:
        ads = json.loads(json.loads(m.group(1)))["listing"]["listing"]["ads"]
    except (ValueError, KeyError, TypeError):
        return {}
    return {
        extract_id_from_url(ad["url"]): re.sub(r";s=\d+x\d+", f";s={PHOTO_SIZE}", ad["photos"][0])
        for ad in ads
        if ad.get("url") and ad.get("photos")
    }


def parse_listings(soup: BeautifulSoup) -> list[dict]:
    divider = _extended_results_divider(soup)
    if divider:
        cards = divider.find_all_previous(attrs={"data-cy": "l-card"})[::-1]
    else:
        cards = soup.find_all(attrs={"data-cy": "l-card"})
    photos = _photos_by_id(soup)

    listings = []
    for card in cards:
        try:
            link_tag = card.find("a", href=True)
            if not link_tag:
                continue
            url = link_tag["href"].split("?")[0]
            if not url.startswith("http"):
                url = "https://www.olx.pl" + url
            ad_id = extract_id_from_url(url)

            title_tag = card.find(["h4", "h6", "h3"])
            title = title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)
            if not title:
                continue

            price = None
            price_tag = card.find(attrs={"data-testid": "ad-price"})
            if price_tag:
                price = parse_price(price_tag.get_text(strip=True))
            else:
                for s in card.strings:
                    if "zł" in s and re.search(r"\d", s):
                        price = parse_price(s)
                        break

            loc_tag = card.find(attrs={"data-testid": "location-date"})
            location_text = loc_tag.get_text(strip=True) if loc_tag else ""
            lokalizacja = location_text.split(" - ")[0].strip()
            data_dodania = location_text.split(" - ")[1].strip() if " - " in location_text else ""

            metraz = None
            for s in card.strings:
                s = s.strip()
                if "m²" in s or "m2" in s:
                    metraz = parse_metraz(s)
                    if metraz:
                        break
            if not metraz:
                metraz = parse_metraz(title)

            img = card.find("img")
            card_photo = img.get("src", "") if img else ""

            listings.append({
                "id": ad_id,
                "title": title,
                "price": price,
                "negotiable": any("do negocjacji" in s for s in card.strings),
                "metraz": metraz,
                "lokalizacja": lokalizacja,
                "data": data_dodania,
                "url": url,
                "photo": photos.get(ad_id) or (card_photo if card_photo.startswith("http") else None),
            })
        except Exception as e:
            logger.debug("Pominięto kartę ogłoszenia: %s", e, exc_info=True)
    return listings


def has_next_page(soup: BeautifulSoup, page: int) -> bool:
    # Przy kilku stronach wyników OLX pokazuje same numery stron, bez strzałki "dalej".
    return bool(
        soup.find(attrs={"data-testid": "pagination-forward"})
        or soup.find(attrs={"data-cy": "pagination-forward"})
        or soup.find("a", href=re.compile(rf"[?&]page={page + 1}(?:&|$)"))
    )


def find_new_listings(search_url: str, seen: set[str]) -> list[dict]:
    """Zwraca ogłoszenia spoza `seen`, od najnowszego."""
    new: dict[str, dict] = {}
    for page in range(1, MAX_PAGES + 1):
        url = search_url if page == 1 else f"{search_url}&page={page}"
        logger.info("Strona %d: %s", page, url)
        soup = fetch_page(url)
        listings = parse_listings(soup)
        logger.info("Ogłoszeń na stronie: %d", len(listings))
        if not listings:
            break
        for listing in listings:
            if listing["id"] not in seen:
                new.setdefault(listing["id"], listing)
        if not has_next_page(soup, page) or _extended_results_divider(soup):
            break
        time.sleep(DELAY_BETWEEN_PAGES)
    return list(new.values())


def load_state(path: Path) -> dict[str, set[str]]:
    """{znormalizowany link wyszukiwania: zbiór widzianych ID}"""
    if path.exists():
        return {url: set(ids) for url, ids in json.loads(path.read_text(encoding="utf-8")).items()}
    return {}


def save_state(path: Path, state: dict[str, set[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {url: sorted(ids) for url, ids in state.items()}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _format_price(listing: dict) -> str:
    if not listing["price"]:
        return "cena ukryta"
    return f"{listing['price']:,} zł".replace(",", " ")


def build_embed(listing: dict) -> dict:
    cena = _format_price(listing)
    if listing["negotiable"]:
        cena += "\ndo negocjacji"
    fields = [{"name": "Cena", "value": cena, "inline": True}]
    if listing["metraz"]:
        fields.append({"name": "Metraż", "value": f"{listing['metraz']:g} m²", "inline": True})
    if listing["lokalizacja"]:
        fields.append({"name": "Lokalizacja", "value": listing["lokalizacja"], "inline": False})

    embed = {
        "title": listing["title"][:256],
        "url": listing["url"],
        "color": EMBED_COLOR,
        "fields": fields,
    }
    if listing["data"]:
        embed["footer"] = {"text": listing["data"]}
    if listing["photo"]:
        embed["thumbnail"] = {"url": listing["photo"]}
    return embed


def summary(listing: dict) -> str:
    parts = [listing["title"], _format_price(listing)]
    if listing["metraz"]:
        parts.append(f"{listing['metraz']:g} m²")
    parts += [listing["lokalizacja"], listing["url"]]
    return " | ".join(p for p in parts if p)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Monitor nowych ogłoszeń na OLX.pl")
    parser.add_argument("--dry-run", action="store_true",
                        help="Tylko wypisz nowe ogłoszenia – bez Discorda i bez zapisu stanu")
    parser.add_argument("--reset", action="store_true",
                        help="Zapomnij widziane ogłoszenia (następne uruchomienie zacznie od zera)")
    args = parser.parse_args()

    if args.reset:
        SEEN_FILE.unlink(missing_ok=True)
        logger.info("Pamięć widzianych ogłoszeń wyczyszczona.")

    webhook = notify.discord_webhook_url()
    if not args.dry_run and not webhook:
        logger.error("Ustaw zmienną DISCORD_WEBHOOK_URL albo uruchom z --dry-run.")
        sys.exit(1)

    state = load_state(SEEN_FILE)
    # Budujemy stan od nowa, więc linki usunięte z SEARCH_URLS znikają też z pliku.
    new_state: dict[str, set[str]] = {}
    failed = 0

    for search_url in map(normalize_search_url, SEARCH_URLS):
        seen = state.get(search_url)
        first_run = seen is None
        seen = set() if first_run else seen
        logger.info("Wyszukiwanie: %s (zapamiętanych: %d)", search_url, len(seen))

        try:
            new = find_new_listings(search_url, seen)
        except requests.RequestException as e:
            logger.error("Nie udało się pobrać OLX: %s", e)
            failed += 1
            if not first_run:
                new_state[search_url] = seen
            continue

        if args.dry_run:
            for listing in new:
                logger.info("[NOWE] %s", summary(listing))
            logger.info("--dry-run: %d nowych ogłoszeń.", len(new))
            continue

        # Nowe wyszukiwanie tylko zapamiętuje obecne ogłoszenia, żeby nie zasypać kanału
        # kilkudziesięcioma starymi ofertami; wiadomość startowa potwierdza, że webhook działa.
        if first_run:
            msg = (f"Zaczynam obserwować wyszukiwanie – zapamiętano {len(new)} aktualnych "
                   f"ogłoszeń, od teraz dostaniesz tylko nowe.\n{search_url}")
            if notify.send_discord(webhook, content=msg):
                new_state[search_url] = {listing["id"] for listing in new}
            else:
                failed += 1
            continue

        for listing in reversed(new):
            logger.info("[NOWE] %s", summary(listing))
            if notify.send_discord(webhook, embed=build_embed(listing)):
                seen.add(listing["id"])
            else:
                failed += 1
        new_state[search_url] = seen

    if not args.dry_run:
        save_state(SEEN_FILE, new_state)
    if failed:
        logger.error("Nieudanych operacji: %d.", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
