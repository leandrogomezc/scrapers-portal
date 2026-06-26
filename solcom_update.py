"""Generate Solís Comercial inventory update file from master catalog + scrape."""

from __future__ import annotations

import csv
import re
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
from solcom_prices import (
    build_master_index,
    match_inventory,
    match_prices,
    parse_price_text,
)

STORE = MasterFileStore("solcom_master", "solcom_actualizacion")

MASTER_SKU_COL = "SKU"
UPDATE_STOCK_COL = "Punto Digital"
UPDATE_COST_COL = "Costo"
UPDATE_PRICE_COL = "Precio"

# Los costos pegados vienen en dólares; se convierten a moneda local al escribir.
USD_TO_LOCAL_RATE = 37.1
MIN_GROSS_MARGIN = 0.12

# Candidate columns that may hold the product name to match pasted prices against.
NAME_COLUMN_CANDIDATES = ("Nombre del Producto", "Nombre", "Descripción", "Producto")

# Candidate columns that may hold specs (storage/RAM/connectivity) outside the name.
ATTRIBUTE_COLUMN_CANDIDATES = ("Atributos", "Atributo", "Especificaciones")

# How many unmatched names to surface back to the UI.
UNMATCHED_SAMPLE_SIZE = 15

SCRAPE_SKU_COL = "SKU"
SCRAPE_QTY_COL = "Cantidad"
SCRAPE_NAME_COL = "Nombre del Producto"

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


def _detect_name_column(fieldnames: list[str]) -> str | None:
    for candidate in NAME_COLUMN_CANDIDATES:
        if candidate in fieldnames:
            return candidate
    return None


def _detect_attribute_column(fieldnames: list[str]) -> str | None:
    for candidate in ATTRIBUTE_COLUMN_CANDIDATES:
        if candidate in fieldnames:
            return candidate
    return None


def _convert_cost_amount(price: str) -> float | None:
    try:
        return float(price) * USD_TO_LOCAL_RATE
    except (TypeError, ValueError):
        return None


def _format_cost(amount: float) -> str:
    return f"{amount:.2f}"


