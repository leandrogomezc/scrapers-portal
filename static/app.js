const TOKEN_KEY = "portal_scrape_token";
const ACCESS_DENIED_MSG = "Acceso denegado. Verifica tu token de acceso.";

const tokenInput = document.getElementById("token");
const tokenLabel = document.getElementById("token-label");
const clearTokenBtn = document.getElementById("clear-token-btn");
const securityBanner = document.getElementById("security-banner");
const portalRoot = document.getElementById("portal-root");
const pollTimers = {};
const scrapePanel = document.getElementById("tab-scrape");

let authRequired = false;

const UPDATE_SOURCES = {
  beautydepot: {
    statusUrl: "/api/beautydepot/update-status",
    uploadUrl: "/api/beautydepot/upload-master",
    generateUrl: "/api/beautydepot/generate-update",
    downloadUrl: "/download/beautydepot/update-csv",
    downloadName: "beautydepot_actualizacion",
    masterFileInput: document.getElementById("master-file"),
    masterStatusEl: document.getElementById("master-status"),
    scrapeBadgeEl: document.getElementById("scrape-badge-beautydepot"),
    card: document.querySelector('[data-update-source="beautydepot"]'),
    requiresScrape: true,
    formatDoneMessage: (data) =>
      `${data.total} SKUs en maestro · ${data.stock_10} con stock 10 · ${data.stock_0} con stock 0`,
  },
  solcom: {
    statusUrl: "/api/solcom/update-status",
    uploadUrl: "/api/solcom/upload-master",
    generateUrl: "/api/solcom/generate-update",
    downloadUrl: "/download/solcom/update-csv",
    downloadName: "solcom_actualizacion",
    masterFileInput: document.getElementById("solcom-master-file"),
    masterStatusEl: document.getElementById("solcom-master-status"),
    pricesTextInput: document.getElementById("solcom-prices-text"),
    scrapeBadgeEl: document.getElementById("scrape-badge-solcom"),
    card: document.querySelector('[data-update-source="solcom"]'),
    requiresScrape: true,
    formatDoneMessage: (data) => {
      let msg = `${data.total} SKUs · ${data.with_stock} con stock · ${data.zero_stock} en 0`;
      if (data.name_matched) {
        msg += ` · ${data.name_matched} por nombre`;
      }
      if (data.skus_written) {
        msg += ` · ${data.skus_written} SKU escritos`;
      }
      if ((data.prices_matched || 0) + (data.prices_unmatched || 0) > 0) {
        msg += ` · ${data.prices_matched} costos aplicados · ${data.prices_unmatched} ignorados`;
        if (data.sale_prices_adjusted) {
          msg += ` · ${data.sale_prices_adjusted} precios ajustados al margen mínimo`;
        }
      }
      return msg;
    },
  },
  moderna: {
    statusUrl: "/api/moderna/update-status",
    baseUploadUrl: "/api/moderna/upload-base",
    uploadUrl: "/api/moderna/upload-master",
    generateUrl: "/api/moderna/generate-update",
    downloadUrl: "/download/moderna/update-csv",
    downloadName: "moderna_actualizacion",
    baseFileInput: document.getElementById("moderna-base-file"),
    baseStatusEl: document.getElementById("moderna-base-status"),
    masterFileInput: document.getElementById("moderna-master-file"),
    masterStatusEl: document.getElementById("moderna-master-status"),
    scrapeBadgeEl: document.getElementById("moderna-base-badge"),
    card: document.querySelector('[data-update-source="moderna"]'),
    requiresScrape: false,
    formatDoneMessage: (data) =>
      `${data.total} SKUs · ${data.matched} en base · ${data.unmatched} sin existir (MODERNA=0) · ${data.with_stock} con stock`,
  },
};

const updateStates = {
  beautydepot: { masterUploaded: false, scrapeAvailable: false, updateAvailable: false },
  solcom: { masterUploaded: false, scrapeAvailable: false, updateAvailable: false },
  moderna: { baseUploaded: false, masterUploaded: false, scrapeAvailable: false, updateAvailable: false },
};

const savedToken = sessionStorage.getItem(TOKEN_KEY);
if (savedToken) {
  tokenInput.value = savedToken;
}

