"""Shared CSV/XLSX read/write helpers for master inventory update files."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pandas as pd

OUTPUT_DIR = Path(__file__).parent / "output"
MASTER_EXTENSIONS = (".xlsx", ".csv")


class MasterFileError(ValueError):
    """Invalid or missing columns in master file."""


class MasterFileStore:
    def __init__(self, master_basename: str, update_basename: str) -> None:
        self.master_basename = master_basename
        self.update_basename = update_basename

    def master_path_for_ext(self, ext: str) -> Path:
        return OUTPUT_DIR / f"{self.master_basename}{ext}"

    def update_path_for_ext(self, ext: str) -> Path:
        return OUTPUT_DIR / f"{self.update_basename}{ext}"

    def find_master_path(self) -> Path | None:
        for ext in MASTER_EXTENSIONS:
            path = self.master_path_for_ext(ext)
            if path.exists():
                return path
        return None

    def find_update_path(self) -> Path | None:
        master_path = self.find_master_path()
        ext = master_path.suffix if master_path else ".xlsx"
        path = self.update_path_for_ext(ext)
        return path if path.exists() else None

    def save_master_upload(self, source_path: Path, original_filename: str) -> Path:
        suffix = Path(original_filename).suffix.lower()
        if suffix not in MASTER_EXTENSIONS:
            raise MasterFileError("Formato no soportado. Usa .xlsx o .csv.")

        for ext in MASTER_EXTENSIONS:
            self.master_path_for_ext(ext).unlink(missing_ok=True)

        target = self.master_path_for_ext(suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(target))
        return target

    def cleanup_other_update_formats(self, target: Path) -> None:
        for ext in MASTER_EXTENSIONS:
            other = self.update_path_for_ext(ext)
            if other != target:
                other.unlink(missing_ok=True)


def cell_to_str(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise MasterFileError("El archivo no tiene encabezados.")
        fieldnames = list(reader.fieldnames)
        rows = [{key: (row.get(key) or "") for key in fieldnames} for row in reader]
    return fieldnames, rows


def read_xlsx_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    df = pd.read_excel(path, dtype=object)
    if df.empty and df.columns.empty:
        raise MasterFileError("El archivo maestro está vacío.")
    fieldnames = [str(col).strip() for col in df.columns]
    rows: list[dict[str, str]] = []
    for record in df.to_dict(orient="records"):
        rows.append({col: cell_to_str(record.get(col)) for col in fieldnames})
    return fieldnames, rows


def read_master_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return read_csv_rows(path)
    if suffix == ".xlsx":
        return read_xlsx_rows(path)
    raise MasterFileError("Formato no soportado. Usa .xlsx o .csv.")


def validate_master_columns(fieldnames: list[str], required_columns: tuple[str, ...]) -> None:
    missing = [col for col in required_columns if col not in fieldnames]
    if missing:
        raise MasterFileError(
            f"Faltan columnas requeridas en el maestro: {', '.join(missing)}"
        )


def write_csv_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_xlsx_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    df = pd.DataFrame(rows, columns=fieldnames)
    df.to_excel(path, index=False, engine="openpyxl")


def write_update_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        write_csv_rows(path, fieldnames, rows)
    else:
        write_xlsx_rows(path, fieldnames, rows)


def normalize_sku(value: str) -> str:
    return (value or "").strip()
