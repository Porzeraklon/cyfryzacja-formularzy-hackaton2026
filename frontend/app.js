/* COI Form Digitizer — frontend SPA (Branch 2: feat/frontend-and-visualizer).
 * Vanilla JS, zero zależności, podpięty pod /api/v1/* (działa z mock pipeline).
 * [OWNERSHIP: Branch 2] — cały katalog frontend/ należy do Brancha 2.
 */
"use strict";

const API = "/api/v1";
const MAX_FILENAME_LENGTH = 250;
const FORBIDDEN_FILENAME_CHARS = new Set(["$", ":", ">", "<", "/", "\\", "|", "'", "*", "%", "?", '"']);

const state = {
  config: null,
  token: sessionStorage.getItem("jwt_token") || "",
  selectedFiles: [],
  results: [],
  currentForm: null,
  dictCache: new Map(),
};

const viz = {
  form: null,
  values: {},
  pageIdx: 0,
  flows: [],
  fieldIndex: new Map(),
  sectionIndex: new Map(),
  pageIndex: new Map(),
  refs: new Map(),
  appliedSetValue: new Set(),
  timers: new Map(),
};

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

let toastTimer = null;
function toast(message, kind = "error") {
  const box = $("toast");
  box.textContent = message;
  box.className = `toast ${kind === "info" ? "info" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add("hidden"), 6000);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.json);
  }
  const response = await fetch(`${API}${path}`, { ...options, headers });
  let payload = null;
  try { payload = await response.json(); } catch { /* np. 204 */ }
  if (!response.ok) {
    const err = payload?.error;
    const message = err ? `${err.code}: ${err.message}` : `Błąd HTTP ${response.status}`;
    const error = new Error(message);
    error.code = err?.code;
    error.details = err?.details;
    error.status = response.status;
    throw error;
  }
  return payload;
}

/* ---------------- Walidatory lokalne (lustro backendu, natychmiastowy feedback) ---------------- */

function peselErrors(value) {
  if (!/^\d{11}$/.test(value)) return ["PESEL musi składać się z 11 cyfr."];
  const weights = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3];
  const sum = weights.reduce((acc, w, i) => acc + Number(value[i]) * w, 0);
  const errors = [];
  if ((10 - (sum % 10)) % 10 !== Number(value[10])) errors.push("Nieprawidłowa suma kontrolna PESEL.");
  const month = Number(value.slice(2, 4));
  const century = { 0: 1900, 2: 2000, 4: 2100, 6: 2200, 8: 1800 }[Math.floor(month / 20)];
  if (century === undefined) errors.push("Nieprawidłowy miesiąc w numerze PESEL.");
  else {
    const date = new Date(century + Number(value.slice(0, 2)), (month % 20) - 1, Number(value.slice(4, 6)));
    if (Number.isNaN(date.getTime()) || date.getDate() !== Number(value.slice(4, 6))) {
      errors.push("Nieprawidłowa data urodzenia w numerze PESEL.");
    }
  }
  return errors;
}

function nipErrors(value) {
  const digits = value.replace(/[\s-]/g, "");
  if (!/^\d{10}$/.test(digits)) return ["NIP musi składać się z 10 cyfr."];
  const weights = [6, 5, 7, 2, 3, 4, 5, 6, 7];
  const sum = weights.reduce((acc, w, i) => acc + Number(digits[i]) * w, 0) % 11;
  return sum === 10 || sum !== Number(digits[9]) ? ["Nieprawidłowa suma kontrolna NIP."] : [];
}

function regonErrors(value) {
  const digits = value.replace(/[\s-]/g, "");
  let weights;
  if (/^\d{9}$/.test(digits)) weights = [8, 9, 2, 3, 4, 5, 6, 7];
  else if (/^\d{14}$/.test(digits)) weights = [2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8];
  else return ["REGON musi składać się z 9 lub 14 cyfr."];
  const sum = weights.reduce((acc, w, i) => acc + Number(digits[i]) * w, 0) % 11;
  return sum === 10 || sum !== Number(digits[digits.length - 1]) ? ["Nieprawidłowa suma kontrolna REGON."] : [];
}

const LOCAL_VALIDATORS = {
  PESEL: peselErrors,
  NIP: nipErrors,
  REGON: regonErrors,
  PL_PHONE: (v) => (/^(?:\+48)?\d{9}$/.test(v.replace(/[\s\-()]/g, "")) ? [] : ["Numer telefonu musi mieć 9 cyfr (opcjonalnie +48)."]),
  POSTAL_CODE: (v) => (/^\d{2}-\d{3}$/.test(v.trim()) ? [] : ["Kod pocztowy musi mieć format NN-NNN."]),
};

function isNumeric(text) {
  const normalized = text.replace(",", ".");
  return normalized.trim() !== "" && Number.isFinite(Number(normalized));
}

function isValidIsoDate(text) {
  const [y, m, d] = text.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
}

function localFieldErrors(value, rules, required) {
  const text = value === null || value === undefined ? "" : String(value).trim();
  if (text === "" || value === false) {
    return required ? ["To pole jest wymagane."] : [];
  }
  if (!rules) return [];
  const errors = [];
  switch (rules.data_type) {
    case "integer":
      if (!/^-?\d+$/.test(text)) errors.push("Wartość musi być liczbą całkowitą.");
      break;
    case "number":
      if (!isNumeric(text)) errors.push("Wartość musi być liczbą.");
      break;
    case "boolean":
      if (!["true", "false", "1", "0", "tak", "nie"].includes(text.toLowerCase())) {
        errors.push("Wartość musi być logiczna (true/false).");
      }
      break;
    case "date":
      if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) errors.push("Data musi mieć format RRRR-MM-DD.");
      else if (!isValidIsoDate(text)) errors.push("Nieprawidłowa data.");
      break;
    default: break;
  }
  if (rules.min_length != null && text.length < rules.min_length) errors.push(`Wartość jest za krótka (min. ${rules.min_length} znaków).`);
  if (rules.max_length != null && text.length > rules.max_length) errors.push(`Wartość jest za długa (maks. ${rules.max_length} znaków).`);
  if (rules.min_value != null || rules.max_value != null) {
    if (!isNumeric(text)) errors.push("Wartość musi być liczbą, aby sprawdzić zakres.");
    else {
      const numeric = Number(text.replace(",", "."));
      if (rules.min_value != null && numeric < rules.min_value) errors.push(`Wartość jest mniejsza niż minimum (${rules.min_value}).`);
      if (rules.max_value != null && numeric > rules.max_value) errors.push(`Wartość jest większa niż maksimum (${rules.max_value}).`);
    }
  }
  if (rules.pattern) {
    try { if (!new RegExp(`^(?:${rules.pattern})$`).test(text)) errors.push("Wartość nie pasuje do wymaganego wzorca."); }
    catch { /* nieprawidłowy regex po stronie serwera */ }
  }
  if (rules.custom_validator && LOCAL_VALIDATORS[rules.custom_validator]) {
    errors.push(...LOCAL_VALIDATORS[rules.custom_validator](text));
  }
  if (errors.length && rules.error_message) return [rules.error_message];
  return errors;
}

/* ---------------- Zakładki ---------------- */

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
}

/* ---------------- Konfiguracja + auth ---------------- */

async function loadConfig() {
  try {
    const config = await api("/config");
    state.config = config;
    $("config-info").textContent =
      `Maks. rozmiar pliku: ${formatBytes(config.max_file_size_bytes)} • ` +
      `Maks. plików w batchu: ${config.max_batch_files} • JWT: ${config.jwt_auth_enabled ? "włączony" : "wyłączony"}`;
    $("allowed-ext").textContent = `Dozwolone formaty: ${config.allowed_extensions.join(", ")}`;
    $("file-input").accept = config.allowed_extensions.join(",");
    $("auth-bar").classList.toggle("hidden", !config.jwt_auth_enabled);
    $("btn-dev-token").classList.toggle("hidden", config.environment !== "development");
    $("env-badge").textContent = config.environment;
    $("jwt-token").value = state.token;
  } catch (error) {
    toast(`Nie udało się pobrać konfiguracji: ${error.message}`);
  }
  try {
    const health = await api("/health");
    const badge = $("pipeline-badge");
    badge.textContent = `pipeline: ${health.pipeline_mode}`;
    badge.className = `badge ${health.pipeline_mode === "mock" ? "badge-mock" : "badge-real"}`;
  } catch { $("pipeline-badge").textContent = "backend offline"; }
}

function formatBytes(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

/* ---------------- Import (single / batch) ---------------- */

function clientFileError(file) {
  const name = file.name;
  if (!name) return "Pusta nazwa pliku.";
  if (name.length > MAX_FILENAME_LENGTH) return `Nazwa pliku przekracza ${MAX_FILENAME_LENGTH} znaków.`;
  if (name.includes("..")) return "Nazwa pliku zawiera niedozwolony ciąg '..'.";
  const forbidden = [...new Set([...name].filter((ch) => FORBIDDEN_FILENAME_CHARS.has(ch) || ch.charCodeAt(0) < 32))];
  if (forbidden.length) return `Nazwa zawiera niedozwolone znaki: ${forbidden.join(" ")}`;
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  if (state.config && !state.config.allowed_extensions.includes(ext)) return `Niedozwolone rozszerzenie '${ext}'.`;
  if (state.config && file.size > state.config.max_file_size_bytes) {
    return `Plik przekracza limit ${formatBytes(state.config.max_file_size_bytes)}.`;
  }
  if (file.size === 0) return "Plik jest pusty.";
  return null;
}

function addFiles(fileList) {
  for (const file of fileList) {
    if (!state.selectedFiles.some((f) => f.name === file.name && f.size === file.size)) {
      state.selectedFiles.push(file);
    }
  }
  renderSelectedFiles();
}

function renderSelectedFiles() {
  const list = $("selected-files");
  list.replaceChildren();
  for (const file of state.selectedFiles) {
    const error = clientFileError(file);
    list.append(el("li", { class: error ? "result-error" : "" },
      el("span", { class: "file-name", text: file.name }),
      el("span", { class: "file-size", text: formatBytes(file.size) }),
      error ? el("span", { class: "err small", text: error }) : null,
      el("button", { class: "btn btn-secondary small", type: "button", onclick: () => {
        state.selectedFiles = state.selectedFiles.filter((f) => f !== file);
        renderSelectedFiles();
      } }, "Usuń"),
    ));
  }
  $("btn-convert").disabled = state.selectedFiles.length === 0;
}

function setProgress(percent, label, mode = "running") {
  $("progress-wrap").classList.remove("hidden");
  const bar = $("progress-bar");
  const clamped = Math.max(0, Math.min(100, Math.round(percent)));
  bar.classList.toggle("indeterminate", mode === "indeterminate");
  bar.classList.toggle("done", mode === "done");
  bar.classList.toggle("failed", mode === "failed");
  bar.style.width = mode === "indeterminate" ? "40%" : `${clamped}%`;
  bar.parentElement.setAttribute("aria-valuenow", String(clamped));
  $("progress-label").textContent = label;
}

/* Upload przez XHR (fetch nie raportuje postępu wysyłania). Błędy mają ten sam kształt co w api(). */
function uploadForm(path, body, { onUploadProgress, onUploaded } = {}) {
  const xhr = new XMLHttpRequest();
  const promise = new Promise((resolve, reject) => {
    xhr.open("POST", `${API}${path}`);
    if (state.token) xhr.setRequestHeader("Authorization", `Bearer ${state.token}`);
    xhr.responseType = "text";
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable && onUploadProgress) onUploadProgress(e.loaded / e.total);
    });
    xhr.upload.addEventListener("load", () => onUploaded?.());
    xhr.addEventListener("load", () => {
      let payload = null;
      try { payload = JSON.parse(xhr.responseText); } catch { /* odpowiedź nie-JSON */ }
      if (xhr.status >= 200 && xhr.status < 300 && payload) { resolve(payload); return; }
      const err = payload?.error;
      const error = new Error(err ? `${err.code}: ${err.message}` : `Błąd HTTP ${xhr.status}`);
      error.code = err?.code || `HTTP_${xhr.status}`;
      error.details = err?.details;
      error.status = xhr.status;
      reject(error);
    });
    xhr.addEventListener("error", () => {
      const error = new Error("Brak połączenia z serwerem.");
      error.code = "NETWORK_ERROR";
      error.status = 0;
      reject(error);
    });
    xhr.addEventListener("abort", () => {
      const error = new Error("Przetwarzanie anulowane przez użytkownika.");
      error.code = "CANCELLED";
      error.cancelled = true;
      reject(error);
    });
    xhr.send(body);
  });
  return { promise, abort: () => xhr.abort() };
}

const importRun = { active: false, cancelled: false, abort: null };

function errorResult(filename, error) {
  return { filename, status: "error", error: { code: error.code || "HTTP_ERROR", message: error.message, details: error.details } };
}

/* Błąd dotyczy całego żądania (auth, sieć, limit) — ponawianie pojedynczo nic nie da. */
function isRequestWideError(error) {
  return error.cancelled || [0, 401, 403, 429].includes(error.status);
}

function chunk(array, size) {
  const out = [];
  for (let i = 0; i < array.length; i += size) out.push(array.slice(i, i + size));
  return out;
}

async function convertSingle(file, progress) {
  const body = new FormData();
  body.append("file", file);
  const request = uploadForm("/forms/convert", body, {
    onUploadProgress: (f) => progress.upload(f),
    onUploaded: () => progress.processing(),
  });
  importRun.abort = request.abort;
  const response = await request.promise;
  return { filename: file.name, status: "success", ...response };
}

async function convertChunk(files, progress) {
  if (files.length === 1) {
    try { return [await convertSingle(files[0], progress)]; }
    catch (error) { if (error.cancelled) throw error; return [errorResult(files[0].name, error)]; }
  }
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  const request = uploadForm("/forms/convert-batch", body, {
    onUploadProgress: (f) => progress.upload(f),
    onUploaded: () => progress.processing(),
  });
  importRun.abort = request.abort;
  try {
    const response = await request.promise;
    return response.results;
  } catch (error) {
    if (error.cancelled) throw error;
    if (isRequestWideError(error)) return files.map((file) => errorResult(file.name, error));
    // Batch padł w całości (np. 5xx/503) — ratujemy wyniki, przetwarzając pliki pojedynczo.
    toast(`Batch nie powiódł się (${error.message}). Ponawiam pliki pojedynczo…`, "info");
    const results = [];
    for (const [index, file] of files.entries()) {
      if (importRun.cancelled) throw Object.assign(new Error("Anulowano."), { cancelled: true });
      progress.retry(index, files.length, file.name);
      try { results.push(await convertSingle(file, { upload: () => {}, processing: () => {} })); }
      catch (singleError) {
        if (singleError.cancelled) throw singleError;
        results.push(errorResult(file.name, singleError));
      }
    }
    return results;
  }
}

async function convertFiles() {
  const files = [...state.selectedFiles];
  if (!files.length || importRun.active) return;
  const valid = [];
  const results = [];
  for (const file of files) {
    const error = clientFileError(file);
    if (error) results.push(errorResult(file.name, { code: "CLIENT_VALIDATION", message: error }));
    else valid.push(file);
  }
  const batchLimit = Math.max(1, state.config?.max_batch_files || 20);
  const chunks = chunk(valid, batchLimit);
  const pending = new Map(valid.map((file) => [file, { filename: file.name, status: "pending" }]));
  const startedAt = performance.now();

  importRun.active = true;
  importRun.cancelled = false;
  $("btn-convert").disabled = true;
  $("btn-cancel").classList.remove("hidden");
  const publish = () => { state.results = [...results, ...pending.values()]; renderResults(); };
  publish();

  let done = 0;
  const total = valid.length;
  let cancelled = false;
  try {
    if (!total) setProgress(100, "Brak poprawnych plików do wysłania.", "failed");
    for (const [chunkIdx, files] of chunks.entries()) {
      files.forEach((file) => { pending.get(file).status = "processing"; });
      publish();
      const prefix = chunks.length > 1 ? `Paczka ${chunkIdx + 1}/${chunks.length}: ` : "";
      const base = (done / total) * 100;
      const span = (files.length / total) * 100;
      const progress = {
        // wysyłka = 0–60% udziału paczki, przetwarzanie na serwerze = 60–100%
        upload: (f) => setProgress(base + span * 0.6 * f, `${prefix}wysyłanie ${files.length === 1 ? files[0].name : `${files.length} plików`}… ${Math.round(f * 100)}%`),
        processing: () => setProgress(base + span * 0.6, `${prefix}przetwarzanie na serwerze (${done + 1}–${done + files.length} z ${total})…`),
        retry: (i, n, name) => setProgress(base + span * (i / n), `${prefix}ponawianie pojedynczo ${i + 1}/${n}: ${name}…`),
      };
      progress.upload(0);
      const chunkResults = await convertChunk(files, progress);
      files.forEach((file) => pending.delete(file));
      results.push(...chunkResults);
      done += files.length;
      setProgress((done / total) * 100, `Przetworzono ${done}/${total} plików.`);
      publish();
    }
  } catch (error) {
    if (!error.cancelled) throw error;
    cancelled = true;
    for (const entry of pending.values()) {
      results.push(errorResult(entry.filename, { code: "CANCELLED", message: "Anulowano przed zakończeniem przetwarzania." }));
    }
    pending.clear();
  } finally {
    importRun.active = false;
    importRun.abort = null;
    $("btn-convert").disabled = state.selectedFiles.length === 0;
    $("btn-cancel").classList.add("hidden");
  }

  state.results = results;
  renderResults();
  const ok = results.filter((r) => r.status === "success").length;
  const seconds = ((performance.now() - startedAt) / 1000).toFixed(1);
  if (cancelled) setProgress((done / Math.max(total, 1)) * 100, `Anulowano. Przetworzono ${done}/${total} plików (${seconds} s).`, "failed");
  else if (total) {
    setProgress(100, `Zakończono: ${ok}/${results.length} poprawnie, ${results.length - ok} z błędem (${seconds} s).`,
      ok === results.length ? "done" : ok === 0 ? "failed" : "running");
  }
  const firstSuccess = results.find((r) => r.status === "success");
  if (firstSuccess) openForm(firstSuccess.form, { switchTo: results.length === 1 ? "json" : null });
}

function cancelImport() {
  if (!importRun.active) return;
  importRun.cancelled = true;
  importRun.abort?.();
}

function downloadBlob(content, filename) {
  const blob = new Blob([content], { type: "application/json" });
  const link = el("a", { href: URL.createObjectURL(blob), download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

function jsonFilename(sourceName) {
  const base = (sourceName || "coi-form").replace(/\.[^.]+$/, "").replace(/[^\p{L}\p{N}._-]+/gu, "_");
  return `${base || "coi-form"}.json`;
}

function downloadAllResults() {
  const successes = state.results.filter((r) => r.status === "success");
  if (!successes.length) return;
  const bundle = {
    generated_at: new Date().toISOString(),
    summary: {
      total: state.results.length,
      succeeded: successes.length,
      failed: state.results.filter((r) => r.status === "error").length,
    },
    results: state.results.map((r) => (r.status === "success"
      ? { filename: r.filename, status: r.status, form: r.form, warnings: r.warnings || [] }
      : { filename: r.filename, status: r.status, error: r.error })),
  };
  downloadBlob(JSON.stringify(bundle, null, 2), "coi-batch-wyniki.json");
}

const RESULT_BADGES = {
  success: ["badge-real", "OK"],
  error: ["badge-error", "BŁĄD"],
  pending: ["badge-muted", "w kolejce"],
  processing: ["badge-progress", "przetwarzanie…"],
};

function renderResults() {
  const list = $("results-list");
  list.replaceChildren();
  const finished = state.results.filter((r) => r.status === "success" || r.status === "error");
  const succeeded = finished.filter((r) => r.status === "success").length;
  const failed = finished.length - succeeded;
  const waiting = state.results.length - finished.length;
  $("batch-summary").textContent = state.results.length
    ? `Wyniki: ${succeeded}/${state.results.length} poprawnie • ${failed} z błędem${waiting ? ` • ${waiting} w toku` : ""}.`
    : "Brak wyników — zaimportuj pliki.";
  $("btn-download-all").classList.toggle("hidden", succeeded === 0 || waiting > 0);
  for (const result of state.results) {
    const details = el("div", { class: "result-details" });
    if (result.status === "error") {
      details.append(el("div", { class: "err", text: `${result.error.code}: ${result.error.message}` }));
    } else if (result.status === "success") {
      const p = result.processing;
      if (p) {
        details.append(el("div", { class: "muted", text:
          `${p.pages_count} stron • ${p.fields_count} pól • ${p.processing_time_ms} ms • ${p.mime_type} • tryb ${p.pipeline_mode}` }));
      }
      for (const warning of result.warnings || []) {
        details.append(el("div", { class: "warn", text: `⚠ ${warning.code}: ${warning.message}` }));
      }
    }
    const [badgeClass, badgeText] = RESULT_BADGES[result.status] || RESULT_BADGES.error;
    list.append(el("li", { class: `result-${result.status}` },
      el("span", { class: "file-name", text: result.filename }),
      el("span", { class: `badge ${badgeClass}`, text: badgeText }),
      result.status === "success"
        ? [
          el("button", { class: "btn btn-secondary small", type: "button", onclick: () => openForm(result.form) }, "Otwórz JSON i wizualizację"),
          el("button", { class: "btn btn-secondary small", type: "button",
            onclick: () => downloadBlob(JSON.stringify(result.form, null, 2), jsonFilename(result.filename)) }, "Pobierz JSON"),
        ]
        : null,
      details.childNodes.length ? details : null,
    ));
  }
}

/* ---------------- JSON + inspektor słowników ---------------- */

function openForm(form, { switchTo = "json" } = {}) {
  state.currentForm = form;
  setEditorText(JSON.stringify(form, null, 2));
  $("json-validation-output").replaceChildren();
  renderDictionaryInspector(form);
  initVisualizer(form);
  if (switchTo) switchTab(switchTo);
}

/* ---------------- Edytor JSON (textarea + numery linii + diagnostyka składni) ---------------- */

const editor = { parseTimer: null, lastError: null };

function setEditorText(text) {
  $("json-editor").value = text;
  refreshEditor();
}

/* Pozycja błędu JSON.parse: Chrome/Node podają "position N" lub "line L column C", Firefox "line L column C". */
function jsonErrorLocation(message, text) {
  const lineCol = /line (\d+) column (\d+)/i.exec(message);
  if (lineCol) {
    const line = Number(lineCol[1]);
    const column = Number(lineCol[2]);
    const lines = text.split("\n");
    let offset = 0;
    for (let i = 0; i < line - 1 && i < lines.length; i += 1) offset += lines[i].length + 1;
    return { line, column, offset: offset + column - 1 };
  }
  const pos = /position (\d+)/i.exec(message);
  if (pos) {
    const offset = Number(pos[1]);
    const before = text.slice(0, offset).split("\n");
    return { line: before.length, column: before[before.length - 1].length + 1, offset };
  }
  const offset = findJsonErrorOffset(text);
  if (offset === null) return null;
  const before = text.slice(0, offset).split("\n");
  return { line: before.length, column: before[before.length - 1].length + 1, offset };
}

/* Minimalny skaner składni JSON — zwraca offset pierwszego błędu (nowsze Chrome nie podają pozycji). */
function findJsonErrorOffset(text) {
  let i = 0;
  const ws = () => { while (i < text.length && " \t\n\r".includes(text[i])) i += 1; };
  const fail = () => { throw i; };
  const literal = (word) => { if (text.startsWith(word, i)) i += word.length; else fail(); };
  const string = () => {
    if (text[i] !== '"') fail();
    i += 1;
    while (i < text.length && text[i] !== '"') {
      if (text.charCodeAt(i) < 32) fail();
      if (text[i] === "\\") {
        i += 1;
        if (text[i] === "u") { if (!/^[0-9a-fA-F]{4}$/.test(text.slice(i + 1, i + 5))) fail(); i += 4; }
        else if (!'"\\/bfnrt'.includes(text[i] ?? "x")) fail();
      }
      i += 1;
    }
    if (i >= text.length) fail();
    i += 1;
  };
  const number = () => {
    const m = /^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i, i + 400));
    if (!m) fail();
    i += m[0].length;
  };
  const value = () => {
    ws();
    const ch = text[i];
    if (ch === "{") {
      i += 1; ws();
      if (text[i] === "}") { i += 1; return; }
      for (;;) {
        ws(); string(); ws();
        if (text[i] !== ":") fail();
        i += 1; value(); ws();
        if (text[i] === ",") { i += 1; continue; }
        if (text[i] === "}") { i += 1; return; }
        fail();
      }
    } else if (ch === "[") {
      i += 1; ws();
      if (text[i] === "]") { i += 1; return; }
      for (;;) {
        value(); ws();
        if (text[i] === ",") { i += 1; continue; }
        if (text[i] === "]") { i += 1; return; }
        fail();
      }
    } else if (ch === '"') string();
    else if (ch === "t") literal("true");
    else if (ch === "f") literal("false");
    else if (ch === "n") literal("null");
    else number();
  };
  try {
    value(); ws();
    return i < text.length ? i : null;
  } catch (offset) {
    return typeof offset === "number" ? Math.min(offset, text.length) : null;
  }
}

function parseEditor() {
  const text = $("json-editor").value;
  if (!text.trim()) return { empty: true };
  try { return { value: JSON.parse(text) }; }
  catch (error) { return { error, location: jsonErrorLocation(error.message, text) }; }
}

function renderGutter(lineCount, errorLine) {
  const gutter = $("json-gutter");
  const nodes = [];
  for (let i = 1; i <= lineCount; i += 1) {
    if (i === errorLine) nodes.push(el("span", { class: "err-line", text: String(i) }));
    else nodes.push(String(i));
    if (i < lineCount) nodes.push("\n");
  }
  gutter.replaceChildren(...nodes);
  gutter.scrollTop = $("json-editor").scrollTop;
}

function refreshEditor() {
  const text = $("json-editor").value;
  const lineCount = text.split("\n").length;
  const status = $("json-status");
  const result = parseEditor();
  editor.lastError = result.error ? result : null;
  renderGutter(lineCount, result.location?.line);
  $("json-editor").closest(".json-editor-wrap").classList.toggle("has-error", Boolean(result.error));
  status.className = "small";
  if (result.empty) {
    status.classList.add("muted");
    status.textContent = "Pusty edytor.";
  } else if (result.error) {
    status.classList.add("err");
    const where = result.location ? `linia ${result.location.line}, kolumna ${result.location.column}` : "pozycja nieznana";
    const short = result.error.message.replace(/\s+/g, " ");
    status.textContent = `Błąd składni JSON (${where}): ${short.length > 140 ? `${short.slice(0, 140)}…` : short}`;
    status.title = "Kliknij, aby przejść do miejsca błędu";
  } else {
    status.classList.add("ok");
    const fields = countFields(result.value);
    const dirty = state.currentForm && JSON.stringify(result.value) !== JSON.stringify(state.currentForm);
    status.textContent = `Składnia poprawna • ${lineCount} linii • ${fields} pól` + (dirty ? " • niezastosowane zmiany" : "");
    status.title = "";
  }
}

function countFields(form) {
  let count = 0;
  const walk = (fields) => { for (const f of fields || []) { count += 1; walk(f.fields); } };
  for (const page of form?.pages || []) for (const section of page.sections || []) walk(section.fields);
  return count;
}

function focusEditorAt(offset, length = 1) {
  const area = $("json-editor");
  area.focus();
  area.setSelectionRange(offset, Math.min(offset + length, area.value.length));
  const line = area.value.slice(0, offset).split("\n").length;
  const lineHeight = parseFloat(getComputedStyle(area).lineHeight) || 18;
  area.scrollTop = Math.max(0, (line - 5) * lineHeight);
}

/* Ścieżka z błędu Pydantic (np. "pages.0.sections.1.fields.2.label") → pozycja klucza w tekście. */
function locateJsonPath(path) {
  const parsed = parseEditor();
  if (!parsed.value) return null;
  const text = $("json-editor").value;
  const parts = path.split(".");
  let node = parsed.value;
  const anchors = [];
  for (const part of parts) {
    if (node === null || typeof node !== "object") break;
    if (Array.isArray(node)) {
      const item = node[Number(part)];
      if (item && typeof item === "object") {
        const idKey = Object.keys(item).find((k) => /_id$/.test(k) && typeof item[k] === "string");
        if (idKey) anchors.push(`"${idKey}": ${JSON.stringify(item[idKey])}`);
      }
      node = item;
    } else {
      if (!(part in node)) break; // brakujące pole — wskazujemy obiekt-rodzica
      anchors.push(`"${part}":`);
      node = node[part];
    }
  }
  let offset = 0;
  let found = -1;
  for (const anchor of anchors) {
    const idx = text.indexOf(anchor, offset);
    if (idx === -1) continue;
    found = idx;
    offset = idx;
  }
  return found === -1 ? null : found;
}

function editorKeydown(e) {
  const area = e.target;
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); applyJson(); return; }
  if (e.key !== "Tab") return;
  e.preventDefault();
  const { selectionStart: start, selectionEnd: end, value } = area;
  const lineStart = value.lastIndexOf("\n", start - 1) + 1;
  if (start === end && !e.shiftKey) {
    area.setRangeText("  ", start, end, "end");
  } else {
    const block = value.slice(lineStart, end);
    const changed = e.shiftKey ? block.replace(/^ {1,2}/gm, "") : block.replace(/^/gm, "  ");
    area.setRangeText(changed, lineStart, end, "select");
  }
  scheduleEditorRefresh();
}

function scheduleEditorRefresh() {
  clearTimeout(editor.parseTimer);
  editor.parseTimer = setTimeout(refreshEditor, 200);
}

function reformatEditor(indent) {
  const parsed = parseEditor();
  if (parsed.error) { toast("Nie można sformatować — popraw najpierw błąd składni."); return; }
  if (parsed.empty) return;
  setEditorText(JSON.stringify(parsed.value, null, indent));
}

function loadJsonFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    setEditorText(String(reader.result));
    $("json-validation-output").replaceChildren();
    const parsed = parseEditor();
    if (parsed.value) toast(`Wczytano ${file.name}. Kliknij „Zastosuj i wizualizuj”.`, "info");
    else toast(`Wczytano ${file.name}, ale JSON zawiera błąd składni.`);
  };
  reader.onerror = () => toast(`Nie udało się odczytać pliku ${file.name}.`);
  reader.readAsText(file, "utf-8");
}

async function validateJson() {
  const output = $("json-validation-output");
  output.replaceChildren();
  let parsed;
  const result0 = parseEditor();
  if (result0.empty) { output.append(el("div", { class: "err", text: "Edytor jest pusty." })); return; }
  if (result0.error) {
    output.append(el("div", { class: "err", text: `Nieprawidłowy JSON: ${result0.error.message}` }));
    if (result0.location) focusEditorAt(result0.location.offset);
    return;
  }
  parsed = result0.value;
  try {
    const result = await api("/forms/validate-json", { method: "POST", json: { form: parsed } });
    if (result.valid) output.append(el("div", { class: "ok", text: "JSON zgodny ze schematem COI i spójny." }));
    const total = result.schema_errors.length + result.consistency_errors.length;
    if (total) output.append(el("div", { class: "err", text: `Znaleziono błędów: ${total}. Kliknij błąd schematu, aby przejść do miejsca w JSON.` }));
    for (const error of result.schema_errors) {
      const path = error.split(":")[0];
      output.append(el("div", { class: "err link", text: error, title: "Przejdź do miejsca w edytorze", onclick: () => {
        const offset = locateJsonPath(path);
        if (offset === null) toast("Nie udało się wskazać miejsca w edytorze.");
        else focusEditorAt(offset, 1);
      } }));
    }
    for (const error of result.consistency_errors) output.append(el("div", { class: "err", text: error }));
    for (const warning of result.warnings) output.append(el("div", { class: "warn", text: `⚠ ${warning}` }));
  } catch (error) {
    output.append(el("div", { class: "err", text: error.message }));
  }
}

function applyJson() {
  const result = parseEditor();
  if (result.empty) { toast("Edytor jest pusty."); return; }
  if (result.error) {
    toast(`Nieprawidłowy JSON: ${result.error.message}`);
    if (result.location) focusEditorAt(result.location.offset);
    return;
  }
  const parsed = result.value;
  if (!Array.isArray(parsed?.pages)) { toast("JSON nie zawiera tablicy „pages” — to nie wygląda na formularz COI."); return; }
  state.currentForm = parsed;
  refreshEditor();
  renderDictionaryInspector(parsed);
  initVisualizer(parsed);
  switchTab("visualizer");
  toast("Wizualizacja odświeżona z edytowanego JSON-a.", "info");
}

function downloadJson() {
  const text = $("json-editor").value;
  if (!text.trim()) { toast("Edytor jest pusty."); return; }
  const source = state.currentForm?.metadata?.source_filename;
  downloadBlob(text, jsonFilename(source));
}

async function getDictionaryItems(dictionaryId) {
  if (state.dictCache.has(dictionaryId)) return state.dictCache.get(dictionaryId);
  try {
    const detail = await api(`/dictionaries/${encodeURIComponent(dictionaryId)}`);
    state.dictCache.set(dictionaryId, detail.items);
    return detail.items;
  } catch {
    state.dictCache.set(dictionaryId, []);
    return [];
  }
}

function dictItemsList(items) {
  return el("ul", { class: "dict-items" },
    items.map((item) => el("li", {}, el("code", { text: item.code }), ` — ${item.label}`)));
}

function renderDictionaryInspector(form) {
  const mappedBox = $("mapped-dicts");
  const proposedBox = $("proposed-dicts");
  mappedBox.replaceChildren();
  proposedBox.replaceChildren();
  const mapped = form.dictionaries?.mapped_dictionaries || [];
  const proposed = form.dictionaries?.proposed_dictionaries || [];
  if (!mapped.length) mappedBox.append(el("p", { class: "muted", text: "Brak zmapowanych słowników." }));
  if (!proposed.length) proposedBox.append(el("p", { class: "muted", text: "Brak propozycji nowych słowników." }));

  for (const entry of mapped) {
    const card = el("div", { class: "dict-card" },
      el("h4", { text: entry.dictionary_name }),
      el("div", { class: "dict-meta" },
        el("code", { text: entry.dictionary_id }), ` → pole ${entry.field_id} `,
        el("span", { class: "confidence", text: `${Math.round(entry.confidence * 100)}%` })),
      el("div", { class: "muted small", text: "Wczytywanie pozycji…" }));
    mappedBox.append(card);
    getDictionaryItems(entry.dictionary_id).then((items) => {
      card.lastChild.replaceWith(items.length ? dictItemsList(items) : el("p", { class: "muted small", text: "Brak pozycji w bazie." }));
    });
  }
  for (const entry of proposed) {
    proposedBox.append(el("div", { class: "dict-card" },
      el("h4", {}, entry.name, " ", el("span", { class: "badge badge-new", text: "NOWY" })),
      el("div", { class: "dict-meta" }, el("code", { text: entry.proposed_id }), ` → pole ${entry.target_field_id}`),
      el("p", { class: "small", text: entry.reason }),
      dictItemsList(entry.items || [])));
  }
}

/* ---------------- Wizualizator e-Usługi ---------------- */

function collectFlows(form) {
  const flows = [];
  const seen = new Set();
  const push = (flow) => {
    const key = JSON.stringify(flow);
    if (!seen.has(key)) { seen.add(key); flows.push(flow); }
  };
  (form.conditional_flows || []).forEach(push);
  for (const page of form.pages || []) {
    (page.visibility_rules || []).forEach(push);
    for (const section of page.sections || []) {
      (section.visibility_rules || []).forEach(push);
      for (const field of section.fields || []) (field.dependencies || []).forEach(push);
    }
  }
  return flows;
}

function indexForm(form) {
  viz.fieldIndex.clear(); viz.sectionIndex.clear(); viz.pageIndex.clear();
  const walkFields = (fields) => {
    for (const field of fields) {
      viz.fieldIndex.set(field.field_id, field);
      if (field.fields) walkFields(field.fields);
    }
  };
  for (const page of form.pages || []) {
    viz.pageIndex.set(page.page_id, page);
    for (const section of page.sections || []) {
      viz.sectionIndex.set(section.section_id, section);
      walkFields(section.fields || []);
    }
  }
}

function conditionMet(flow) {
  const raw = viz.values[flow.source_field_id];
  const value = raw === undefined || raw === null ? "" : raw;
  switch (flow.operator) {
    case "EQUALS": return String(value) === String(flow.value);
    case "NOT_EQUALS": return String(value) !== String(flow.value);
    case "IN": return Array.isArray(flow.value) && flow.value.map(String).includes(String(value));
    case "IS_EMPTY": return value === "" || value === false;
    case "GREATER_THAN": return Number(value) > Number(flow.value);
    default: return false;
  }
}

function computeState(targetType, targetId, baseRequired) {
  let visible = true;
  let required = baseRequired;
  let showRules = 0;
  let showMet = false;
  for (const flow of viz.flows) {
    if (flow.target_type !== targetType || flow.target_id !== targetId) continue;
    const met = conditionMet(flow);
    if (flow.action === "SHOW") { showRules += 1; if (met) showMet = true; }
    else if (flow.action === "HIDE" && met) visible = false;
    else if (flow.action === "REQUIRE" && met) required = true;
    else if (flow.action === "OPTIONAL" && met) required = false;
    else if (flow.action === "SET_VALUE" && targetType === "FIELD") {
      const key = JSON.stringify(flow);
      if (met && !viz.appliedSetValue.has(key)) {
        viz.appliedSetValue.add(key);
        viz.values[targetId] = flow.action_value;
        setInputValue(viz.refs.get(targetId), flow.action_value);
      } else if (!met) viz.appliedSetValue.delete(key);
    }
  }
  if (showRules > 0 && !showMet) visible = false;
  return { visible, required };
}

/* Odzwierciedla wartość ustawioną przez SET_VALUE w kontrolce dowolnego typu. */
function setInputValue(ref, value) {
  if (!ref?.input) return;
  const input = ref.input;
  if (input.type === "checkbox") input.checked = value === true || value === "true";
  else if (input.classList?.contains("radio-group")) {
    input.querySelectorAll("input[type=radio]").forEach((radio) => { radio.checked = radio.value === String(value ?? ""); });
  } else if (input.type !== "file") input.value = value ?? "";
}

async function resolveFieldOptions(field) {
  if (field.options?.length) return field.options;
  if (!field.dictionary_ref) return [];
  const proposed = (viz.form.dictionaries?.proposed_dictionaries || [])
    .find((p) => p.proposed_id === field.dictionary_ref || p.target_field_id === field.field_id);
  if (proposed) return proposed.items || [];
  return getDictionaryItems(field.dictionary_ref);
}

async function initVisualizer(form) {
  viz.form = form;
  viz.values = {};
  viz.pageIdx = 0;
  viz.flows = collectFlows(form);
  viz.appliedSetValue.clear();
  viz.refs.clear();
  indexForm(form);
  for (const field of viz.fieldIndex.values()) {
    if (field.field_type === "repeatable_group") viz.values[field.field_id] = [{}];
    else if (field.field_type === "checkbox") viz.values[field.field_id] = field.initial_value === true;
    else viz.values[field.field_id] = field.initial_value ?? "";
  }
  $("viz-title").textContent = form.metadata?.title || "Wizualizacja e-Usługi";
  $("viz-summary").classList.add("hidden");
  $("btn-viz-download").classList.add("hidden");
  $("viz-messages").replaceChildren();
  await renderCurrentPage();
}

function visiblePages() {
  return (viz.form.pages || [])
    .map((page) => ({ page, state: computeState("PAGE", page.page_id, false) }))
    .filter((entry) => entry.state.visible)
    .map((entry) => entry.page);
}

function renderPageNav() {
  const nav = $("viz-pages");
  nav.replaceChildren();
  const pages = visiblePages();
  pages.forEach((page, index) => {
    nav.append(el("button", {
      class: `viz-page-btn ${index === viz.pageIdx ? "active" : ""}`,
      type: "button",
      onclick: () => { viz.pageIdx = index; renderCurrentPage(); },
    }, `${index + 1}. ${page.title}`));
  });
  $("btn-viz-prev").disabled = viz.pageIdx === 0;
  const last = viz.pageIdx >= pages.length - 1;
  $("btn-viz-next").classList.toggle("hidden", last);
  $("btn-viz-summary").classList.toggle("hidden", !last);
}

let renderToken = 0;

async function renderCurrentPage() {
  if (!viz.form) return;
  const token = ++renderToken;
  const pages = visiblePages();
  if (viz.pageIdx >= pages.length) viz.pageIdx = Math.max(0, pages.length - 1);
  renderPageNav();
  const formEl = $("viz-form");
  const page = pages[viz.pageIdx];
  if (!page) {
    viz.refs.clear();
    formEl.replaceChildren(el("p", { class: "muted", text: "Brak widocznych stron — zaimportuj plik lub zastosuj JSON." }));
    return;
  }
  // Budujemy stronę poza DOM; słowniki ładują się asynchronicznie, więc starszy render
  // (np. po szybkim kliknięciu "Dalej") nie może nadpisać nowszego.
  const refs = new Map();
  const fragment = document.createDocumentFragment();
  if (page.description) fragment.append(el("p", { class: "muted", text: page.description }));
  const sections = [...(page.sections || [])].sort((a, b) => a.order - b.order);
  for (const section of sections) {
    const sectionEl = el("section", { class: "viz-section" },
      el("h3", { text: section.title }),
      section.description ? el("p", { class: "section-desc", text: section.description }) : null);
    const fields = [...(section.fields || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    for (const field of fields) {
      const node = await renderField(field, refs);
      if (token !== renderToken) return;
      sectionEl.append(node);
    }
    fragment.append(sectionEl);
    refs.set(`section:${section.section_id}`, { wrap: sectionEl });
  }
  if (token !== renderToken) return;
  viz.refs = refs;
  formEl.replaceChildren(fragment);
  applyFlows();
}

async function renderField(field, refs = viz.refs) {
  const wrap = el("div", { class: "viz-field", "data-field-id": field.field_id });
  const label = el("label", { for: `input-${field.field_id}` }, field.label);
  const errorEl = el("div", { class: "field-error hidden" });
  const helpEl = field.help_text ? el("div", { class: "help", text: field.help_text }) : null;
  let input = null;

  const commit = (value) => {
    viz.values[field.field_id] = value;
    applyFlows();
    scheduleValidation(field);
  };

  switch (field.field_type) {
    case "textarea":
      input = el("textarea", { id: `input-${field.field_id}`, placeholder: field.placeholder || "", oninput: (e) => commit(e.target.value) });
      input.value = viz.values[field.field_id] ?? "";
      break;
    case "select": {
      const options = await resolveFieldOptions(field);
      input = el("select", { id: `input-${field.field_id}`, onchange: (e) => commit(e.target.value) },
        el("option", { value: "" }, "— wybierz —"),
        options.map((item) => el("option", { value: item.code }, item.label)));
      input.value = viz.values[field.field_id] ?? "";
      break;
    }
    case "radio": {
      const options = await resolveFieldOptions(field);
      input = el("div", { class: "radio-group" }, options.map((item) =>
        el("label", {},
          el("input", { type: "radio", name: `radio-${field.field_id}`, value: item.code,
            onchange: (e) => { if (e.target.checked) commit(item.code); } }),
          item.label)));
      const current = viz.values[field.field_id];
      input.querySelectorAll("input").forEach((radio) => { radio.checked = radio.value === current; });
      break;
    }
    case "checkbox":
      input = el("input", { type: "checkbox", id: `input-${field.field_id}`, onchange: (e) => commit(e.target.checked) });
      input.checked = viz.values[field.field_id] === true;
      wrap.append(el("label", { class: "checkbox-label", for: `input-${field.field_id}` }, input, field.label));
      wrap.append(...[helpEl, errorEl].filter(Boolean));
      refs.set(field.field_id, { wrap, input, errorEl, label: null, field });
      return wrap;
    case "file":
      input = el("input", { type: "file", id: `input-${field.field_id}`, onchange: (e) => commit(e.target.files[0]?.name || "") });
      break;
    case "number":
      input = el("input", { type: "number", id: `input-${field.field_id}`, placeholder: field.placeholder || "", oninput: (e) => commit(e.target.value) });
      input.value = viz.values[field.field_id] ?? "";
      break;
    case "date":
      input = el("input", { type: "date", id: `input-${field.field_id}`, oninput: (e) => commit(e.target.value) });
      input.value = viz.values[field.field_id] ?? "";
      break;
    case "repeatable_group": {
      const group = el("div", { class: "repeatable-group" });
      const entries = viz.values[field.field_id] || [{}];
      const subRefs = [];
      const renderEntries = () => {
        group.replaceChildren();
        subRefs.length = 0;
        entries.forEach((entry, index) => {
          const entryEl = el("div", { class: "repeatable-entry" },
            el("div", { class: "repeatable-entry-head", text: `Wpis ${index + 1}` }));
          const entryRefs = [];
          for (const sub of field.fields || []) {
            const inputType = { number: "number", date: "date" }[sub.field_type] || "text";
            const subError = el("div", { class: "field-error hidden" });
            const subInput = el("input", { type: inputType, id: `input-${field.field_id}-${index}-${sub.field_id}`,
              placeholder: sub.placeholder || "",
              oninput: (e) => {
                entry[sub.field_id] = e.target.value;
                viz.values[field.field_id] = entries;
                showErrorsOn(subInput, subError, localFieldErrors(e.target.value, sub.validation_rules, sub.required));
              } });
            subInput.value = entry[sub.field_id] ?? "";
            const subLabel = el("label", { for: subInput.id }, sub.label, sub.required ? el("span", { class: "required-mark", text: " *" }) : null);
            entryEl.append(el("div", { class: "viz-field" }, subLabel, subInput,
              sub.help_text ? el("div", { class: "help", text: sub.help_text }) : null, subError));
            entryRefs.push({ sub, input: subInput, errorEl: subError, entry });
          }
          subRefs.push(entryRefs);
          entryEl.append(el("button", { class: "btn btn-secondary small", type: "button", onclick: () => {
            entries.splice(index, 1);
            if (!entries.length) entries.push({});
            viz.values[field.field_id] = entries;
            renderEntries();
          } }, "Usuń wpis"));
          group.append(entryEl);
        });
        group.append(el("button", { class: "btn btn-secondary small", type: "button", onclick: () => {
          entries.push({});
          viz.values[field.field_id] = entries;
          renderEntries();
        } }, "Dodaj wpis"));
      };
      renderEntries();
      wrap.append(...[label, group, helpEl, errorEl].filter(Boolean));
      refs.set(field.field_id, { wrap, input: null, errorEl, label, field, subRefs });
      return wrap;
    }
    default:
      input = el("input", { type: "text", id: `input-${field.field_id}`, placeholder: field.placeholder || "", oninput: (e) => commit(e.target.value) });
      input.value = viz.values[field.field_id] ?? "";
  }
  wrap.append(...[label, input, helpEl, errorEl].filter(Boolean));
  refs.set(field.field_id, { wrap, input, errorEl, label, field });
  return wrap;
}

function applyFlows() {
  for (const [sectionId, section] of viz.sectionIndex) {
    const ref = viz.refs.get(`section:${sectionId}`);
    if (!ref) continue;
    const { visible } = computeState("SECTION", sectionId, false);
    ref.wrap.classList.toggle("hidden", !visible);
  }
  for (const [fieldId, field] of viz.fieldIndex) {
    const ref = viz.refs.get(fieldId);
    if (!ref) continue;
    const { visible, required } = computeState("FIELD", fieldId, field.required);
    ref.wrap.classList.toggle("hidden", !visible);
    if (ref.label) {
      ref.label.querySelector(".required-mark")?.remove();
      if (required) ref.label.append(el("span", { class: "required-mark", text: " *" }));
    }
    ref.required = required;
    ref.visible = visible;
  }
  renderPageNav();
}

function showErrorsOn(input, errorEl, errors) {
  errorEl.replaceChildren(...errors.map((error) => el("div", { text: error })));
  errorEl.classList.toggle("hidden", errors.length === 0);
  if (input?.classList) input.classList.toggle("invalid", errors.length > 0);
}

function showFieldErrors(fieldId, errors) {
  const ref = viz.refs.get(fieldId);
  if (!ref) return;
  showErrorsOn(ref.input, ref.errorEl, errors);
}

function entryIsEmpty(entry) {
  return Object.values(entry || {}).every((v) => v === undefined || v === null || String(v).trim() === "");
}

/* Walidacja grupy powtarzalnej: wymagalność całej grupy + reguły pól w każdym niepustym wpisie. */
function repeatableGroupErrors(field, required) {
  const entries = viz.values[field.field_id] || [];
  const filled = entries.filter((entry) => !entryIsEmpty(entry));
  const groupErrors = required && filled.length === 0 ? ["Dodaj co najmniej jeden wypełniony wpis."] : [];
  const entryErrors = entries.map((entry) => {
    if (entryIsEmpty(entry) && !(required && filled.length === 0 && entry === entries[0])) {
      return (field.fields || []).map(() => []);
    }
    return (field.fields || []).map((sub) => localFieldErrors(entry[sub.field_id], sub.validation_rules, sub.required));
  });
  return { groupErrors, entryErrors, ok: groupErrors.length === 0 && entryErrors.every((e) => e.every((x) => !x.length)) };
}

function fieldErrorsFor(field, required, show) {
  if (field.field_type === "repeatable_group") {
    const result = repeatableGroupErrors(field, required);
    if (show) {
      const ref = viz.refs.get(field.field_id);
      if (ref) {
        showErrorsOn(null, ref.errorEl, result.groupErrors);
        (ref.subRefs || []).forEach((entryRefs, i) => entryRefs.forEach((subRef, j) => {
          showErrorsOn(subRef.input, subRef.errorEl, result.entryErrors[i]?.[j] || []);
        }));
      }
    }
    return result.ok ? [] : (result.groupErrors.length ? result.groupErrors : ["Popraw pola we wpisach grupy."]);
  }
  const errors = localFieldErrors(viz.values[field.field_id], field.validation_rules, required);
  if (show) showFieldErrors(field.field_id, errors);
  return errors;
}

function scheduleValidation(field) {
  clearTimeout(viz.timers.get(field.field_id));
  const ref = viz.refs.get(field.field_id);
  const required = ref?.required ?? field.required;
  const localErrors = localFieldErrors(viz.values[field.field_id], field.validation_rules, required);
  showFieldErrors(field.field_id, localErrors);
  viz.timers.set(field.field_id, setTimeout(async () => {
    const value = viz.values[field.field_id];
    if (value === "" || value === null || value === undefined || value === false) return;
    try {
      const result = await api("/forms/validate-field", { method: "POST", json: { value, rules: field.validation_rules || null } });
      if (!result.valid) showFieldErrors(field.field_id, result.errors);
    } catch { /* serwer niedostępny — lokalna walidacja wystarcza */ }
  }, 350));
}

function validatePage(page, show) {
  if (!page) return true;
  let ok = true;
  for (const section of page.sections || []) {
    const sectionState = computeState("SECTION", section.section_id, false);
    if (!sectionState.visible) continue;
    for (const field of section.fields || []) {
      const { visible, required } = computeState("FIELD", field.field_id, field.required);
      if (!visible) continue;
      if (fieldErrorsFor(field, required, show).length) ok = false;
    }
  }
  return ok;
}

function validateCurrentPage() {
  return validatePage(visiblePages()[viz.pageIdx], true);
}

function markInvalidPages(invalidIdx) {
  document.querySelectorAll("#viz-pages .viz-page-btn").forEach((btn, i) => btn.classList.toggle("invalid", invalidIdx.includes(i)));
}

function showSummary() {
  const messages = $("viz-messages");
  messages.replaceChildren();
  const pages = visiblePages();
  const invalid = pages.map((page, i) => (validatePage(page, i === viz.pageIdx) ? -1 : i)).filter((i) => i >= 0);
  if (invalid.length) {
    const names = invalid.map((i) => `${i + 1}. ${pages[i].title}`).join(", ");
    $("viz-summary").classList.add("hidden");
    $("btn-viz-download").classList.add("hidden");
    if (!invalid.includes(viz.pageIdx)) {
      viz.pageIdx = invalid[0];
      renderCurrentPage().then(() => { validateCurrentPage(); markInvalidPages(invalid); });
    } else markInvalidPages(invalid);
    messages.append(el("div", { class: "err", text: `Popraw błędy przed podsumowaniem. Strony z błędami: ${names}.` }));
    return;
  }
  markInvalidPages([]);
  const payload = { form_id: viz.form.metadata?.form_id, title: viz.form.metadata?.title, values: {} };
  for (const page of visiblePages()) {
    for (const section of page.sections || []) {
      if (!computeState("SECTION", section.section_id, false).visible) continue;
      for (const field of section.fields || []) {
        const { visible } = computeState("FIELD", field.field_id, field.required);
        if (!visible) continue;
        const value = viz.values[field.field_id];
        payload.values[field.field_id] = field.field_type === "repeatable_group"
          ? (value || []).filter((entry) => !entryIsEmpty(entry))
          : value;
      }
    }
  }
  const summary = $("viz-summary");
  summary.textContent = JSON.stringify(payload, null, 2);
  summary.classList.remove("hidden");
  $("btn-viz-download").classList.remove("hidden");
  messages.append(el("div", { class: "ok", text: "Wniosek gotowy — dane zebrane z widocznych pól e-Usługi." }));
}

/* ---------------- Init ---------------- */

function bindEvents() {
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));

  $("file-input").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  const dropzone = $("dropzone");
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragover"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    addFiles(e.dataTransfer.files);
  });
  $("btn-convert").addEventListener("click", convertFiles);
  $("btn-clear-files").addEventListener("click", () => { state.selectedFiles = []; renderSelectedFiles(); });

  $("jwt-token").addEventListener("input", (e) => {
    state.token = e.target.value.trim();
    sessionStorage.setItem("jwt_token", state.token);
    $("auth-status").textContent = state.token ? "token ustawiony" : "";
  });
  $("btn-dev-token").addEventListener("click", async () => {
    try {
      const response = await api("/auth/dev-token", { method: "POST" });
      state.token = response.access_token;
      sessionStorage.setItem("jwt_token", state.token);
      $("jwt-token").value = state.token;
      $("auth-status").textContent = "dev-token pobrany";
      toast("Pobrano testowy token JWT.", "info");
    } catch (error) { toast(error.message); }
  });

  $("btn-validate-json").addEventListener("click", validateJson);
  $("btn-apply-json").addEventListener("click", applyJson);
  $("btn-download-json").addEventListener("click", downloadJson);
  $("btn-format-json").addEventListener("click", () => reformatEditor(2));
  $("btn-minify-json").addEventListener("click", () => reformatEditor(0));
  $("json-file-input").addEventListener("change", (e) => { loadJsonFile(e.target.files[0]); e.target.value = ""; });
  const area = $("json-editor");
  area.addEventListener("input", scheduleEditorRefresh);
  area.addEventListener("keydown", editorKeydown);
  area.addEventListener("scroll", () => { $("json-gutter").scrollTop = area.scrollTop; });
  $("json-status").addEventListener("click", () => {
    if (editor.lastError?.location) focusEditorAt(editor.lastError.location.offset);
  });
  $("btn-cancel").addEventListener("click", cancelImport);
  $("btn-download-all").addEventListener("click", downloadAllResults);

  $("btn-viz-prev").addEventListener("click", () => { if (viz.pageIdx > 0) { viz.pageIdx -= 1; renderCurrentPage(); } });
  $("btn-viz-next").addEventListener("click", () => {
    if (!validateCurrentPage()) {
      $("viz-messages").replaceChildren(el("div", { class: "err", text: "Popraw błędy na tej stronie, aby przejść dalej." }));
      return;
    }
    $("viz-messages").replaceChildren();
    viz.pageIdx += 1;
    renderCurrentPage();
  });
  $("btn-viz-summary").addEventListener("click", showSummary);
  $("btn-viz-download").addEventListener("click", () => {
    downloadBlob($("viz-summary").textContent, `wniosek-${jsonFilename(viz.form?.metadata?.form_id)}`);
  });
  $("btn-viz-reset").addEventListener("click", () => { if (viz.form) initVisualizer(viz.form); });
}

bindEvents();
loadConfig();
