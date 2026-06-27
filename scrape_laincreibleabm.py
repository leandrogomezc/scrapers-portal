#!/usr/bin/env python3
"""Scrape La Increíble ABM (PrestaShop 1.6) catalog and export to CSV."""

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

BASE_URL = "https://old.laincreibleabm.com.gt"
SITEMAP_URL = f"{BASE_URL}/index.php?controller=sitemap"
OUTPUT_PATH = Path(__file__).parent / "output" / "laincreibleabm_productos.csv"

CSV_COLUMNS = [
    "Código de SKU",
    "Nombre del Producto",
    "Descripcion del Producto",
    "Precio de Venta",
    "Disponibilidad",
    "Categoría",
    "Fabricante",
    "Estado",
    "Ficha Técnica",
    "URL de la imagen",
    "URL del Producto",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-GT,es;q=0.9",
}

REQUEST_DELAY = 0.35
DETAIL_WORKERS = 6
MAX_RETRIES = 3
TIMEOUT = 60
MAX_PAGES_PER_CATEGORY = 200

PRODUCT_URL_RE = re.compile(r"/\d+-[^/]+\.html(?:$|\?)")
CATEGORY_URL_RE = re.compile(r"^/(\d+)-[^/]+/?$")
PRODUCT_ID_RE = re.compile(r"/(\d+)-[^/]+\.html")


def clean_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = BeautifulSoup(raw_html, "html.parser").get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def format_price(raw_price: str | None) -> str:
    if raw_price in (None, ""):
        return ""
    cleaned = re.sub(r"[^0-9.]", "", str(raw_price))
    if cleaned in ("", "."):
        return ""
    try:
        amount = float(cleaned)
    except ValueError:
        return ""
    return f"Q{amount:,.2f}"


def absolute_url(path_or_url: str) -> str:
    if not path_or_url:
        return ""
    return urljoin(BASE_URL, path_or_url)


def product_id_from_url(url: str) -> str | None:
    match = PRODUCT_ID_RE.search(urlparse(url).path)
    return match.group(1) if match else None


def prettify_slug(slug: str) -> str:
    slug = re.sub(r"^\d+-", "", slug)
    return slug.replace("-", " ").strip().title()


def fetch_html(session: requests.Session, url: str) -> str:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(attempt * 2)
    raise RuntimeError(f"Fallo al obtener {url}: {last_error}")


def discover_categories(session: requests.Session) -> list[tuple[str, str]]:
    """Return list of (category_url, category_name) from the HTML sitemap."""
    html = fetch_html(session, SITEMAP_URL)
    soup = BeautifulSoup(html, "html.parser")
    categories: dict[str, str] = {}

    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").split("#")[0].strip()
        if not href:
            continue
        path = urlparse(href).path
        match = CATEGORY_URL_RE.match(path)
        if not match:
            continue
        url = f"{BASE_URL}{path.rstrip('/')}"
        name = anchor.get_text(strip=True) or prettify_slug(path.strip("/"))
        categories.setdefault(url, name)

    return list(categories.items())


def collect_product_urls(
    session: requests.Session,
    categories: list[tuple[str, str]],
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, dict]:
    """Crawl every category with pagination. Returns {product_url: {url, category}}."""
    products: dict[str, dict] = {}
    seen_ids: set[str] = set()

    for index, (category_url, category_name) in enumerate(categories, start=1):
        page = 1
        while page <= MAX_PAGES_PER_CATEGORY:
            page_url = category_url if page == 1 else f"{category_url}?p={page}"
            try:
                html = fetch_html(session, page_url)
            except RuntimeError:
                break

            soup = BeautifulSoup(html, "html.parser")
            new_on_page = 0
            for anchor in soup.select("ul.product_list a[href]"):
                href = anchor.get("href", "")
                if not PRODUCT_URL_RE.search(href):
                    continue
                product_url = absolute_url(href.split("?")[0])
                product_id = product_id_from_url(product_url)
                if not product_id or product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                products[product_url] = {
                    "url": product_url,
                    "category": category_name,
                }
                new_on_page += 1

            if new_on_page == 0:
                break
            page += 1
            time.sleep(REQUEST_DELAY)

        if on_progress:
            on_progress(
                f"Categoría {index}/{len(categories)} — {len(products)} productos únicos"
            )

    return products