function getToken() {
  return tokenInput.value.trim();
}

function persistToken() {
  const token = getToken();
  if (token) {
    sessionStorage.setItem(TOKEN_KEY, token);
  } else {
    sessionStorage.removeItem(TOKEN_KEY);
  }
  updateAuthUI();
}

function clearSession() {
  tokenInput.value = "";
  sessionStorage.removeItem(TOKEN_KEY);
  updateAuthUI();
}

function authHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getToken();
  if (token) {
    headers["X-Scrape-Token"] = token;
  }
  return headers;
}

function canUsePortal() {
  if (!authRequired) {
    return true;
  }
  return Boolean(getToken());
}

function updateAuthUI() {
  const blocked = authRequired && !canUsePortal();

  portalRoot.classList.toggle("portal-blocked", blocked);
  securityBanner.classList.toggle("hidden", !authRequired);
  securityBanner.classList.toggle(
    "security-banner--misconfigured",
    securityBanner.dataset.misconfigured === "true"
  );

  if (authRequired) {
    tokenLabel.textContent = "Token de acceso requerido";
    tokenInput.placeholder = "Ingresa tu token de acceso";
  } else {
    tokenLabel.textContent = "Token de acceso (opcional)";
    tokenInput.placeholder = "Solo si SCRAPE_SECRET está configurado";
  }

  document.querySelectorAll(".run-btn, .download-btn, .update-download-btn").forEach((btn) => {
    btn.disabled = blocked;
  });
  document.querySelectorAll(".file-input, .prices-textarea").forEach((el) => {
    el.disabled = blocked;
  });

  if (blocked) {
    document.querySelectorAll(".generate-update-btn").forEach((btn) => {
      btn.disabled = true;
    });
    return;
  }

  Object.keys(UPDATE_SOURCES).forEach(refreshGenerateButton);
  refreshAllUpdateStatus();
  scrapePanel.querySelectorAll(".card[data-source]").forEach((card) => {
    pollStatus(card.dataset.source, card);
  });
}

async function initAuth() {
  try {
    const res = await fetch("/api/auth/required");
    const data = await res.json();
    authRequired = Boolean(data.required);
    if (data.misconfigured) {
      securityBanner.textContent =
        "El portal está en producción sin SCRAPE_SECRET configurado. Contacta al administrador.";
      securityBanner.dataset.misconfigured = "true";
    } else if (authRequired) {
      securityBanner.textContent =
        "Este portal requiere token de acceso. Ingresa tu clave para usar scrapes, subidas y descargas.";
    }
    updateAuthUI();
  } catch {
    securityBanner.textContent = "No se pudo verificar la configuración de acceso.";
    securityBanner.classList.remove("hidden");
  }
}

