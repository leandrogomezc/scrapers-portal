"""Unified Flask portal for Beauty Depot, Molvu, Biotech and Solís Comercial scrapers."""

import os
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, make_response, request, send_file, send_from_directory

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
from scrape_inventory import OUTPUT_PATH as SOLCOM_OUTPUT
from scrape_inventory import run_scrape as solcom_run_scrape
from scrape_biotech import OUTPUT_PATH as BIOTECH_OUTPUT
from scrape_biotech import run_scrape as biotech_run_scrape
from scrape_molvu import OUTPUT_PATH as MOLVU_OUTPUT
from scrape_molvu import run_scrape as molvu_run_scrape

app = Flask(__name__, static_folder="static", static_url_path="/static")

SCRAPE_SECRET = os.environ.get("SCRAPE_SECRET", "")

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
    "molvu": dict(_idle_job),
    "solcom": dict(_idle_job),
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
        return dict(_jobs[source])


def _is_authorized() -> bool:
    if not SCRAPE_SECRET:
        return True
    token = request.headers.get("X-Scrape-Token")
    if not token and request.is_json and request.json:
        token = request.json.get("token")
    if not token:
        token = request.form.get("token")
    return token == SCRAPE_SECRET


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
    except Exception as exc:
        _update_job(
            source,
            status="error",
            message=str(exc),
            finished_at=_utc_now(),
        )


@app.get("/")
def index():
    response = make_response(send_from_directory(app.static_folder, "index.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.post("/api/<source>/run")
def api_run(source: str):
    scraper = _get_scraper(source)
    if not scraper:
        return jsonify({"error": "Fuente desconocida"}), 404

    if not _is_authorized():
        return jsonify({"error": "Token inválido o faltante"}), 401

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
    return jsonify(get_beauty_update_status())


@app.post("/api/beautydepot/upload-master")
def beautydepot_upload_master():
    if not _is_authorized():
        return jsonify({"error": "Token inválido o faltante"}), 401

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
    if not _is_authorized():
        return jsonify({"error": "Token inválido o faltante"}), 401

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
    return _send_update_file(find_beauty_update_path(), "beautydepot_actualizacion")


@app.get("/api/solcom/update-status")
def solcom_update_status():
    return jsonify(get_solcom_update_status())


@app.post("/api/solcom/upload-master")
def solcom_upload_master():
    if not _is_authorized():
        return jsonify({"error": "Token inválido o faltante"}), 401

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
    if not _is_authorized():
        return jsonify({"error": "Token inválido o faltante"}), 401

    if not find_solcom_master_path():
        return jsonify({"error": "Sube primero el archivo maestro."}), 400

    if not SOLCOM_OUTPUT.exists():
        return jsonify({"error": "Ejecuta primero el scrape de Solís Comercial."}), 400

    try:
        result = generate_solcom_update()
    except MasterFileError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"status": "ok", **result})


@app.get("/download/solcom/update-csv")
def download_solcom_update_csv():
    return _send_update_file(find_solcom_update_path(), "solcom_actualizacion")


@app.get("/api/moderna/update-status")
def moderna_update_status():
    return jsonify(get_moderna_update_status())


@app.post("/api/moderna/upload-base")
def moderna_upload_base():
    if not _is_authorized():
        return jsonify({"error": "Token inválido o faltante"}), 401

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
    if not _is_authorized():
        return jsonify({"error": "Token inválido o faltante"}), 401

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
    if not _is_authorized():
        return jsonify({"error": "Token inválido o faltante"}), 401

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
    return _send_update_file(find_moderna_update_path(), "moderna_actualizacion")


@app.get("/download/<source>/csv")
def download_csv(source: str):
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
    print("  Beauty Depot + Biotech + Molvu + Solís Comercial")
    print(f"  Abre en tu navegador: {url}")
    print("  Presiona Ctrl+C para detener el servidor")
    print("=" * 50)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
