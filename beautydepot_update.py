"""Generate Beauty Depot inventory update file from master catalog + scrape."""

from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path

import pandas as pd

from scrape_beautydepot import OUTPUT_PATH as SCRAPE_OUTPUT_PATH

OUTPUT_DIR = Path(__file__).parent / "output"
MASTER_BASENAME = "beautydepot_master"
UPDATE_BASENAME = "beautydepot_actualizacion"
MASTER_EXTENSIONS = (".xlsx", ".csv")

MASTER_SKU_COL = "SKU"
UPDATE_PRICE_COL = "Precio"
UPDATE_STOCK_COL = "Beauty Depot"

SCRAPE_SKU_COL = "Código de SKU"
SCRAPE_PRICE_COL = "Precio de Venta"

STOCK_IF_FOUND = 10
STOCK_IF_MISSING = 0

REQUIRED_MASTER_COLUMNS = (MASTER_SKU_COL, UPDATE_PRICE_COL, UPDATE_STOCK_COL)


class MasterFileError(ValueError):
    """Invalid or missing columns in master file."""


def _master_path_for_ext(ext: str) -> Path:
    return OUTPUT_DIR / f"{MASTER_BASENAME}{ext}"


def _update_path_for_ext(ext: str) -> Path:
    return OUTPUT_DIR / f"{UPDATE_BASENAME}{ext}"


def find_master_path() -> Path | None:
    for ext in MASTER_EXTENSIONS:
        path = _master_path_for_ext(ext)
        if path.exists():
            return path
    return None


def find_update_path() -> Path | None:
    master_path = find_master_path()
    ext = master_path.suffix if master_path else ".xlsx"
    path = _update_path_for_ext(ext)
    return path if path.exists() else None


def _cell_to_str(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise MasterFileError("El archivo maestro no tiene encabezados.")
        fieldnames = list(reader.fieldnames)
        rows = [{key: (row.get(key) or "") for key in fieldnames} for row in reader]
    return fieldnames, rows


def _read_xlsx_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    df = pd.read_excel(path, dtype=object)
    if df.empty and df.columns.empty:
        raise MasterFileError("El archivo maestro está vacío.")
    fieldnames = [str(col).strip() for col in df.columns]
    rows: list[dict[str, str]] = []
    for record in df.to_dict(orient="records"):
        rows.append({col: _cell_to_str(record.get(col)) for col in fieldnames})
    return fieldnames, rows


def read_master_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv_rows(path)
    if suffix == ".xlsx":
        return _read_xlsx_rows(path)
    raise MasterFileError("Formato no soportado. Usa .xlsx o .csv.")


def validate_master_columns(fieldnames: list[str]) -> None:
    missing = [col for col in REQUIRED_MASTER_COLUMNS if col not in fieldnames]
    if missing:
        raise MasterFileError(
            f"Faltan columnas requeridas en el maestro: {', '.join(missing)}"
        )


def save_master_upload(source_path: Path, original_filename: str) -> Path:
    suffix = Path(original_filename).suffix.lower()
    if suffix not in MASTER_EXTENSIONS:
        raise MasterFileError("Formato no soportado. Usa .xlsx o .csv.")

    for ext in MASTER_EXTENSIONS:
        _master_path_for_ext(ext).unlink(missing_ok=True)

    target = _master_path_for_ext(suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_path), str(target))
    return target


def _write_csv_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_xlsx_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    df = pd.DataFrame(rows, columns=fieldnames)
    df.to_excel(path, index=False, engine="openpyxl")


def write_update_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        _write_csv_rows(path, fieldnames, rows)
    else:
        _write_xlsx_rows(path, fieldnames, rows)


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
    master_file = master_path or find_master_path()
    if not master_file:
        raise FileNotFoundError("Sube primero el archivo maestro.")

    target = output_path or _update_path_for_ext(master_file.suffix.lower())

    fieldnames, master_rows = read_master_rows(master_file)
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

    for ext in MASTER_EXTENSIONS:
        other = _update_path_for_ext(ext)
        if other != target:
            other.unlink(missing_ok=True)

    write_update_rows(target, fieldnames, updated_rows)

    return {
        "total": len(updated_rows),
        "matched": matched,
        "stock_10": stock_10,
        "stock_0": stock_0,
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
