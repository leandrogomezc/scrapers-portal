"""Generate Beauty Depot inventory update CSV from master catalog + scrape."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from scrape_beautydepot import OUTPUT_PATH as SCRAPE_OUTPUT_PATH

OUTPUT_DIR = Path(__file__).parent / "output"
MASTER_PATH = OUTPUT_DIR / "beautydepot_master.csv"
UPDATE_OUTPUT_PATH = OUTPUT_DIR / "beautydepot_actualizacion.csv"

MASTER_SKU_COL = "SKU"
UPDATE_PRICE_COL = "Precio"
UPDATE_STOCK_COL = "Beauty Depot"

SCRAPE_SKU_COL = "Código de SKU"
SCRAPE_PRICE_COL = "Precio de Venta"

STOCK_IF_FOUND = 10
STOCK_IF_MISSING = 0

REQUIRED_MASTER_COLUMNS = (MASTER_SKU_COL, UPDATE_PRICE_COL, UPDATE_STOCK_COL)


class MasterCsvError(ValueError):
    """Invalid or missing columns in master CSV."""


def _read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {path}")

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise MasterCsvError("El CSV maestro no tiene encabezados.")
        fieldnames = list(reader.fieldnames)
        rows = [{key: (row.get(key) or "") for key in fieldnames} for row in reader]

    return fieldnames, rows


def validate_master_columns(fieldnames: list[str]) -> None:
    missing = [col for col in REQUIRED_MASTER_COLUMNS if col not in fieldnames]
    if missing:
        raise MasterCsvError(
            f"Faltan columnas requeridas en el maestro: {', '.join(missing)}"
        )


def _normalize_sku(value: str) -> str:
    return (value or "").strip()


def load_scrape_index(scrape_path: Path | None = None) -> dict[str, str]:
    path = scrape_path or SCRAPE_OUTPUT_PATH
    _, rows = _read_csv_rows(path)
    index: dict[str, str] = {}
    for row in rows:
        sku = _normalize_sku(row.get(SCRAPE_SKU_COL, ""))
        if not sku:
            continue
        index[sku] = row.get(SCRAPE_PRICE_COL, "")
    return index


def _master_uses_currency_prefix(sample_prices: list[str]) -> bool:
    non_empty = [p.strip() for p in sample_prices if p.strip()]
    if not non_empty:
        return True
    prefixed = sum(1 for p in non_empty if re.match(r"^[A-Za-z$]", p))
    return prefixed >= len(non_empty) / 2


def normalize_price_for_export(scraped_price: str, master_prices: list[str]) -> str:
    scraped = (scraped_price or "").strip()
    if not scraped:
        return ""

    if _master_uses_currency_prefix(master_prices):
        return scraped

    numeric = re.sub(r"[^\d.]", "", scraped.replace(",", ""))
    if not numeric:
        return scraped
    try:
        amount = float(numeric)
    except ValueError:
        return scraped
    return f"{amount:.2f}"


def generate_update(
    master_path: Path | None = None,
    scrape_path: Path | None = None,
    output_path: Path | None = None,
) -> dict:
    master_file = master_path or MASTER_PATH
    target = output_path or UPDATE_OUTPUT_PATH

    fieldnames, master_rows = _read_csv_rows(master_file)
    validate_master_columns(fieldnames)

    scrape_index = load_scrape_index(scrape_path)
    if not scrape_index:
        raise FileNotFoundError(
            "El scrape de Beauty Depot está vacío o no existe. Ejecuta el scrape primero."
        )

    master_prices = [row.get(UPDATE_PRICE_COL, "") for row in master_rows]
    updated_rows: list[dict[str, str]] = []
    matched = 0
    stock_10 = 0
    stock_0 = 0

    for row in master_rows:
        updated = dict(row)
        sku = _normalize_sku(row.get(MASTER_SKU_COL, ""))
        scraped_price = scrape_index.get(sku)

        if scraped_price is not None:
            matched += 1
            updated[UPDATE_PRICE_COL] = normalize_price_for_export(scraped_price, master_prices)
            updated[UPDATE_STOCK_COL] = str(STOCK_IF_FOUND)
            stock_10 += 1
        else:
            updated[UPDATE_STOCK_COL] = str(STOCK_IF_MISSING)
            stock_0 += 1

        updated_rows.append(updated)

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(updated_rows)

    return {
        "total": len(updated_rows),
        "matched": matched,
        "stock_10": stock_10,
        "stock_0": stock_0,
        "output_path": str(target),
    }


def get_update_status() -> dict:
    master_uploaded = MASTER_PATH.exists()
    master_rows = None
    if master_uploaded:
        try:
            _, rows = _read_csv_rows(MASTER_PATH)
            master_rows = len(rows)
        except (OSError, MasterCsvError, csv.Error):
            master_uploaded = False

    scrape_available = SCRAPE_OUTPUT_PATH.exists() and SCRAPE_OUTPUT_PATH.stat().st_size > 0
    update_available = UPDATE_OUTPUT_PATH.exists()

    return {
        "master_uploaded": master_uploaded,
        "master_rows": master_rows,
        "scrape_available": scrape_available,
        "update_available": update_available,
    }
