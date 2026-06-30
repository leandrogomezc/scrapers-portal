"""Unified Flask portal for Beauty Depot, Molvu, Biotech and Solís Comercial scrapers."""

import logging
import os
import secrets
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, make_response, request, send_file, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge

from beautydepot_update import (
    find_master_path as find_beauty_master_path,
    find_update_path as find_beauty_update_path,
    generate_update as generate_beauty_update,
    get_update_status as get_beauty_update_status,
    save_master_upload as save_beauty_master_upload,
    validate_beautydepot_master_columns,
)
from master_file_io import MasterFileError, read_master_rows
from solcom_update import (
    find_master_path as find_solcom_master_path,
    find_update_path as find_solcom_update_path,
    generate_update as generate_solcom_update,
    get_update_status as get_solcom_update_status,
    save_master_upload as save_solcom_master_upload,
    validate_solcom_master_columns,
)
from moderna_update import (
    find_base_path as find_moderna_base_path,
    find_master_path as find_moderna_master_path,
    find_update_path as find_moderna_update_path,
    generate_update as generate_moderna_update,
    get_update_status as get_moderna_update_status,
    save_base_upload as save_moderna_base_upload,
    save_master_upload as save_moderna_master_upload,
    validate_moderna_base_columns,
    validate_moderna_master_columns,
)
from scrape_beautydepot import OUTPUT_PATH as BEAUTY_OUTPUT
from scrape_beautydepot import run_scrape as beauty_run_scrape
from scrape_laincreibleabm import OUTPUT_PATH as LAINCREIBLE_OUTPUT
from scrape_laincreibleabm import run_scrape as laincreible_run_scrape
from scrape_inventory import OUTPUT_PATH as SOLCOM_OUTPUT
from scrape_inventory import run_scrape as solcom_run_scrape
from scrape_biotech import OUTPUT_PATH as BIOTECH_OUTPUT
from scrape_biotech import run_scrape as biotech_run_scrape
from scrape_molvu import OUTPUT_PATH as MOLVU_OUTPUT
from scrape_molvu import run_scrape as molvu_run_scrape
from scrape_tecnobodega import OUTPUT_PATH as TECNOBODEGA_OUTPUT
from scrape_tecnobodega import run_scrape as tecnobodega_run_scrape

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024

SCRAPE_SECRET = os.environ.get("SCRAPE_SECRET", "")
IS_PRODUCTION = bool(os.environ.get("RENDER"))
AUTH_REQUIRED = bool(SCRAPE_SECRET) or IS_PRODUCTION

RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW_SECONDS = 300
_rate_limit: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()

if IS_PRODUCTION and not SCRAPE_SECRET:
    logging.warning(
        "SCRAPE_SECRET no está configurado en producción. "
        "Todas las rutas protegidas rechazarán solicitudes hasta configurarlo."
    )

_job_lock = threading.Lock()
_idle_job = {
    "status": "idle",
    "message": "",
    "rows": None,
    "started_at": None,
    "finished_at": None,
    "output_path": None,
}
_jobs: dict[str, dict] = {
    "beautydepot": dict(_idle_job),
    "biotech": dict(_idle_job),
    "laincreible": dict(_idle_job),
    "molvu": dict(_idle_job),
    "solcom": dict(_idle_job),
    "tecnobodega": dict(_idle_job),
}