def _select_long_description(soup: BeautifulSoup) -> str:
    candidates = soup.select(".rte")
    long_desc = ""
    for el in candidates:
        if el.get("id") == "short_description_content":
            continue
        text = clean_text(el.decode_contents())
        if len(text) > len(long_desc):
            long_desc = text
    if long_desc:
        return long_desc
    short = soup.select_one("#short_description_content")
    return clean_text(short.decode_contents()) if short else ""


def _select_manufacturer(soup: BeautifulSoup) -> str:
    logo = soup.select_one("#manufacturer_logo[alt], .product_manufacturer img[alt]")
    if logo and logo.get("alt"):
        return logo["alt"].strip()
    link = soup.select_one(
        "#product_manufacturer a, .product_manufacturer a, "
        "[itemprop='brand'], a[href*='fabricante'], a[href*='manufacturer']"
    )
    if link:
        text = link.get("content") or link.get_text(strip=True)
        if text:
            return text.strip()
    return ""


def _select_availability(soup: BeautifulSoup) -> str:
    qty_el = soup.select_one("#quantityAvailable")
    avail_el = soup.select_one("#availability_value")
    qty = qty_el.get_text(strip=True) if qty_el else ""
    avail = avail_el.get_text(strip=True) if avail_el else ""
    if avail and qty:
        return f"{avail} ({qty})"
    if qty:
        return qty
    return avail


def _select_data_sheet(soup: BeautifulSoup) -> str:
    table = soup.select_one("table.table-data-sheet")
    if not table:
        return ""
    pairs = []
    for row in table.select("tr"):
        cells = row.select("td")
        if len(cells) >= 2:
            key = cells[0].get_text(strip=True)
            value = cells[1].get_text(strip=True)
            if key:
                pairs.append(f"{key}: {value}".strip())
    return "; ".join(pairs)


def parse_detail_page(html: str, product_url: str, category: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    name_el = soup.select_one("h1[itemprop='name'], h1")
    name = name_el.get_text(strip=True) if name_el else ""

    sku_el = soup.select_one("[itemprop='sku']")
    sku = (sku_el.get("content") or sku_el.get_text(strip=True)) if sku_el else ""

    price_el = soup.select_one("#our_price_display, [itemprop='price']")
    raw_price = ""
    if price_el:
        raw_price = price_el.get("content") or price_el.get_text(strip=True)

    cond_el = soup.select_one("#product_condition span, #product_condition .editable")
    estado = cond_el.get_text(strip=True) if cond_el else ""

    img_el = soup.select_one("#bigpic, img[itemprop='image']")
    image_url = absolute_url(img_el["src"]) if img_el and img_el.get("src") else ""

    return {
        "Código de SKU": sku.strip(),
        "Nombre del Producto": name,
        "Descripcion del Producto": _select_long_description(soup),
        "Precio de Venta": format_price(raw_price),
        "Disponibilidad": _select_availability(soup),
        "Categoría": category,
        "Fabricante": _select_manufacturer(soup),
        "Estado": estado,
        "Ficha Técnica": _select_data_sheet(soup),
        "URL de la imagen": image_url,
        "URL del Producto": product_url,
    }


def fetch_product_details(product_url: str, category: str) -> dict:
    session = requests.Session()
    session.headers.update(HEADERS)
    html = fetch_html(session, product_url)
    return parse_detail_page(html, product_url, category)


def scrape_all_products(
    on_progress: Callable[[str], None] | None = None,
) -> list[dict]:
    def report(message: str) -> None:
        print(message)
        if on_progress:
            on_progress(message)

    session = requests.Session()
    session.headers.update(HEADERS)

    report("Descubriendo categorías...")
    categories = discover_categories(session)
    report(f"{len(categories)} categorías encontradas — recolectando productos...")

    products = collect_product_urls(session, categories, on_progress=on_progress)
    report(f"{len(products)} productos únicos — obteniendo detalles...")

    rows: list[dict] = []
    total = len(products)
    completed = 0

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
        futures = {
            executor.submit(fetch_product_details, info["url"], info["category"]): info["url"]
            for info in products.values()
        }
        for future in as_completed(futures):
            try:
                rows.append(future.result())
            except RuntimeError as exc:
                print(f"  Aviso: {exc}", file=sys.stderr)
            completed += 1
            if completed % 25 == 0 or completed == total:
                report(f"Detalle {completed}/{total} productos")

    rows.sort(key=lambda r: r["Nombre del Producto"].lower())
    report(f"Completado: {len(rows)} productos")
    return rows


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
    print("Iniciando scrape de La Increíble ABM...")
    try:
        result = run_scrape()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"\nCompletado: {result['rows']} productos exportados a {result['output_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