function switchTab(tabId) {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    const isActive = btn.dataset.tab === tabId;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tabId}`);
  });
  if (tabId === "actualizacion" && canUsePortal()) {
    refreshAllUpdateStatus();
  }
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

document.querySelectorAll(".scrape-badge .tab-link").forEach((link) => {
  link.addEventListener("click", () => switchTab("scrape"));
});

tokenInput.addEventListener("blur", persistToken);
tokenInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    persistToken();
    tokenInput.blur();
  }
});
clearTokenBtn.addEventListener("click", clearSession);

function setScrapeBadgeMissing(badge) {
  badge.textContent = "";
  badge.append("Falta ejecutar scrape. ");
  const link = document.createElement("button");
  link.type = "button";
  link.className = "tab-link";
  link.textContent = "Ir a Scrape";
  link.addEventListener("click", () => switchTab("scrape"));
  badge.append(link);
}

function updateScrapeBadge(sourceKey, available) {
  const badge = UPDATE_SOURCES[sourceKey].scrapeBadgeEl;
  badge.classList.toggle("available", available);
  badge.classList.toggle("missing", !available);
  if (available) {
    badge.textContent = "Catálogo disponible";
  } else {
    setScrapeBadgeMissing(badge);
  }
}

function updateBaseBadge(sourceKey, available) {
  const badge = UPDATE_SOURCES[sourceKey].scrapeBadgeEl;
  badge.classList.toggle("available", available);
  badge.classList.toggle("missing", !available);
  badge.textContent = available ? "Base de datos cargada" : "Falta subir base de datos.";
}

function refreshGenerateButton(sourceKey) {
  const config = UPDATE_SOURCES[sourceKey];
  const state = updateStates[sourceKey];
  const generateBtn = config.card.querySelector(".generate-update-btn");

  if (!canUsePortal()) {
    generateBtn.disabled = true;
    return;
  }

  if (config.requiresScrape === false) {
    generateBtn.disabled = !(state.baseUploaded && state.masterUploaded);
  } else {
    generateBtn.disabled = !(state.masterUploaded && state.scrapeAvailable);
  }
}

function showUpdateDownload(sourceKey, show) {
  UPDATE_SOURCES[sourceKey].card
    .querySelector(".update-download-btn")
    .classList.toggle("hidden", !show);
}

async function refreshUpdateStatus(sourceKey) {
  if (!canUsePortal()) {
    return;
  }

  const config = UPDATE_SOURCES[sourceKey];
  const state = updateStates[sourceKey];

  try {
    const res = await fetch(config.statusUrl, { headers: authHeaders() });
    if (res.status === 401) {
      updateAuthUI();
      return;
    }
    const data = await res.json();
    state.masterUploaded = Boolean(data.master_uploaded);
    state.scrapeAvailable = Boolean(data.scrape_available);
    state.updateAvailable = Boolean(data.update_available);

    if (config.requiresScrape === false) {
      state.baseUploaded = Boolean(data.base_uploaded);
      if (data.base_uploaded && data.base_rows != null) {
        config.baseStatusEl.textContent = `Base de datos (CSV) cargada: ${data.base_rows} filas.`;
      } else {
        config.baseStatusEl.textContent = "Sin base de datos subida.";
      }
      updateBaseBadge(sourceKey, state.baseUploaded);
    } else {
      updateScrapeBadge(sourceKey, state.scrapeAvailable);
    }

    if (data.master_uploaded && data.master_rows != null) {
      const fmt = data.master_format ? data.master_format.toUpperCase() : "XLSX";
      const label = config.requiresScrape === false ? "Archivo de actualización" : "Archivo maestro";
      config.masterStatusEl.textContent = `${label} (${fmt}) cargado: ${data.master_rows} filas.`;
    } else {
      config.masterStatusEl.textContent =
        config.requiresScrape === false
          ? "Sin archivo de actualización subido."
          : "Sin archivo maestro subido.";
    }

    showUpdateDownload(sourceKey, state.updateAvailable);
    refreshGenerateButton(sourceKey);
  } catch {
    if (config.baseStatusEl) {
      config.baseStatusEl.textContent = "No se pudo consultar el estado.";
    }
    config.masterStatusEl.textContent = "No se pudo consultar el estado del maestro.";
  }
}

function refreshAllUpdateStatus() {
  Object.keys(UPDATE_SOURCES).forEach(refreshUpdateStatus);
}

async function uploadBaseFile(sourceKey, file) {
  if (!canUsePortal()) {
    return;
  }

  const config = UPDATE_SOURCES[sourceKey];
  const state = updateStates[sourceKey];
  const formData = new FormData();
  formData.append("file", file);

  config.baseStatusEl.textContent = "Subiendo base de datos...";

  try {
    const res = await fetch(config.baseUploadUrl, {
      method: "POST",
      headers: authHeaders(),
      body: formData,
    });
    const data = await res.json().catch(() => ({}));

    if (res.status === 401) {
      config.baseStatusEl.textContent = ACCESS_DENIED_MSG;
      updateAuthUI();
      return;
    }

    if (!res.ok) {
      config.baseStatusEl.textContent = data.error || "Error al subir la base de datos.";
      return;
    }

    const fmt = data.format ? data.format.toUpperCase() : "CSV";
    config.baseStatusEl.textContent = `Base de datos (${fmt}) cargada: ${data.master_rows} filas.`;
    state.baseUploaded = true;
    updateBaseBadge(sourceKey, true);
    refreshGenerateButton(sourceKey);
  } catch (err) {
    config.baseStatusEl.textContent = "Error de conexión al subir: " + err.message;
  }
}

async function uploadMasterFile(sourceKey, file) {
  if (!canUsePortal()) {
    return;
  }

  const config = UPDATE_SOURCES[sourceKey];
  const state = updateStates[sourceKey];
  const formData = new FormData();
  formData.append("file", file);

  config.masterStatusEl.textContent = "Subiendo archivo maestro...";

  try {
    const res = await fetch(config.uploadUrl, {
      method: "POST",
      headers: authHeaders(),
      body: formData,
    });
    const data = await res.json().catch(() => ({}));

    if (res.status === 401) {
      config.masterStatusEl.textContent = ACCESS_DENIED_MSG;
      updateAuthUI();
      return;
    }

    if (!res.ok) {
      config.masterStatusEl.textContent = data.error || "Error al subir el archivo maestro.";
      return;
    }

    const fmt = data.format ? data.format.toUpperCase() : "XLSX";
    const label = config.requiresScrape === false ? "Archivo de actualización" : "Archivo maestro";
    config.masterStatusEl.textContent = `${label} (${fmt}) cargado: ${data.master_rows} filas.`;
    state.masterUploaded = true;
    refreshGenerateButton(sourceKey);
  } catch (err) {
    config.masterStatusEl.textContent = "Error de conexión al subir: " + err.message;
  }
}

async function generateUpdate(sourceKey) {
  if (!canUsePortal()) {
    return;
  }

  const config = UPDATE_SOURCES[sourceKey];
  const state = updateStates[sourceKey];
  const generateBtn = config.card.querySelector(".generate-update-btn");

  generateBtn.disabled = true;
  setStatus(config.card, "running", "Generando archivo de actualización...", true);

  const payload = {};
  if (config.pricesTextInput) {
    payload.prices_text = config.pricesTextInput.value || "";
  }

  try {
    const res = await fetch(config.generateUrl, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));

    if (res.status === 401) {
      setStatus(config.card, "error", ACCESS_DENIED_MSG);
      updateAuthUI();
      refreshGenerateButton(sourceKey);
      return;
    }

    if (!res.ok) {
      setStatus(config.card, "error", data.error || "No se pudo generar la actualización.");
      refreshGenerateButton(sourceKey);
      return;
    }

    state.updateAvailable = true;
    showUpdateDownload(sourceKey, true);
    setStatus(config.card, "done", config.formatDoneMessage(data));
  } catch (err) {
    setStatus(config.card, "error", "Error de conexión: " + err.message);
  } finally {
    refreshGenerateButton(sourceKey);
  }
}

Object.entries(UPDATE_SOURCES).forEach(([sourceKey, config]) => {
  if (config.baseFileInput) {
    config.baseFileInput.addEventListener("change", () => {
      const file = config.baseFileInput.files[0];
      if (file) uploadBaseFile(sourceKey, file);
    });
  }
  config.masterFileInput.addEventListener("change", () => {
    const file = config.masterFileInput.files[0];
    if (file) uploadMasterFile(sourceKey, file);
  });
  config.card.querySelector(".generate-update-btn").addEventListener("click", () => {
    generateUpdate(sourceKey);
  });
  config.card.querySelector(".update-download-btn").addEventListener("click", () => {
    downloadFile(config.downloadUrl, config.downloadName);
  });
});

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function getCardElements(card) {
  return {
    runBtn: card.querySelector(".run-btn"),
    downloadBtn: card.querySelector(".download-btn"),
    statusBox: card.querySelector(".status"),
    statusMessage: card.querySelector(".status-message"),
  };
}

function setStatus(card, status, message, showSpinner = false) {
  const statusBox = card.querySelector(".status");
  const statusMessage = card.querySelector(".status-message");
  statusBox.className = "status";
  if (status && status !== "idle") statusBox.classList.add(status);
  statusMessage.innerHTML = showSpinner
    ? '<span class="spinner"></span>' + escapeHtml(message)
    : escapeHtml(message);
}

function setRunning(card, isRunning) {
  const { runBtn } = getCardElements(card);
  runBtn.disabled = isRunning || (authRequired && !canUsePortal());
  tokenInput.disabled = scrapePanel.querySelectorAll(".run-btn:disabled").length > 0 && isRunning;
}

function showDownload(card, show) {
  getCardElements(card).downloadBtn.classList.toggle("hidden", !show);
}

function filenameFromDisposition(header, fallback) {
  if (!header) {
    return fallback;
  }
  const match = header.match(/filename="?([^";]+)"?/i);
  return match ? match[1] : fallback;
}

async function downloadFile(url, fallbackName) {
  if (!canUsePortal()) {
    return;
  }

  try {
    const res = await fetch(url, { headers: authHeaders() });
    if (res.status === 401) {
      updateAuthUI();
      return;
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.error || "No se pudo descargar el archivo.");
      return;
    }

    const blob = await res.blob();
    const filename = filenameFromDisposition(
      res.headers.get("Content-Disposition"),
      fallbackName
    );
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(objectUrl);
  } catch (err) {
    alert("Error de conexión al descargar: " + err.message);
  }
}

async function pollStatus(source, card) {
  if (!canUsePortal()) {
    return;
  }

  try {
    const res = await fetch(`/api/${source}/status`, { headers: authHeaders() });
    if (res.status === 401) {
      updateAuthUI();
      return;
    }
    const data = await res.json();

    if (data.status === "running") {
      setStatus(card, "running", data.message || "En progreso...", true);
      showDownload(card, false);
      setRunning(card, true);
      return;
    }

    clearInterval(pollTimers[source]);
    pollTimers[source] = null;
    setRunning(card, false);

    if (data.status === "done") {
      setStatus(card, "done", data.message || `Completado: ${data.rows} productos`);
      showDownload(card, true);
      if (source === "beautydepot" || source === "solcom") {
        refreshUpdateStatus(source);
      }
    } else if (data.status === "error") {
      setStatus(card, "error", data.message || "Error desconocido");
      showDownload(card, false);
    } else {
      setStatus(card, "idle", "Listo para ejecutar.");
      showDownload(card, data.output_path != null);
      if ((source === "beautydepot" || source === "solcom") && data.output_path != null) {
        refreshUpdateStatus(source);
      }
    }
  } catch (err) {
    clearInterval(pollTimers[source]);
    pollTimers[source] = null;
    setRunning(card, false);
    setStatus(card, "error", "Error al consultar el estado: " + err.message);
  }
}

async function startScrape(source, card) {
  if (!canUsePortal()) {
    setStatus(card, "error", ACCESS_DENIED_MSG);
    return;
  }

  setRunning(card, true);
  showDownload(card, false);
  setStatus(card, "running", "Iniciando...", true);

  try {
    const res = await fetch(`/api/${source}/run`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({}),
    });

    const data = await res.json().catch(() => ({}));

    if (res.status === 401) {
      setRunning(card, false);
      setStatus(card, "error", ACCESS_DENIED_MSG);
      updateAuthUI();
      return;
    }

    if (res.status === 409) {
      setStatus(card, "running", data.error || "Ya hay un scrape en ejecución", true);
    } else if (res.status === 429) {
      setRunning(card, false);
      setStatus(card, "error", data.error || "Demasiadas solicitudes. Espera unos minutos.");
      return;
    } else if (!res.ok) {
      setRunning(card, false);
      setStatus(card, "error", data.error || "No se pudo iniciar el scrape");
      return;
    }

    if (pollTimers[source]) clearInterval(pollTimers[source]);
    pollTimers[source] = setInterval(() => pollStatus(source, card), 2000);
    pollStatus(source, card);
  } catch (err) {
    setRunning(card, false);
    setStatus(card, "error", "Error de conexión: " + err.message);
  }
}

scrapePanel.querySelectorAll(".card[data-source]").forEach((card) => {
  const source = card.dataset.source;
  card.querySelector(".run-btn").addEventListener("click", () => startScrape(source, card));
  card.querySelector(".download-btn").addEventListener("click", () => {
    downloadFile(`/download/${source}/csv`, card.dataset.downloadName);
  });
});

initAuth();