SCRAPERS: dict[str, dict] = {
    "beautydepot": {
        "label": "Beauty Depot",
        "run": beauty_run_scrape,
        "output_path": BEAUTY_OUTPUT,
        "download_name": "beautydepot_productos.csv",
        "start_message": "Iniciando scrape de Beauty Depot...",
    },
    "biotech": {
        "label": "Pinturas Biotech",
        "run": biotech_run_scrape,
        "output_path": BIOTECH_OUTPUT,
        "download_name": "biotech_productos.csv",
        "start_message": "Iniciando scrape de Pinturas Biotech...",
    },
    "laincreible": {
        "label": "La Increíble ABM",
        "run": laincreible_run_scrape,
        "output_path": LAINCREIBLE_OUTPUT,
        "download_name": "laincreibleabm_productos.csv",
        "start_message": "Iniciando scrape de La Increíble ABM...",
    },
    "molvu": {
        "label": "Molvu",
        "run": molvu_run_scrape,
        "output_path": MOLVU_OUTPUT,
        "download_name": "molvu_productos.csv",
        "start_message": "Iniciando scrape de Molvu...",
    },
    "solcom": {
        "label": "Solís Comercial",
        "run": solcom_run_scrape,
        "output_path": SOLCOM_OUTPUT,
        "download_name": "inventario.csv",
        "start_message": "Iniciando extracción de inventario...",
    },
    "tecnobodega": {
        "label": "TecnoBodega",
        "run": tecnobodega_run_scrape,
        "output_path": TECNOBODEGA_OUTPUT,
        "download_name": "tecnobodega_productos.csv",
        "start_message": "Iniciando scrape de TecnoBodega...",
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_scraper(source: str) -> dict | None:
    return SCRAPERS.get(source)


def _update_job(source: str, **kwargs) -> None:
    with _job_lock:
        _jobs[source].update(kwargs)


def _get_job(source: str) -> dict:
    with _job_lock:
        job = dict(_jobs[source])

    scraper = _get_scraper(source)
    if scraper and job["status"] != "running" and not job.get("output_path"):
        output_path: Path = scraper["output_path"]
        if output_path.exists():
            job["output_path"] = str(output_path)

    return job


def _extract_token() -> str:
    return (request.headers.get("X-Scrape-Token") or "").strip()


def _is_authorized() -> bool:
    if not SCRAPE_SECRET:
        return not IS_PRODUCTION
    return secrets.compare_digest(_extract_token(), SCRAPE_SECRET)


def _auth_error_response():
    return jsonify({"error": "Token inválido o faltante"}), 401


def _require_auth():
    if not _is_authorized():
        return _auth_error_response()
    return None


def _check_rate_limit() -> bool:
    ip = request.remote_addr or "unknown"
    now = time.time()
    with _rate_limit_lock:
        timestamps = [t for t in _rate_limit.get(ip, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(timestamps) >= RATE_LIMIT_MAX:
            return False
        timestamps.append(now)
        _rate_limit[ip] = timestamps
    return True


@app.after_request
def _set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.errorhandler(RequestEntityTooLarge)
def _handle_file_too_large(_exc):
    return jsonify({"error": "El archivo supera el límite de 15 MB."}), 413


def _run_job(source: str) -> None:
    scraper = SCRAPERS[source]
    run_fn: Callable = scraper["run"]
    output_path: Path = scraper["output_path"]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def on_progress(message: str) -> None:
        _update_job(source, message=message)

    try:
        result = run_fn(on_progress=on_progress, output_path=output_path)
        _update_job(
            source,
            status="done",
            message=f"Completado: {result['rows']} productos exportados",
            rows=result["rows"],
            finished_at=_utc_now(),
            output_path=result["output_path"],
        )
    except Exception:
        app.logger.exception("Scrape job failed for source=%s", source)
        _update_job(
            source,
            status="error",
            message="Error interno al ejecutar el scrape.",
            finished_at=_utc_now(),
        )


@app.get("/")
def index():
    response = make_response(send_from_directory(app.static_folder, "index.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/robots.txt")
def robots_txt():
    return make_response("User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"})


@app.get("/api/auth/required")
def auth_required():
    return jsonify(
        {
            "required": AUTH_REQUIRED,
            "misconfigured": IS_PRODUCTION and not SCRAPE_SECRET,
        }
    )


@app.post("/api/<source>/run")
def api_run(source: str):
    scraper = _get_scraper(source)
    if not scraper:
        return jsonify({"error": "Fuente desconocida"}), 404

    auth_error = _require_auth()
    if auth_error:
        return auth_error

    if not _check_rate_limit():
        return jsonify({"error": "Demasiadas solicitudes. Espera unos minutos e intenta de nuevo."}), 429

    with _job_lock:
        if _jobs[source]["status"] == "running":
            return jsonify({"error": f"Ya hay un scrape de {scraper['label']} en ejecución"}), 409

        _jobs[source].update(
            {
                "status": "running",
                "message": scraper["start_message"],
                "rows": None,
                "started_at": _utc_now(),
                "finished_at": None,
                "output_path": None,
            }
        )

    thread = threading.Thread(target=_run_job, args=(source,), daemon=True)
    thread.start()
    return jsonify({"status": "running", "source": source}), 202


@app.get("/api/<source>/status")
def api_status(source: str):
    if not _get_scraper(source):
        return jsonify({"error": "Fuente desconocida"}), 404
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    return jsonify(_get_job(source))


def _upload_master_file(upload, validate_columns, save_upload):
    if not upload or not upload.filename:
        return None, (jsonify({"error": "Debes subir un archivo maestro (.xlsx o .csv)."}), 400)

    filename = upload.filename.lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".csv")):
        return None, (jsonify({"error": "El archivo debe ser .xlsx o .csv."}), 400)

    suffix = ".xlsx" if filename.endswith(".xlsx") else ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        upload.save(tmp.name)
        temp_path = Path(tmp.name)

    try:
        fieldnames, rows = read_master_rows(temp_path)
        validate_columns(fieldnames)
        master_path = save_upload(temp_path, upload.filename)
    except MasterFileError as exc:
        temp_path.unlink(missing_ok=True)
        return None, (jsonify({"error": str(exc)}), 400)
    except (OSError, ValueError) as exc:
        temp_path.unlink(missing_ok=True)
        return None, (jsonify({"error": f"Archivo inválido: {exc}"}), 400)

    return (
        {
            "status": "ok",
            "master_rows": len(rows),
            "columns": fieldnames,
            "format": master_path.suffix.lstrip(".").lower(),
        },
        None,
    )


def _send_update_file(update_path: Path | None, download_basename: str):
    if not update_path:
        return jsonify({"error": "No hay archivo de actualización. Genera uno primero."}), 404

    if update_path.suffix.lower() == ".xlsx":
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        mimetype = "text/csv"

    return send_file(
        update_path,
        as_attachment=True,
        download_name=f"{download_basename}{update_path.suffix}",
        mimetype=mimetype,
    )


@app.get("/api/beautydepot/update-status")
def beautydepot_update_status():
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    return jsonify(get_beauty_update_status())


@app.post("/api/beautydepot/upload-master")
def beautydepot_upload_master():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload, error = _upload_master_file(
        request.files.get("file"),
        validate_beautydepot_master_columns,
        save_beauty_master_upload,
    )
    if error:
        return error
    return jsonify(payload)


@app.post("/api/beautydepot/generate-update")
def beautydepot_generate_update():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    if not find_beauty_master_path():
        return jsonify({"error": "Sube primero el archivo maestro."}), 400

    if not BEAUTY_OUTPUT.exists():
        return jsonify({"error": "Ejecuta primero el scrape de Beauty Depot."}), 400

    try:
        result = generate_beauty_update()
    except MasterFileError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"status": "ok", **result})


