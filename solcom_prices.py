"""Parse a pasted price list and match entries to master rows by name + specs.

The pasted text looks like:

    APPLE|VARIA SIN AVISO
    $ 1175…..17 PRO (256) ESIM
    SAMSUNG
    $ 130…..A16 (128_4) DS

Each price line is ``$ <price> <separator> <product name>`` where the
separator is the ellipsis character ``…`` optionally followed by dots. Lines
that do not start with ``$`` are treated as brand/section headers and only used
to track the current brand (a soft tie-breaker for matching).

Matching is heuristic: a pasted entry matches a master row when all of its
model tokens are contained in the master name and the numeric specs
(storage / RAM / connectivity) are compatible. Ambiguous matches are skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Storage values that are plausible for a bare parenthetical number like "(256)".
STORAGE_SET = {16, 32, 64, 128, 256, 512, 1024, 2048}

# Words that never help identify a model and only add noise to the token set.
NOISE_TOKENS = {"DS", "ESIM", "SIM", "DUAL", "GEN", "GB", "RAM", "SSD", "TB"}

# Connectivity tokens are handled as a spec, not as model tokens.
CONNECTIVITY_TOKENS = {"WIFI", "WIFI+CELL", "CELL", "LTE", "4G"}


@dataclass(frozen=True)
class Signature:
    tokens: frozenset[str]
    storage: int | None
    ram: int | None
    connectivity: str | None
    brand: str | None = None
    raw: str = ""


@dataclass
class ParsedPrice:
    price: str
    name: str
    brand: str | None = None


def _leading_int(text: str) -> int | None:
    match = re.match(r"\s*(\d+)", text or "")
    return int(match.group(1)) if match else None


def _parse_storage_value(token: str) -> int | None:
    token = (token or "").strip().upper().replace(" ", "")
    match = re.match(r"^(\d+)TB$", token)
    if match:
        return int(match.group(1)) * 1024
    match = re.match(r"^(\d+)(?:GB)?$", token)
    if match:
        return int(match.group(1))
    return None


def _detect_connectivity(name: str) -> str | None:
    upper = name.upper()
    compact = re.sub(r"\s+", "", upper)
    if "WIFI+CELL" in compact:
        return "CELL"
    if "WIFI" in upper:
        return "WIFI"
    if "LTE" in upper:
        return "LTE"
    if "CELL" in upper:
        return "CELL"
    return None


def extract_specs(name: str) -> tuple[int | None, int | None, str | None]:
    """Return ``(storage_gb, ram_gb, connectivity)`` parsed from a product name.

    Handles the pasted conventions (``(storage_ram)``, ``(256)``,
    ``(512GB SSD/16GB RAM)``) as well as inline master styles such as
    ``"A07 4GB 64 GB"`` or ``"S25 ULTRA 1TB"``.
    """
    connectivity = _detect_connectivity(name)
    storage: int | None = None
    ram: int | None = None

    for content in re.findall(r"\(([^)]*)\)", name):
        chunk = content.strip()
        if not chunk:
            continue
        upper = chunk.upper()
        if "SSD" in upper or "RAM" in upper:
            ssd = re.search(r"(\d+)\s*(TB|GB)?\s*SSD", upper)
            if ssd:
                storage = int(ssd.group(1)) * (1024 if ssd.group(2) == "TB" else 1)
            ram_match = re.search(r"(\d+)\s*GB?\s*RAM", upper)
            if ram_match:
                ram = int(ram_match.group(1))
            continue
        if "_" in chunk:
            parts = chunk.split("_")
            parsed_storage = _parse_storage_value(parts[0])
            if parsed_storage is not None:
                storage = parsed_storage
            if len(parts) > 1:
                parsed_ram = _leading_int(parts[1])
                if parsed_ram is not None:
                    ram = parsed_ram
            continue
        parsed_storage = _parse_storage_value(chunk)
        if parsed_storage is not None and (
            parsed_storage in STORAGE_SET or "GB" in upper or "TB" in upper
        ):
            storage = parsed_storage

    if storage is None and ram is None:
        upper = name.upper()
        tb = re.search(r"(\d+)\s*TB", upper)
        if tb:
            storage = int(tb.group(1)) * 1024
        ssd = re.search(r"(\d+)\s*(TB|GB)\s*SSD", upper)
        if ssd:
            storage = int(ssd.group(1)) * (1024 if ssd.group(2) == "TB" else 1)
        ram_match = re.search(r"(\d+)\s*GB?\s*RAM", upper)
        if ram_match:
            ram = int(ram_match.group(1))

        gb_numbers = [int(value) for value in re.findall(r"(\d+)\s*GB", upper)]
        if storage is None and ram is None and len(gb_numbers) >= 2:
            storage, ram = max(gb_numbers), min(gb_numbers)
        elif storage is None and gb_numbers:
            remaining = [g for g in gb_numbers if g != ram]
            if remaining:
                storage = max(remaining)
        elif ram is None and gb_numbers:
            remaining = [g for g in gb_numbers if g != storage]
            if remaining:
                ram = min(remaining)

    return storage, ram, connectivity


def _model_tokens(name: str) -> frozenset[str]:
    base = re.sub(r"\([^)]*\)", " ", name).upper()
    base = re.sub(r"[^A-Z0-9+.]", " ", base)
    tokens: set[str] = set()
    for token in base.split():
        if token in NOISE_TOKENS or token in CONNECTIVITY_TOKENS:
            continue
        if token.startswith("WIFI") or token == "LTE" or token == "CELL":
            continue
        # Pure storage/RAM numbers carrying a unit (e.g. "256GB", "4GB", "1TB").
        if re.fullmatch(r"\d+(GB|TB|MB)", token):
            continue
        tokens.add(token)
    return frozenset(tokens)


def build_signature(name: str, brand: str | None = None) -> Signature:
    storage, ram, connectivity = extract_specs(name)
    return Signature(
        tokens=_model_tokens(name),
        storage=storage,
        ram=ram,
        connectivity=connectivity,
        brand=(brand or "").strip().upper() or None,
        raw=name.strip(),
    )


def _clean_price(raw: str) -> str | None:
    numeric = re.sub(r"[^\d.]", "", (raw or "").replace(",", "")).strip(".")
    if not numeric:
        return None
    if "." in numeric:
        try:
            value = float(numeric)
        except ValueError:
            return numeric
        return str(int(value)) if value.is_integer() else f"{value}"
    return numeric


_PRICE_LINE = re.compile(r"^(\d[\d.,]*)\s*(?:…\.*|\.{2,})\s*(.+)$")


def _looks_like_header(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("$"):
        return False
    return True


def _brand_from_header(line: str) -> str | None:
    head = line.split("|", 1)[0].strip()
    if not head:
        return None
    # Only treat short, mostly-uppercase headers as brand markers.
    letters = [c for c in head if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) < 0.7:
        return None
    first = re.split(r"\s+", head)[0].upper()
    return first or None


def parse_price_text(text: str) -> list[ParsedPrice]:
    if not text:
        return []

    parsed: list[ParsedPrice] = []
    current_brand: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if not stripped.startswith("$"):
            if _looks_like_header(stripped):
                current_brand = _brand_from_header(stripped) or current_brand
            continue

        rest = stripped[1:].strip()
        match = _PRICE_LINE.match(rest)
        if not match:
            continue
        price = _clean_price(match.group(1))
        name = match.group(2).strip()
        if not price or not name:
            continue
        parsed.append(ParsedPrice(price=price, name=name, brand=current_brand))

    return parsed


def build_master_index(
    rows: list[dict[str, str]], name_col: str
) -> list[tuple[int, Signature]]:
    index: list[tuple[int, Signature]] = []
    for idx, row in enumerate(rows):
        name = (row.get(name_col) or "").strip()
        if not name:
            continue
        index.append((idx, build_signature(name)))
    return index


def _is_candidate(pasted: Signature, master: Signature) -> bool:
    if not pasted.tokens or not pasted.tokens.issubset(master.tokens):
        return False
    if pasted.storage is not None:
        if master.storage is None or master.storage != pasted.storage:
            return False
    if (
        pasted.ram is not None
        and master.ram is not None
        and pasted.ram != master.ram
    ):
        return False
    if (
        pasted.connectivity is not None
        and master.connectivity is not None
        and pasted.connectivity != master.connectivity
    ):
        return False
    return True


def _score(pasted: Signature, master: Signature) -> tuple[int, int, int, int]:
    ram_match = int(
        pasted.ram is not None
        and master.ram is not None
        and pasted.ram == master.ram
    )
    conn_match = int(
        pasted.connectivity is not None
        and master.connectivity is not None
        and pasted.connectivity == master.connectivity
    )
    brand_match = int(bool(pasted.brand) and pasted.brand in master.tokens)
    extra = len(master.tokens - pasted.tokens)
    return (ram_match, conn_match, brand_match, -extra)


def _best_match(
    pasted: Signature, master_index: list[tuple[int, Signature]]
) -> int | None:
    scored: list[tuple[tuple[int, int, int, int], int]] = []
    for idx, master in master_index:
        if _is_candidate(pasted, master):
            scored.append((_score(pasted, master), idx))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # ambiguous: refuse to guess
    return scored[0][1]


@dataclass
class PriceMatchResult:
    prices_by_row: dict[int, str]
    matched: list[str]
    unmatched: list[str]


def match_prices(
    parsed: list[ParsedPrice], master_index: list[tuple[int, Signature]]
) -> PriceMatchResult:
    prices_by_row: dict[int, str] = {}
    matched: list[str] = []
    unmatched: list[str] = []

    for entry in parsed:
        signature = build_signature(entry.name, entry.brand)
        row_idx = _best_match(signature, master_index)
        if row_idx is None:
            unmatched.append(entry.name)
            continue
        prices_by_row[row_idx] = entry.price
        matched.append(entry.name)

    return PriceMatchResult(
        prices_by_row=prices_by_row, matched=matched, unmatched=unmatched
    )
