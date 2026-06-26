"""Generate Moderna inventory update file from base database + update template."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

from master_file_io import (
    OUTPUT_DIR,
    MasterFileError,
    MasterFileStore,
    normalize_sku,
    read_master_rows,
    validate_master_columns,
    write_update_rows,
)

BASE_BASENAME = "moderna_base"
BASE_EXTENSION = ".csv"
STORE = MasterFileStore("moderna_plantilla", "moderna_actualizacion")

BASE_CODIGO_COL = "Código"
BASE_COSTO_SRC_COL = "Precio"
BASE_PRECIO_SRC_COL = "Precio MAP"
BASE_STOCK_SRC_COL = "Stock"

MASTER_SKU_COL = "SKU"
MASTER_COSTO_COL = "Costo"
MASTER_PRECIO_COL = "Precio"
MASTER_STOCK_COL = "MODERNA"

REQUIRED_BASE_COLUMNS = (
    BASE_CODIGO_COL,
    BASE_COSTO_SRC_COL,
    BASE_PRECIO_SRC_COL,
    BASE_STOCK_SRC_COL,
)
REQUIRED_MASTER_COLUMNS = (
    MASTER_SKU_COL,
    MASTER_COSTO_COL,
    MASTER_PRECIO_COL,
    MASTER_STOCK_COL,
)


def _base_path() -> Path:
    return OUTPUT_DIR / f"{BASE_BASENAME}{BASE_EXTENSION}"


def find_base_path() -> Path | None:
    path = _base_path()
    return path if path.exists() else None


def save_base_upload(source_path: Path, original_filename: str) -> Path:
    suffix = Path(original_filename).suffix.lower()
    if suffix != BASE_EXTENSION:
        raise MasterFileError("La Base de Datos debe ser .csv.")

    target = _base_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_path), str(target))
    return target


def find_master_path() -> Path | None:
    return STORE.find_master_path()


def find_update_path() -> Path | None:
    return STORE.find_update_path()


def save_master_upload(source_path: Path, original_filename: str) -> Path:
    return STORE.save_master_upload(source_path, original_filename)


def validate_moderna_base_columns(fieldnames: list[str]) -> None:
    validate_master_columns(fieldnames, REQUIRED_BASE_COLUMNS)


def validate_moderna_master_columns(fieldnames: list[str]) -> None:
    validate_master_columns(fieldnames, REQUIRED_MASTER_COLUMNS)


def _parse_stock(value: str) -> int:
    text = (value or "").strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def load_base_index(base_path: Path | None = None) -> dict[str, dict[str, str]]:
    path = base_path or find_base_path()
    if not path:
        raise FileNotFoundError("Sube primero la Base de Datos.")

    fieldnames, rows = read_master_rows(path)
    validate_moderna_base_columns(fieldnames)

    index: dict[str, dict[str, str]] = {}
    for row in rows:
        sku = normalize_sku(row.get(BASE_CODIGO_COL, ""))
        if not sku:
            continue
        index[sku] = {
            "costo": row.get(BASE_COSTO_SRC_COL, ""),
            "precio": row.get(BASE_PRECIO_SRC_COL, ""),
            "stock": row.get(BASE_STOCK_SRC_COL, ""),
        }
    return index


def generate_update(
    base_path: Path | None = None,
    master_path: Path | None = None,
    output_path: Path | None = None,
) -> dict:
    base_file = base_path or find_base_path()
    if not base_file:
        raise FileNotFoundError("Sube primero la Base de Datos.")

    master_file = master_path or find_master_path()
    if not master_file:
        raise FileNotFoundError("Sube primero el Archivo de Actualización.")

    target = output_path or STORE.update_path_for_ext(master_file.suffix.lower())

    fieldnames, master_rows = read_master_rows(master_file)
    validate_moderna_master_columns(fieldnames)

    base_index = load_base_index(base_file)
    if not base_index:
        raise FileNotFoundError("La Base de Datos está vacía o no tiene filas válidas.")

    updated_rows: list[dict[str, str]] = []
    matched = 0
    unmatched = 0
    with_stock = 0
    zero_stock = 0

    for row in master_rows:
        updated = dict(row)
        sku = normalize_sku(row.get(MASTER_SKU_COL, ""))
        base_row = base_index.get(sku)

        if base_row is not None:
            matched += 1
            updated[MASTER_COSTO_COL] = base_row["costo"]
            updated[MASTER_PRECIO_COL] = base_row["precio"]
            stock_qty = _parse_stock(base_row["stock"])
            updated[MASTER_STOCK_COL] = str(stock_qty)
            if stock_qty > 0:
                with_stock += 1
            else:
                zero_stock += 1
        else:
            updated[MASTER_STOCK_COL] = "0"
            zero_stock += 1
            unmatched += 1

        updated_rows.append(updated)

    STORE.cleanup_other_update_formats(target)
    write_update_rows(target, fieldnames, updated_rows)

    return {
        "total": len(updated_rows),
        "matched": matched,
        "unmatched": unmatched,
        "with_stock": with_stock,
        "zero_stock": zero_stock,
        "output_path": str(target),
        "output_format": target.suffix.lstrip(".").lower(),
    }


def _file_status(path: Path | None) -> tuple[bool, int | None, str | None]:
    if not path:
        return False, None, None
    try:
        _, rows = read_master_rows(path)
        return True, len(rows), path.suffix.lstrip(".").lower()
    except (OSError, MasterFileError, csv.Error, ValueError):
        return False, None, None


def get_update_status() -> dict:
    base_path = find_base_path()
    base_uploaded, base_rows, base_format = _file_status(base_path)

    master_path = find_master_path()
    master_uploaded, master_rows, master_format = _file_status(master_path)

    update_path = find_update_path()

    return {
        "base_uploaded": base_uploaded,
        "base_rows": base_rows,
        "base_format": base_format,
        "master_uploaded": master_uploaded,
        "master_rows": master_rows,
        "master_format": master_format,
        "scrape_available": base_uploaded,
        "ready_to_generate": base_uploaded and master_uploaded,
        "update_available": update_path is not None,
        "update_format": update_path.suffix.lstrip(".").lower() if update_path else None,
    }
