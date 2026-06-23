#!/usr/bin/env python3
"""Scrape Beauty Depot products via WooCommerce Store API and export to CSV."""

import csv
import html
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://beautydepot.com.gt"
API_URL = f"{BASE_URL}/wp-json/wc/store/v1/products"
OUTPUT_PATH = Path(__file__).parent / "output" / "beautydepot_productos.csv"

CSV_COLUMNS = [
    "Código de SKU",
    "Nombre del Producto",
    "Descripcion del Producto",
    "URL de la imagen",
    "Precio de Venta",
]

PER_PAGE = 100
REQUEST_DELAY = 0.5
MAX_RETRIES = 3
TIMEOUT = 60


def clean_html(html: str) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def format_price(prices: dict) -> str:
    if not prices:
        return ""
    minor_unit = prices.get("currency_minor_unit", 2)
    raw_price = prices.get("price")
    if raw_price in (None, ""):
        return ""
    amount = int(raw_price) / (10**minor_unit)
    symbol = prices.get("currency_symbol", "Q")
    return f"{symbol}{amount:,.2f}"


def get_image_url(images: list) -> str:
    if not images:
        return ""
    return images[0].get("src", "")


def fetch_json(session: requests.Session, url: str, params: dict | None = None) -> tuple:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json(), response.headers
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = attempt * 2
                print(f"  Reintento {attempt}/{MAX_RETRIES} en {wait}s: {exc}")
                time.sleep(wait)
    raise RuntimeError(f"Fallo al obtener {url}: {last_error}")


def product_to_row(product: dict) -> dict:
    return {
        "Código de SKU": product.get("sku", "") or "",
        "Nombre del Producto": html.unescape(product.get("name", "") or ""),
        "Descripcion del Producto": clean_html(product.get("description", "")),
        "URL de la imagen": get_image_url(product.get("images", [])),
        "Precio de Venta": format_price(product.get("prices", {})),
    }


def expand_product(session: requests.Session, product: dict) -> list[dict]:
    product_type = product.get("type", "simple")
    if product_type != "variable":
        return [product_to_row(product)]

    product_id = product.get("id")
    if not product_id:
        return [product_to_row(product)]

    detail, _ = fetch_json(session, f"{API_URL}/{product_id}")
    time.sleep(REQUEST_DELAY)

    variation_refs = detail.get("variations", [])
    if not variation_refs:
        return [product_to_row(detail)]

    rows = []
    parent_description = detail.get("description", "")

    for variation_ref in variation_refs:
        if isinstance(variation_ref, dict):
            variation_id = variation_ref.get("id")
        else:
            variation_id = variation_ref

        if not variation_id:
            continue

        variation, _ = fetch_json(session, f"{API_URL}/{variation_id}")
        time.sleep(REQUEST_DELAY)

        merged = {
            "sku": variation.get("sku", "") or "",
            "name": variation.get("name", detail.get("name", "")),
            "description": parent_description,
            "images": variation.get("images") or detail.get("images", []),
            "prices": variation.get("prices", {}),
        }
        rows.append(product_to_row(merged))

    return rows or [product_to_row(detail)]


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
            "User-Agent": "BeautyDepotScraper/1.0 (+personal use)",
            "Accept": "application/json",
        }
    )

    all_rows: list[dict] = []
    seen_skus: set[str] = set()
    page = 1
    total_pages = None

    while True:
        params = {"per_page": PER_PAGE, "page": page}
        products, headers = fetch_json(session, API_URL, params=params)

        if total_pages is None:
            total_pages = int(headers.get("X-WP-TotalPages", 1))
            total_items = headers.get("X-WP-Total", "?")
            report(f"Total de productos en catálogo: {total_items} ({total_pages} páginas)")

        if not products:
            break

        for product in products:
            for row in expand_product(session, product):
                sku = row["Código de SKU"]
                dedupe_key = sku if sku else f"__no_sku__{row['Nombre del Producto']}"
                if dedupe_key in seen_skus:
                    continue
                seen_skus.add(dedupe_key)
                all_rows.append(row)

        report(f"Página {page}/{total_pages} — {len(all_rows)} filas acumuladas")
        page += 1
        time.sleep(REQUEST_DELAY)

        if page > total_pages:
            break

    return all_rows


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
    print("Iniciando scrape de Beauty Depot...")
    try:
        result = run_scrape()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"\nCompletado: {result['rows']} productos exportados a {result['output_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
