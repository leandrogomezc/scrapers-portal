#!/usr/bin/env python3
"""Scrape TecnoBodega products via WooCommerce Store API and export to CSV."""

import csv
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://tecnobodega.com.gt"
API_URL = f"{BASE_URL}/wp-json/wc/store/products"
OUTPUT_PATH = Path(__file__).parent / "output" / "tecnobodega_productos.csv"

CSV_COLUMNS = [
    "Código de SKU",
    "Nombre del Producto",
    "Descripcion del Producto",
    "URL de la imagen",
    "Precio de Venta",
]

PER_PAGE = 100
REQUEST_DELAY = 0.35
MAX_RETRIES = 3
TIMEOUT = 60


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = BeautifulSoup(raw_html, "html.parser").get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def format_wc_price(prices: dict | None) -> str:
    if not prices:
        return ""

    raw = prices.get("sale_price") or prices.get("price") or prices.get("regular_price") or ""
    if raw in ("", "0"):
        return ""

    minor_unit = int(prices.get("currency_minor_unit") or 2)
    try:
        amount = int(raw) / (10**minor_unit)
    except ValueError:
        return ""

    prefix = prices.get("currency_prefix") or "Q"
    return f"{prefix}{amount:,.2f}"


def get_product_image(product: dict) -> str:
    images = product.get("images") or []
    if not images:
        return ""
    first = images[0]
    return (first.get("src") or first.get("thumbnail") or "").strip()


def fetch_products_page(session: requests.Session, page: int) -> tuple[list[dict], int | None]:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(
                API_URL,
                params={"per_page": PER_PAGE, "page": page},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            total_pages_raw = response.headers.get("X-WP-TotalPages")
            total_pages = int(total_pages_raw) if total_pages_raw else None
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Respuesta inesperada de la API de WooCommerce.")
            return payload, total_pages
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = attempt * 2
                print(f"  Reintento {attempt}/{MAX_RETRIES} en {wait}s: {exc}")
                time.sleep(wait)
    raise RuntimeError(f"Fallo al obtener página {page} de {API_URL}: {last_error}")


def product_to_row(product: dict) -> dict:
    description = clean_html(product.get("short_description") or product.get("description") or "")
    return {
        "Código de SKU": (product.get("sku") or "").strip(),
        "Nombre del Producto": (product.get("name") or "").strip(),
        "Descripcion del Producto": description,
        "URL de la imagen": get_product_image(product),
        "Precio de Venta": format_wc_price(product.get("prices")),
    }


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
            "User-Agent": "TecnoBodegaScraper/1.0 (+personal use)",
            "Accept": "application/json",
        }
    )

    all_rows: list[dict] = []
    seen_keys: set[str] = set()
    page = 1
    total_pages: int | None = None

    while True:
        products, header_pages = fetch_products_page(session, page)
        if header_pages is not None:
            total_pages = header_pages

        if not products:
            break

        for product in products:
            row = product_to_row(product)
            dedupe_key = row["Código de SKU"] or f"__no_sku__{row['Nombre del Producto']}"
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            all_rows.append(row)

        if total_pages:
            report(f"Página {page}/{total_pages} — {len(all_rows)} productos acumulados")
        else:
            report(f"Página {page} — {len(all_rows)} productos acumulados")

        if total_pages is not None and page >= total_pages:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

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
    print("Iniciando scrape de TecnoBodega...")
    try:
        result = run_scrape()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"\nCompletado: {result['rows']} productos exportados a {result['output_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