def _parse_money(value: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None

    numeric = re.sub(r"[^\d.]", "", text.replace(",", ""))
    if not numeric:
        return None
    try:
        return float(numeric)
    except ValueError:
        return None


def _minimum_price_for_margin(cost: float) -> int:
    return round(cost / (1 - MIN_GROSS_MARGIN))


def _maybe_update_price(row: dict[str, str], cost: float) -> bool:
    current_price = _parse_money(row.get(UPDATE_PRICE_COL, ""))
    minimum_price = _minimum_price_for_margin(cost)

    if not current_price or current_price <= 0:
        row[UPDATE_PRICE_COL] = str(minimum_price)
        return True

    current_margin = (current_price - cost) / current_price
    if current_margin < MIN_GROSS_MARGIN:
        row[UPDATE_PRICE_COL] = str(minimum_price)
        return True

    return False


def apply_prices(
    fieldnames: list[str],
    rows: list[dict[str, str]],
    prices_text: str,
) -> dict:
    """Overwrite ``Costo`` and protect ``Precio`` on matched rows.

    Pasted values are USD costs and are multiplied by ``USD_TO_LOCAL_RATE``
    before writing. If the gross margin between ``Costo`` and ``Precio`` is
    below ``MIN_GROSS_MARGIN``, ``Precio`` is raised to the minimum value.
    Only existing rows are updated; pasted entries without a confident match
    are ignored (never added as new rows).
    """
    parsed = parse_price_text(prices_text)
    if not parsed:
        return {
            "prices_matched": 0,
            "prices_unmatched": 0,
            "sale_prices_adjusted": 0,
            "unmatched_sample": [],
        }

    if UPDATE_COST_COL not in fieldnames:
        raise MasterFileError(
            f"El maestro no tiene la columna requerida '{UPDATE_COST_COL}'."
        )
    if UPDATE_PRICE_COL not in fieldnames:
        raise MasterFileError(
            f"El maestro no tiene la columna requerida '{UPDATE_PRICE_COL}'."
        )

    name_col = _detect_name_column(fieldnames)
    if not name_col:
        raise MasterFileError(
            "El maestro no tiene una columna de nombre de producto "
            f"(se buscó: {', '.join(NAME_COLUMN_CANDIDATES)})."
        )

    attr_col = _detect_attribute_column(fieldnames)
    master_index = build_master_index(rows, name_col, attr_col)
    result = match_prices(parsed, master_index)
    sale_prices_adjusted = 0

    for row_idx, price in result.prices_by_row.items():
        cost = _convert_cost_amount(price)
        if cost is None:
            rows[row_idx][UPDATE_COST_COL] = price
            continue

        rows[row_idx][UPDATE_COST_COL] = _format_cost(cost)
        if _maybe_update_price(rows[row_idx], cost):
            sale_prices_adjusted += 1

    return {
        "prices_matched": len(result.matched),
        "prices_unmatched": len(result.unmatched),
        "sale_prices_adjusted": sale_prices_adjusted,
        "unmatched_sample": result.unmatched[:UNMATCHED_SAMPLE_SIZE],
    }


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


def load_scrape_entries(
    scrape_path: Path | None = None,
) -> list[tuple[str, int, str]]:
    """Return scrape rows as ``(sku, qty, name)`` for name-based matching."""
    path = scrape_path or SCRAPE_OUTPUT_PATH
    _, rows = read_csv_rows(path)
    entries: list[tuple[str, int, str]] = []
    for row in rows:
        name = (row.get(SCRAPE_NAME_COL, "") or "").strip()
        if not name:
            continue
        sku = normalize_sku(row.get(SCRAPE_SKU_COL, ""))
        qty = _parse_quantity(row.get(SCRAPE_QTY_COL, ""))
        entries.append((sku, qty, name))
    return entries


def generate_update(
    master_path: Path | None = None,
    scrape_path: Path | None = None,
    output_path: Path | None = None,
    prices_text: str | None = None,
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
    sku_matched_rows: set[int] = set()
    consumed_skus: set[str] = set()

    for idx, row in enumerate(master_rows):
        updated = dict(row)
        sku = normalize_sku(row.get(MASTER_SKU_COL, ""))
        scraped_qty = scrape_index.get(sku)

        if scraped_qty is not None:
            matched += 1
            updated[UPDATE_STOCK_COL] = str(scraped_qty)
            sku_matched_rows.add(idx)
            consumed_skus.add(sku)
        else:
            updated[UPDATE_STOCK_COL] = "0"

        updated_rows.append(updated)

    name_matched = 0
    skus_written = 0
    name_col = _detect_name_column(fieldnames)
    if name_col:
        attr_col = _detect_attribute_column(fieldnames)
        master_index = [
            (i, sig)
            for (i, sig) in build_master_index(updated_rows, name_col, attr_col)
            if i not in sku_matched_rows
        ]
        entries = [
            (sku, qty, name)
            for (sku, qty, name) in load_scrape_entries(scrape_path)
            if sku not in consumed_skus
        ]
        inventory = match_inventory(entries, master_index)
        for row_idx, (scrape_sku, qty) in inventory.items():
            name_matched += 1
            updated_rows[row_idx][UPDATE_STOCK_COL] = str(qty)
            if not normalize_sku(updated_rows[row_idx].get(MASTER_SKU_COL, "")) and scrape_sku:
                updated_rows[row_idx][MASTER_SKU_COL] = scrape_sku
                skus_written += 1

    with_stock = sum(
        1 for row in updated_rows if _parse_quantity(row.get(UPDATE_STOCK_COL, "")) > 0
    )
    zero_stock = len(updated_rows) - with_stock

    price_stats = {
        "prices_matched": 0,
        "prices_unmatched": 0,
        "sale_prices_adjusted": 0,
        "unmatched_sample": [],
    }
    if prices_text and prices_text.strip():
        price_stats = apply_prices(fieldnames, updated_rows, prices_text)

    STORE.cleanup_other_update_formats(target)
    write_update_rows(target, fieldnames, updated_rows)

    return {
        "total": len(updated_rows),
        "matched": matched,
        "name_matched": name_matched,
        "skus_written": skus_written,
        "with_stock": with_stock,
        "zero_stock": zero_stock,
        "output_path": str(target),
        "output_format": target.suffix.lstrip(".").lower(),
        **price_stats,
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
