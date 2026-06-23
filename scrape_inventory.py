#!/usr/bin/env python3
"""Scraper de inventario multi-bodega para Solcom ERP."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from dotenv import load_dotenv
from playwright.sync_api import Page, Response, sync_playwright

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_PATH = OUTPUT_DIR / "inventario.csv"
PIVOT_API_PATH = "/api/inventory/pivot?showZeroStock=false"
DEFAULT_SUPABASE_URL = "https://pknkpvysiarfxvrhjqcx.supabase.co"
DEFAULT_SUPABASE_ANON_KEY = "sb_publishable_Cl0OcY_9jV5dPYOmkRh72g_sY1R50Og"

EXPORT_COLUMNS = [
    "SKU",
    "Nombre del Producto",
    "Marca",
    "Cantidad",
    "Condicion",
]
INVENTORY_KEYWORDS = (
    "sku",
    "codigo",
    "code",
    "product",
    "producto",
    "stock",
    "quantity",
    "cantidad",
    "bodega",
    "warehouse",
    "inventario",
    "inventory",
    "existencia",
)
ARRAY_KEYS = ("data", "items", "results", "records", "products", "productos", "inventory", "inventario", "rows")


@dataclass
class Config:
    email: str
    password: str
    base_url: str
    inventory_url: str
    headed: bool = False
    discover: bool = False


@dataclass
class CapturedResponse:
    url: str
    status: int
    content_type: str
    body: Any
    score: int = 0


@dataclass
class ScrapeResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    warehouses: set[str] = field(default_factory=set)
    source: str = ""


def load_config(args: argparse.Namespace | None = None, headed: bool = False) -> Config:
    load_dotenv()
    email = os.getenv("SOLCOM_EMAIL", "").strip()
    password = os.getenv("SOLCOM_PASSWORD", "").strip()
    base_url = os.getenv("SOLCOM_BASE_URL", "https://solcom-erp.vercel.app").rstrip("/")

    missing = []
    if not email:
        missing.append("SOLCOM_EMAIL")
    if not password:
        missing.append("SOLCOM_PASSWORD")
    if missing and (args is None or not args.discover):
        print(
            f"Error: faltan variables en .env: {', '.join(missing)}",
            file=sys.stderr,
        )
        print("Copia .env.example a .env y completa tus credenciales.", file=sys.stderr)
        sys.exit(1)

    return Config(
        email=email,
        password=password,
        base_url=base_url,
        inventory_url=f"{base_url}/inventory",
        headed=args.headed if args else headed,
        discover=args.discover if args else False,
    )


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def save_screenshot(page: Page, filename: str) -> Path:
    ensure_output_dir()
    path = OUTPUT_DIR / filename
    page.screenshot(path=path, full_page=True)
    return path


def is_login_page(page: Page) -> bool:
    password_inputs = page.locator('input[type="password"]')
    if password_inputs.count() == 0:
        return False

    text = page.locator("body").inner_text().lower()
    login_markers = ("login", "ingresar", "correo", "contrasena", "contraseña", "acceso")
    return any(marker in text for marker in login_markers)


def fill_first_visible(page: Page, selectors: list[str], value: str) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0 and locator.is_visible():
                locator.fill(value)
                return True
        except Exception:
            continue
    return False


def click_submit(page: Page) -> bool:
    selectors = [
        'button[type="submit"]',
        'button:has-text("Ingresar")',
        'button:has-text("Entrar")',
        'button:has-text("Login")',
        'input[type="submit"]',
        '[role="button"]:has-text("Ingresar")',
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0 and locator.is_visible():
                locator.click()
                return True
        except Exception:
            continue
    return False


def login(page: Page, config: Config) -> None:
    print(f"Navegando a {config.inventory_url}...")
    page.goto(config.inventory_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1500)

    if not is_login_page(page):
        print("Sesion activa o dashboard ya visible.")
        return

    print("Iniciando sesion...")
    email_filled = fill_first_visible(
        page,
        [
            'input[type="email"]',
            'input[name*="email" i]',
            'input[autocomplete="email"]',
            'input[placeholder*="correo" i]',
            'input[placeholder*="email" i]',
        ],
        config.email,
    )
    password_filled = fill_first_visible(
        page,
        [
            'input[type="password"]',
            'input[name*="password" i]',
            'input[autocomplete="current-password"]',
        ],
        config.password,
    )

    if not email_filled or not password_filled:
        screenshot = save_screenshot(page, "error-login.png")
        raise RuntimeError(
            f"No se encontraron campos de login. Revisa selectores. Screenshot: {screenshot}"
        )

    if not click_submit(page):
        screenshot = save_screenshot(page, "error-login.png")
        raise RuntimeError(
            f"No se encontro boton de ingreso. Screenshot: {screenshot}"
        )

    page.wait_for_load_state("networkidle", timeout=60_000)
    page.wait_for_timeout(2000)

    if is_login_page(page):
        screenshot = save_screenshot(page, "error-login.png")
        raise RuntimeError(
            "Login fallido: credenciales invalidas o formulario no aceptado. "
            f"Screenshot: {screenshot}"
        )

    print("Login exitoso.")


def flatten_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def normalize_key(key: str) -> str:
    key = key.strip().lower()
    key = re.sub(r"[^a-z0-9]+", "_", key)
    return key.strip("_")


def inventory_score(payload: Any) -> int:
    text = json.dumps(payload, ensure_ascii=False).lower()
    return sum(1 for keyword in INVENTORY_KEYWORDS if keyword in text)


def find_arrays(payload: Any, path: str = "$") -> list[tuple[str, list[Any]]]:
    found: list[tuple[str, list[Any]]] = []
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        found.append((path, payload))
    elif isinstance(payload, dict):
        for key, value in payload.items():
            child_path = f"{path}.{key}"
            if isinstance(value, list) and value and isinstance(value[0], dict):
                found.append((child_path, value))
            elif isinstance(value, (dict, list)):
                found.extend(find_arrays(value, child_path))
    return found


def choose_best_array(payload: Any) -> tuple[str, list[dict[str, Any]]] | None:
    candidates = find_arrays(payload)
    if not candidates:
        return None

    best_path = ""
    best_rows: list[dict[str, Any]] = []
    best_score = -1

    for path, rows in candidates:
        dict_rows = [row for row in rows if isinstance(row, dict)]
        if not dict_rows:
            continue
        score = inventory_score(dict_rows) * 10 + len(dict_rows)
        if score > best_score:
            best_score = score
            best_path = path
            best_rows = dict_rows

    if not best_rows:
        return None
    return best_path, best_rows


def extract_warehouse_name(record: dict[str, Any]) -> str:
    for key in ("bodega", "warehouse", "warehouse_name", "store", "location", "sucursal"):
        if key in record and record[key]:
            return flatten_value(record[key])
        normalized_hits = [k for k in record if normalize_key(k) == key]
        for hit in normalized_hits:
            if record[hit]:
                return flatten_value(record[hit])
    return ""


def flatten_nested_inventory(rows: list[dict[str, Any]], scraped_at: str) -> list[dict[str, Any]]:
    flat_rows: list[dict[str, Any]] = []

    for row in rows:
        nested_lists: list[tuple[str, list[Any]]] = []
        base_fields: dict[str, Any] = {}

        for key, value in row.items():
            norm_key = normalize_key(key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                nested_lists.append((norm_key, value))
            else:
                base_fields[norm_key] = flatten_value(value)

        if not nested_lists:
            base_fields.setdefault("bodega", extract_warehouse_name(row) or base_fields.get("bodega", ""))
            base_fields["actualizado_en"] = scraped_at
            flat_rows.append(base_fields)
            continue

        for nested_key, nested_rows in nested_lists:
            for nested in nested_rows:
                if not isinstance(nested, dict):
                    continue
                merged = dict(base_fields)
                for nk, nv in nested.items():
                    merged[normalize_key(nk)] = flatten_value(nv)
                merged.setdefault("bodega", extract_warehouse_name(nested) or nested_key)
                merged["actualizado_en"] = scraped_at
                flat_rows.append(merged)

    return flat_rows


def rows_from_payload(payload: Any, scraped_at: str) -> list[dict[str, Any]]:
    chosen = choose_best_array(payload)
    if not chosen:
        return []
    _, rows = chosen
    return flatten_nested_inventory(rows, scraped_at)


def capture_response(response: Response, captured: list[CapturedResponse]) -> None:
    try:
        if response.status != 200:
            return
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            return
        body = response.json()
        score = inventory_score(body)
        if score == 0:
            return
        captured.append(
            CapturedResponse(
                url=response.url,
                status=response.status,
                content_type=content_type,
                body=body,
                score=score,
            )
        )
    except Exception:
        return


def wait_for_dashboard(page: Page) -> None:
    page.wait_for_load_state("networkidle", timeout=60_000)
    page.wait_for_timeout(3000)


def discover_api(captured: list[CapturedResponse]) -> None:
    ensure_output_dir()
    captured.sort(key=lambda item: item.score, reverse=True)

    summary = [
        {
            "url": item.url,
            "status": item.status,
            "score": item.score,
            "content_type": item.content_type,
        }
        for item in captured
    ]

    summary_path = OUTPUT_DIR / "api-endpoints.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    if captured:
        sample_path = OUTPUT_DIR / "api-sample.json"
        sample_path.write_text(
            json.dumps(captured[0].body, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Endpoints candidatos: {len(captured)}")
        print(f"Mejor endpoint: {captured[0].url} (score={captured[0].score})")
        print(f"Guardado: {summary_path}")
        print(f"Guardado: {sample_path}")
    else:
        print("No se detectaron endpoints JSON con datos de inventario.")


def fetch_paginated_api(page: Page, start_url: str, scraped_at: str) -> list[dict[str, Any]]:
    visited: set[str] = set()
    queue = [start_url]
    all_rows: list[dict[str, Any]] = []

    while queue:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        response = page.request.get(url)
        if not response.ok:
            continue

        try:
            payload = response.json()
        except Exception:
            continue

        rows = rows_from_payload(payload, scraped_at)
        all_rows.extend(rows)

        next_url = None
        if isinstance(payload, dict):
            for key in ("next", "next_page", "nextPage", "next_url", "nextUrl"):
                candidate = payload.get(key)
                if isinstance(candidate, str) and candidate:
                    next_url = urljoin(url, candidate)
                    break

            links = payload.get("links")
            if isinstance(links, dict):
                candidate = links.get("next")
                if isinstance(candidate, str) and candidate:
                    next_url = urljoin(url, candidate)

            pagination = payload.get("pagination")
            if isinstance(pagination, dict):
                candidate = pagination.get("next")
                if isinstance(candidate, str) and candidate:
                    next_url = urljoin(url, candidate)

            meta = payload.get("meta")
            if isinstance(meta, dict):
                candidate = meta.get("next")
                if isinstance(candidate, str) and candidate:
                    next_url = urljoin(url, candidate)

        if next_url and next_url not in visited:
            queue.append(next_url)
            continue

        parsed = urlparse(url)
        query_pairs = dict(param.split("=", 1) for param in parsed.query.split("&") if "=" in param)
        for page_key in ("page", "offset"):
            if page_key in query_pairs and page_key not in {f"done_{page_key}" for _ in visited}:
                try:
                    current = int(query_pairs[page_key])
                except ValueError:
                    break
                query_pairs[page_key] = str(current + 1)
                new_query = "&".join(f"{k}={v}" for k, v in query_pairs.items())
                candidate = parsed._replace(query=new_query).geturl()
                if candidate not in visited:
                    queue.append(candidate)
                break

    return all_rows


def extract_from_api(page: Page, captured: list[CapturedResponse], scraped_at: str) -> ScrapeResult:
    if not captured:
        return ScrapeResult(source="api")

    captured.sort(key=lambda item: item.score, reverse=True)
    best = captured[0]
    rows = fetch_paginated_api(page, best.url, scraped_at)

    if not rows:
        rows = rows_from_payload(best.body, scraped_at)

    warehouses = {row.get("bodega", "") or row.get("warehouse", "") for row in rows}
    warehouses.discard("")
    return ScrapeResult(rows=rows, warehouses=warehouses, source=f"api:{best.url}")


def scrape_table_current_page(page: Page, warehouse: str, scraped_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    table = page.locator("table").first
    if table.count() == 0:
        grid = page.locator('[role="grid"]').first
        if grid.count() == 0:
            return rows
        headers = [
            normalize_key(text)
            for text in grid.locator('[role="columnheader"]').all_inner_texts()
        ]
        body_rows = grid.locator('[role="row"]')
        for idx in range(body_rows.count()):
            row = body_rows.nth(idx)
            if row.locator('[role="columnheader"]').count() > 0:
                continue
            cells = [text.strip() for text in row.locator('[role="gridcell"], [role="cell"]').all_inner_texts()]
            if not cells:
                continue
            record = {headers[i] if i < len(headers) else f"col_{i}": cells[i] for i in range(len(cells))}
            record["bodega"] = warehouse
            record["actualizado_en"] = scraped_at
            rows.append(record)
        return rows

    headers = [normalize_key(text) for text in table.locator("thead th, thead td").all_inner_texts()]
    if not headers:
        headers = [normalize_key(text) for text in table.locator("tr").first.locator("th, td").all_inner_texts()]

    body_rows = table.locator("tbody tr")
    for idx in range(body_rows.count()):
        cells = [text.strip() for text in body_rows.nth(idx).locator("td, th").all_inner_texts()]
        if not cells:
            continue
        record = {headers[i] if i < len(headers) else f"col_{i}": cells[i] for i in range(len(cells))}
        record["bodega"] = warehouse
        record["actualizado_en"] = scraped_at
        rows.append(record)

    return rows


def click_if_visible(page: Page, selectors: list[str]) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0 and locator.is_visible() and locator.is_enabled():
                locator.click()
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
    return False


def scrape_dom(page: Page, scraped_at: str) -> ScrapeResult:
    all_rows: list[dict[str, Any]] = []
    warehouses: set[str] = set()

    warehouse_selectors = [
        '[role="tab"]',
        'button[data-warehouse]',
        'select option',
        '[class*="warehouse" i]',
        '[class*="bodega" i]',
    ]

    warehouse_names: list[str] = ["General"]
    for selector in warehouse_selectors:
        texts = []
        locator = page.locator(selector)
        for idx in range(min(locator.count(), 30)):
            text = locator.nth(idx).inner_text().strip()
            if text and text.lower() not in {"todos", "all", "general"}:
                texts.append(text)
        if texts:
            warehouse_names = texts
            break

    for warehouse in warehouse_names:
        if warehouse != "General":
            clicked = False
            for selector in warehouse_selectors:
                option = page.locator(selector).filter(has_text=warehouse).first
                try:
                    if option.count() > 0 and option.is_visible():
                        option.click()
                        page.wait_for_timeout(1500)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                continue

        warehouses.add(warehouse)
        seen_signatures: set[str] = set()

        while True:
            page_rows = scrape_table_current_page(page, warehouse, scraped_at)
            if not page_rows:
                break

            signature = json.dumps(page_rows, ensure_ascii=False)
            if signature in seen_signatures:
                break
            seen_signatures.add(signature)
            all_rows.extend(page_rows)

            moved = click_if_visible(
                page,
                [
                    'button:has-text("Siguiente")',
                    'button:has-text("Next")',
                    '[aria-label*="next" i]',
                    '[aria-label*="siguiente" i]',
                    'button[rel="next"]',
                ],
            )
            if not moved:
                break

    return ScrapeResult(rows=all_rows, warehouses=warehouses, source="dom")


def format_condition(state: str) -> str:
    mapping = {"NUEVO": "Nuevo", "USADO": "Usado"}
    return mapping.get((state or "").upper(), state or "")


def get_field(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
        target = normalize_key(key)
        for raw_key, value in record.items():
            if normalize_key(raw_key) == target and value not in (None, ""):
                return value
    return ""


def normalize_quantity(value: Any) -> int | str:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        return int(float(text))
    except ValueError:
        return text


def normalize_export_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        rows.append(
            {
                "SKU": str(get_field(item, "sku", "codigo", "code") or ""),
                "Nombre del Producto": str(
                    get_field(item, "name", "nombre", "producto", "descripcion") or ""
                ),
                "Marca": str(get_field(item, "brand", "marca") or ""),
                "Cantidad": normalize_quantity(
                    get_field(item, "total", "cantidad", "quantity", "stock")
                ),
                "Condicion": format_condition(str(get_field(item, "state", "condicion", "estado") or "")),
            }
        )
    return rows


def extract_pivot_items(page: Page, config: Config) -> list[dict[str, Any]]:
    pivot_url = f"{config.base_url}{PIVOT_API_PATH}"
    response = page.request.get(pivot_url)
    if not response.ok:
        return []

    try:
        payload = response.json()
    except Exception:
        return []

    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def export_csv(rows: list[dict[str, Any]], output_path: Path | None = None) -> Path:
    ensure_output_dir()
    target = output_path or OUTPUT_PATH
    if not rows:
        pd.DataFrame(columns=EXPORT_COLUMNS).to_csv(target, index=False, encoding="utf-8-sig")
        return target

    df = pd.DataFrame(rows, columns=EXPORT_COLUMNS)
    df.to_csv(target, index=False, encoding="utf-8-sig")
    return target


def scrape_inventory_playwright(
    config: Config,
    on_progress: Callable[[str], None] | None = None,
) -> ScrapeResult:
    def report(message: str) -> None:
        print(message)
        if on_progress:
            on_progress(message)

    ensure_output_dir()
    scraped_at = datetime.now(timezone.utc).isoformat()
    captured: list[CapturedResponse] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not config.headed)
        context = browser.new_context()
        page = context.new_page()
        page.on("response", lambda response: capture_response(response, captured))

        try:
            report("Iniciando sesion...")
            login(page, config)
            wait_for_dashboard(page)

            if config.discover:
                discover_api(captured)
                return ScrapeResult(source="discover")

            report("Extrayendo inventario...")
            pivot_items = extract_pivot_items(page, config)
            if pivot_items:
                warehouses = set()
                for item in pivot_items:
                    warehouse_qty = item.get("warehouseQty") or item.get("warehouseqty")
                    if isinstance(warehouse_qty, dict):
                        warehouses.update(warehouse_qty.keys())
                report(f"Datos extraidos via API pivot ({len(pivot_items)} productos).")
                return ScrapeResult(
                    rows=pivot_items,
                    warehouses=warehouses,
                    source=f"api:{config.base_url}{PIVOT_API_PATH}",
                )

            api_result = extract_from_api(page, captured, scraped_at)
            if api_result.rows:
                report(f"Datos extraidos via API ({len(api_result.rows)} filas).")
                return api_result

            report("API no utilizable, usando fallback DOM...")
            dom_result = scrape_dom(page, scraped_at)
            if dom_result.rows:
                report(f"Datos extraidos via DOM ({len(dom_result.rows)} filas).")
                return dom_result

            if captured:
                discover_api(captured)
            save_screenshot(page, "error-no-data.png")
            raise RuntimeError(
                "No se pudieron extraer datos de inventario. "
                "Ejecuta con --discover para inspeccionar endpoints."
            )
        finally:
            browser.close()


def supabase_cookie_name(supabase_url: str) -> str:
    host = supabase_url.replace("https://", "").replace("http://", "").split(".")[0]
    return f"sb-{host}-auth-token"


def supabase_auth_session(config: Config) -> requests.Session:
    load_dotenv()
    supabase_url = os.getenv("SUPABASE_URL", DEFAULT_SUPABASE_URL).rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", DEFAULT_SUPABASE_ANON_KEY)
    if not config.email or not config.password:
        raise RuntimeError("Faltan SOLCOM_EMAIL o SOLCOM_PASSWORD en el entorno.")

    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        headers=headers,
        json={"email": config.email, "password": config.password},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Login Supabase fallido: {response.status_code} {response.text[:200]}")

    tokens = response.json()
    session_obj = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": tokens.get("expires_in"),
        "expires_at": tokens.get("expires_at"),
        "token_type": tokens.get("token_type", "bearer"),
        "user": tokens.get("user"),
    }

    session = requests.Session()
    domain = config.base_url.replace("https://", "").replace("http://", "").split("/")[0]
    session.cookies.set(
        supabase_cookie_name(supabase_url),
        json.dumps(session_obj),
        domain=domain,
        path="/",
    )
    session.headers.update({"Accept": "application/json", "User-Agent": "SolcomScraper/1.0"})
    return session


def fetch_pivot_items_api(session: requests.Session, config: Config) -> list[dict[str, Any]]:
    pivot_url = f"{config.base_url}{PIVOT_API_PATH}"
    response = session.get(pivot_url, timeout=60)
    if not response.ok:
        raise RuntimeError(
            f"No se pudo obtener inventario ({response.status_code}): {response.text[:200]}"
        )
    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("Respuesta de inventario sin items.")
    return [item for item in items if isinstance(item, dict)]


def scrape_inventory_api(
    config: Config,
    on_progress: Callable[[str], None] | None = None,
) -> ScrapeResult:
    def report(message: str) -> None:
        print(message)
        if on_progress:
            on_progress(message)

    report("Iniciando sesion via API...")
    session = supabase_auth_session(config)
    report("Extrayendo inventario...")
    pivot_items = fetch_pivot_items_api(session, config)
    warehouses: set[str] = set()
    for item in pivot_items:
        warehouse_qty = item.get("warehouseQty") or item.get("warehouseqty")
        if isinstance(warehouse_qty, dict):
            warehouses.update(warehouse_qty.keys())
    report(f"Datos extraidos via API pivot ({len(pivot_items)} productos).")
    return ScrapeResult(
        rows=pivot_items,
        warehouses=warehouses,
        source=f"api:{config.base_url}{PIVOT_API_PATH}",
    )


def run_scrape(
    on_progress: Callable[[str], None] | None = None,
    headed: bool = False,
    output_path: Path | None = None,
) -> dict[str, Any]:
    load_dotenv()
    use_playwright = os.getenv("USE_PLAYWRIGHT", "false").lower() == "true"
    config = load_config(headed=headed)

    if use_playwright:
        result = scrape_inventory_playwright(config, on_progress=on_progress)
    else:
        result = scrape_inventory_api(config, on_progress=on_progress)

    report = on_progress or (lambda _msg: None)
    report("Normalizando datos...")
    export_rows = normalize_export_rows(result.rows)
    csv_path = export_csv(export_rows, output_path=output_path or OUTPUT_PATH)
    return {
        "rows": len(export_rows),
        "output_path": str(csv_path),
        "source": result.source,
        "warehouses": sorted(result.warehouses),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scraper de inventario Solcom ERP")
    parser.add_argument("--headed", action="store_true", help="Mostrar navegador")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Solo descubrir endpoints JSON del inventario",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args)

    print("Solcom ERP - Scraper de inventario")
    if config.discover:
        scrape_inventory_playwright(config)
        return

    result = run_scrape(headed=config.headed)
    print(f"Filas extraidas: {result['rows']}")
    if result.get("warehouses"):
        print("Bodegas:", ", ".join(result["warehouses"]))
    print(f"Fuente: {result['source']}")
    print(f"CSV generado: {result['output_path']}")


if __name__ == "__main__":
    main()
