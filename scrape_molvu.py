#!/usr/bin/env python3
"""Scrape Molvu products via Shopify products.json and export to CSV."""

import csv
import html
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://molvu.com.gt"
API_URL = f"{BASE_URL}/products.json"
OUTPUT_PATH = Path(__file__).parent / "output" / "molvu_productos.csv"

CSV_COLUMNS = [
    "Código de SKU",
    "Nombre del Producto",
    "Descripcion del Producto",
    "URL de la imagen",
    "Precio de Venta",
]

PER_PAGE = 250
REQUEST_DELAY = 0.5
MAX_RETRIES = 3
TIMEOUT = 60


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = BeautifulSoup(raw_html, "html.parser").get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def format_price(raw_price: str | None) -> str:
    if raw_price in (None, ""):
        return ""
    try:
        amount = float(str(raw_price).replace(",", ""))
    except ValueError:
        return ""
    return f"Q{amount:,.2f}"


def build_product_name(product_title: str, variant_title: str) -> str:
    title = html.unescape(product_title or "").strip()
    variant = html.unescape(variant_title or "").strip()
    if not variant or variant.lower() == "default title":
        return title
    if variant.lower() in title.lower():
        return title
    return f"{title} — {variant}"


def get_variant_image(variant: dict, product_images: list[dict]) -> str:
    featured = variant.get("featured_image")
    if isinstance(featured, dict) and featured.get("src"):
        return featured["src"]
    if product_images:
        return product_images[0].get("src", "") or ""
    return ""


def fetch_json(session: requests.Session, params: dict) -> dict:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(API_URL, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = attempt * 2
                print(f"  Reintento {attempt}/{MAX_RETRIES} en {wait}s: {exc}")
                time.sleep(wait)
    raise RuntimeError(f"Fallo al obtener {API_URL}: {last_error}")


def product_variants_to_rows(product: dict) -> list[dict]:
    description = clean_html(product.get("body_html", ""))
    images = product.get("images", []) or []
    product_title = product.get("title", "") or ""
    rows: list[dict] = []

    for variant in product.get("variants", []) or []:
        rows.append(
            {
                "Código de SKU": (variant.get("sku") or "").strip(),
                "Nombre del Producto": build_product_name(
                    product_title, variant.get("title", "") or ""
                ),
                "Descripcion del Producto": description,
                "URL de la imagen": get_variant_image(variant, images),
                "Precio de Venta": format_price(variant.get("price")),
            }
        )

    return rows


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
            "User-Agent": "MolvuScraper/1.0 (+personal use)",
            "Accept": "application/json",
        }
    )

    all_rows: list[dict] = []
    seen_skus: set[str] = set()
    page = 1

    while True:
        payload = fetch_json(session, {"limit": PER_PAGE, "page": page})
        products = payload.get("products", [])
        if not products:
            break

        for product in products:
            for row in product_variants_to_rows(product):
                sku = row["Código de SKU"]
                dedupe_key = sku if sku else f"__no_sku__{row['Nombre del Producto']}"
                if dedupe_key in seen_skus:
                    continue
                seen_skus.add(dedupe_key)
                all_rows.append(row)

        report(f"Página {page} — {len(all_rows)} filas acumuladas")
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
    print("Iniciando scrape de Molvu...")
    try:
        result = run_scrape()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"\nCompletado: {result['rows']} productos exportados a {result['output_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
