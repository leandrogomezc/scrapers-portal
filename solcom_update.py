"""Generate Solís Comercial inventory update file from master catalog + scrape."""

from __future__ import annotations

import csv
from pathlib import Path

from master_file_io import (
    MasterFileError,
    MasterFileStore,
    normalize_sku,
    read_csv_rows,
    read_master_rows,
    validate_master_columns,
    write_update_rows,
)
from scrape_inventory import OUTPUT_PATH as SCRAPE_OUTPUT_PATH

STORE = MasterFileStore("solcom_master", "solcom_actualizacion")

MASTER_SKU_COL = "SKU"
UPDATE_STOCK_COL = "DataShop"

SCRAPE_SKU_COL = "SKU"
SCRAPE_QTY_COL = "Cantidad"

REQUIRED_MASTER_COLUMNS = (MASTER_SKU_COL, UPDATE_STOCK_COL)


def find_master_path() -> Path | None:
    return STORE.find_master_path()


def find_update_path() -> Path | None:
    return STORE.find_update_path()


def save_master_upload(source_path: Path, original_filename: str) -> Path:
    return STORE.save_master_upload(source_path, original_filename)


def validate_solcom_master_columns(fieldnames: list[str]) -> None:
    validate_master_columns(fieldnames, REQUIRED_MASTER_COLUMNS)


def _parse_quantity(value: str) -> int:
    text = (value or "").strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def load_scrape_index(scrape_path: Path | None = None) -> dict[str, int]:
    path = scrape_path or SCRAPE_OUTPUT_PATH
    _, rows = read_csv_rows(path)
    index: dict[str, int] = {}
    for row in rows:
        sku = normalize_sku(row.get(SCRAPE_SKU_COL, ""))
        if not sku:
            continue
        qty = _parse_quantity(row.get(SCRAPE_QTY_COL, ""))
        index[sku] = index.get(sku, 0) + qty
    return index


def generate_update(
    master_path: Path | None = None,
    scrape_path: Path | None = None,
    output_path: Path | None = None,
) -> dict:
    master_file = master_path or find_master_path()
    if not master_file:
        raise FileNotFoundError("Sube primero el archivo maestro.")

    target = output_path or STORE.update_path_for_ext(master_file.suffix.lower())

    fieldnames, master_rows = read_master_rows(master_file)
    validate_solcom_master_columns(fieldnames)

    scrape_index = load_scrape_index(scrape_path)
    if not scrape_index:
        raise FileNotFoundError(
            "El scrape de Solís Comercial está vacío o no existe. Ejecuta el scrape primero."
        )

    updated_rows: list[dict[str, str]] = []
    matched = 0
    with_stock = 0
    zero_stock = 0

    for row in master_rows:
        updated = dict(row)
        sku = normalize_sku(row.get(MASTER_SKU_COL, ""))
        scraped_qty = scrape_index.get(sku)

        if scraped_qty is not None:
            matched += 1
            updated[UPDATE_STOCK_COL] = str(scraped_qty)
            if scraped_qty > 0:
                with_stock += 1
            else:
                zero_stock += 1
        else:
            updated[UPDATE_STOCK_COL] = "0"
            zero_stock += 1

        updated_rows.append(updated)

    STORE.cleanup_other_update_formats(target)
    write_update_rows(target, fieldnames, updated_rows)

    return {
        "total": len(updated_rows),
        "matched": matched,
        "with_stock": with_stock,
        "zero_stock": zero_stock,
        "output_path": str(target),
        "output_format": target.suffix.lstrip(".").lower(),
    }


def get_update_status() -> dict:
    master_path = find_master_path()
    master_uploaded = master_path is not None
    master_rows = None
    master_format = None

    if master_path:
        try:
            _, rows = read_master_rows(master_path)
            master_rows = len(rows)
            master_format = master_path.suffix.lstrip(".").lower()
        except (OSError, MasterFileError, csv.Error, ValueError):
            master_uploaded = False

    update_path = find_update_path()
    scrape_available = SCRAPE_OUTPUT_PATH.exists() and SCRAPE_OUTPUT_PATH.stat().st_size > 0

    return {
        "master_uploaded": master_uploaded,
        "master_rows": master_rows,
        "master_format": master_format,
        "scrape_available": scrape_available,
        "update_available": update_path is not None,
        "update_format": update_path.suffix.lstrip(".").lower() if update_path else None,
    }
