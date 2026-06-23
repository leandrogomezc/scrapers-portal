#!/usr/bin/env python3
"""Scrape Pinturas Biotech (Odoo eCommerce) shop and export to CSV."""

import csv
import re
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.pinturasbiotech.com"
SHOP_URL = f"{BASE_URL}/shop"
OUTPUT_PATH = Path(__file__).parent / "output" / "biotech_productos.csv"

CSV_COLUMNS = [
    "Código de SKU",
    "Nombre del Producto",
    "Descripcion del Producto",
    "URL de la imagen",
    "Precio de Venta",
]

REQUEST_DELAY = 0.35
DETAIL_WORKERS = 4
MAX_RETRIES = 3
TIMEOUT = 60

SKU_PATTERN = re.compile(r"^\[([^\]]+)\]\s*(.*)$", re.DOTALL)


def clean_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = BeautifulSoup(raw_html, "html.parser").get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def format_price(raw_price: str | None) -> str:
    if raw_price in (None, ""):
        return ""
    try:
        amount = float(str(raw_price).replace(",", "").strip())
    except ValueError:
        return ""
    return f"Q{amount:,.2f}"


def absolute_url(path_or_url: str) -> str:
    if not path_or_url:
        return ""
    return urljoin(BASE_URL, path_or_url)


def parse_sku_from_alt(alt_text: str) -> tuple[str, str]:
    alt = (alt_text or "").strip()
    match = SKU_PATTERN.match(alt)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", alt


def is_product_url(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    if not path.startswith("/shop/"):
        return False
    if path == "/shop" or "/category/" in path or "/cart" in path:
        return False
    return True


def fetch_html(session: requests.Session, url: str) -> str:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = attempt * 2
                time.sleep(wait)
    raise RuntimeError(f"Fallo al obtener {url}: {last_error}")


def parse_listing_card(card) -> dict | None:
    link = card.select_one("a.oe_product_image_link[href], h6.o_wsale_products_item_title a[href]")
    if not link:
        return None

    product_url = absolute_url(link.get("href", "").split("?")[0])
    if not is_product_url(product_url):
        return None

    img = card.select_one('img[itemprop="image"], img.img')
    alt_text = img.get("alt", "") if img else ""
    sku, name_from_alt = parse_sku_from_alt(alt_text)

    name_el = card.select_one('a[itemprop="name"]')
    name = (name_el.get_text(strip=True) if name_el else "") or name_from_alt

    price_el = card.select_one('[itemprop="price"]')
    raw_price = price_el.get_text(strip=True) if price_el else ""
    if not raw_price:
        currency_el = card.select_one(".oe_currency_value")
        raw_price = currency_el.get_text(strip=True) if currency_el else ""

    image_src = ""
    if img and img.get("src"):
        image_src = absolute_url(img["src"])

    subdesc = card.select_one(".oe_subdescription")
    short_desc = clean_text(subdesc.decode_contents()) if subdesc else ""

    return {
        "Código de SKU": sku,
        "Nombre del Producto": name,
        "Descripcion del Producto": short_desc,
        "URL de la imagen": image_src,
        "Precio de Venta": format_price(raw_price),
        "_product_url": product_url,
    }



def scrape_listing(session: requests.Session) -> dict[str, dict]:
    products: dict[str, dict] = {}
    page = 1

    while True:
        shop_url = SHOP_URL if page == 1 else f"{SHOP_URL}/page/{page}"
        html = fetch_html(session, shop_url)
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".oe_product")
        new_count = 0

        for card in cards:
            row = parse_listing_card(card)
            if not row:
                continue
            product_url = row.pop("_product_url")
            if product_url in products:
                continue
            products[product_url] = row
            new_count += 1

        if not cards or new_count == 0:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    return products


def parse_detail_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    desc_el = soup.select_one("#product_full_description")
    description = clean_text(desc_el.decode_contents()) if desc_el else ""

    img = soup.select_one(
        '#product_detail_main img[itemprop="image"], '
        '.o_wsale_product_images img, '
        'img[itemprop="image"]'
    )
    image_url = absolute_url(img["src"]) if img and img.get("src") else ""

    price_el = soup.select_one(".oe_price .oe_currency_value, [itemprop='price']")
    raw_price = price_el.get_text(strip=True) if price_el else ""

    sku = ""
    for candidate in soup.select("img[alt]"):
        alt = candidate.get("alt", "")
        parsed_sku, _ = parse_sku_from_alt(alt)
        if parsed_sku:
            sku = parsed_sku
            break

    return {
        "Descripcion del Producto": description,
        "URL de la imagen": image_url,
        "Precio de Venta": format_price(raw_price),
        "Código de SKU": sku,
    }


def fetch_product_details(
    product_url: str,
    headers: dict,
) -> tuple[str, dict]:
    session = requests.Session()
    session.headers.update(headers)
    html = fetch_html(session, product_url)
    return product_url, parse_detail_page(html)


def enrich_with_details(
    products: dict[str, dict],
    on_progress: Callable[[str], None] | None = None,
) -> None:
    headers = {
        "User-Agent": "BiotechScraper/1.0 (+personal use)",
        "Accept-Language": "es-GT,es;q=0.9",
    }
    urls = list(products.keys())
    completed = 0

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
        futures = {
            executor.submit(fetch_product_details, url, headers): url for url in urls
        }
        for future in as_completed(futures):
            product_url, details = future.result()
            row = products[product_url]

            if details["Descripcion del Producto"]:
                row["Descripcion del Producto"] = details["Descripcion del Producto"]
            if details["URL de la imagen"]:
                row["URL de la imagen"] = details["URL de la imagen"]
            if details["Precio de Venta"]:
                row["Precio de Venta"] = details["Precio de Venta"]
            if details["Código de SKU"] and not row["Código de SKU"]:
                row["Código de SKU"] = details["Código de SKU"]

            completed += 1
            if on_progress and completed % 25 == 0:
                on_progress(f"Detalle {completed}/{len(urls)} productos")


def scrape_all_products(
    on_progress: Callable[[str], None] | None = None,
) -> list[dict]:
    def report(message: str) -> None:
        print(message)
        if on_progress:
            on_progress(message)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "BiotechScraper/1.0 (+personal use)",
            "Accept-Language": "es-GT,es;q=0.9",
        }
    )

    report("Listando productos en /shop...")
    products = scrape_listing(session)
    report(f"Listado: {len(products)} productos — obteniendo descripciones...")
    enrich_with_details(products, on_progress=on_progress)
    report(f"Completado: {len(products)} productos")

    return list(products.values())


def export_csv(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def run_scrape(
    on_progress: Callable[[str], None] | None = None,
    output_path: Path | None = None,
) -> dict:
    target = output_path or OUTPUT_PATH
    rows = scrape_all_products(on_progress=on_progress)
    export_csv(rows, target)
    return {"rows": len(rows), "output_path": str(target)}


def main() -> int:
    print("Iniciando scrape de Pinturas Biotech...")
    try:
        result = run_scrape()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"\nCompletado: {result['rows']} productos exportados a {result['output_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