@app.get("/download/beautydepot/update-csv")
def download_beautydepot_update_csv():
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    return _send_update_file(find_beauty_update_path(), "beautydepot_actualizacion")


@app.get("/api/solcom/update-status")
def solcom_update_status():
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    return jsonify(get_solcom_update_status())


@app.post("/api/solcom/upload-master")
def solcom_upload_master():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload, error = _upload_master_file(
        request.files.get("file"),
        validate_solcom_master_columns,
        save_solcom_master_upload,
    )
    if error:
        return error
    return jsonify(payload)


@app.post("/api/solcom/generate-update")
def solcom_generate_update():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    if not find_solcom_master_path():
        return jsonify({"error": "Sube primero el archivo maestro."}), 400

    if not SOLCOM_OUTPUT.exists():
        return jsonify({"error": "Ejecuta primero el scrape de Solís Comercial."}), 400

    prices_text = ""
    if request.is_json and request.json:
        prices_text = request.json.get("prices_text", "") or ""

    try:
        result = generate_solcom_update(prices_text=prices_text)
    except MasterFileError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"status": "ok", **result})


@app.get("/download/solcom/update-csv")
def download_solcom_update_csv():
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    return _send_update_file(find_solcom_update_path(), "solcom_actualizacion")


@app.get("/api/moderna/update-status")
def moderna_update_status():
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    return jsonify(get_moderna_update_status())


@app.post("/api/moderna/upload-base")
def moderna_upload_base():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "Debes subir la Base de Datos (.csv)."}), 400
    if not upload.filename.lower().endswith(".csv"):
        return jsonify({"error": "La Base de Datos debe ser .csv."}), 400

    payload, error = _upload_master_file(
        upload,
        validate_moderna_base_columns,
        save_moderna_base_upload,
    )
    if error:
        return error
    return jsonify(payload)


@app.post("/api/moderna/upload-master")
def moderna_upload_master():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    payload, error = _upload_master_file(
        request.files.get("file"),
        validate_moderna_master_columns,
        save_moderna_master_upload,
    )
    if error:
        return error
    return jsonify(payload)


@app.post("/api/moderna/generate-update")
def moderna_generate_update():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    if not find_moderna_base_path():
        return jsonify({"error": "Sube primero la Base de Datos."}), 400

    if not find_moderna_master_path():
        return jsonify({"error": "Sube primero el Archivo de Actualización."}), 400

    try:
        result = generate_moderna_update()
    except MasterFileError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"status": "ok", **result})


@app.get("/download/moderna/update-csv")
def download_moderna_update_csv():
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    return _send_update_file(find_moderna_update_path(), "moderna_actualizacion")


@app.get("/download/<source>/csv")
def download_csv(source: str):
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    scraper = _get_scraper(source)
    if not scraper:
        return jsonify({"error": "Fuente desconocida"}), 404

    output_path: Path = scraper["output_path"]
    if not output_path.exists():
        return jsonify({"error": "No hay CSV disponible. Ejecuta el scrape primero."}), 404

    return send_file(
        output_path,
        as_attachment=True,
        download_name=scraper["download_name"],
        mimetype="text/csv",
    )


if __name__ == "__main__":
    import webbrowser

    port = int(os.environ.get("PORT", 5050))
    url = f"http://127.0.0.1:{port}"
    print("=" * 50)
    print("  Portal de Scrapers")
    print("  Beauty Depot + Biotech + La Increíble ABM + Molvu + Solís Comercial + TecnoBodega")
    print(f"  Abre en tu navegador: {url}")
    print("  Presiona Ctrl+C para detener el servidor")
    print("=" * 50)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
