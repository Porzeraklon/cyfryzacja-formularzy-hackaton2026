/* formularzex — frontend SPA (Branch 2: feat/frontend-and-visualizer).
 * Vanilla JS bez bundlera, podpięty pod /api/v1/* (działa z mock pipeline).
 * Formularz w edytorze i wizualizatorze to oficjalny FormDefinition (formdef.js, visualizer.js);
 * JSON backendu w formacie wewnętrznym jest konwertowany przy otwarciu.
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
  currentForm: null,      // FormDefinition (format oficjalny COI)
  sourceForm: null,       // oryginalny JSON backendu (format wewnętrzny), jeśli był konwertowany
  conversionNotes: [],
  dictCache: new Map(),
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

async function downloadAllResults() {
  const successes = state.results.filter((r) => r.status === "success");
  if (!successes.length) return;
  const bundle = {
    generated_at: new Date().toISOString(),
    summary: {
      total: state.results.length,
      succeeded: successes.length,
      failed: state.results.filter((r) => r.status === "error").length,
    },
    results: await Promise.all(state.results.map(async (r) => (r.status === "success"
      ? { filename: r.filename, status: r.status, form: (await toFormDefinition(r.form)).form, warnings: r.warnings || [] }
      : { filename: r.filename, status: r.status, error: r.error }))),
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
            onclick: async () => downloadBlob(JSON.stringify((await toFormDefinition(result.form)).form, null, 2),
              jsonFilename(result.filename)) }, "Pobierz JSON (FormDefinition)"),
        ]
        : null,
      details.childNodes.length ? details : null,
    ));
  }
}

/* ---------------- JSON + inspektor słowników ---------------- */

/* Dowolny JSON formularza → FormDefinition (konwersja formatu wewnętrznego backendu). */
async function toFormDefinition(form) {
  if (fdIsFormDefinition(form)) return { form, source: null, notes: [] };
  if (!fdIsInternalForm(form)) throw new Error("JSON nie zawiera ani „tabs” (FormDefinition), ani „pages” (format backendu).");
  const refs = new Set();
  const walk = (fields) => { for (const f of fields || []) { if (f.dictionary_ref) refs.add(f.dictionary_ref); walk(f.fields); } };
  for (const page of form.pages) for (const section of page.sections || []) walk(section.fields);
  const knownDictionaries = await Promise.all([...refs].map(async (id) => ({ dictionary_id: id, items: await getDictionaryItems(id) })));
  const { form: converted, notes } = fdConvertInternal(form, { knownDictionaries });
  return { form: converted, source: form, notes };
}

async function openForm(form, { switchTo = "json" } = {}) {
  let result;
  try { result = await toFormDefinition(form); } catch (error) { toast(error.message); return; }
  state.currentForm = result.form;
  state.sourceForm = result.source;
  state.conversionNotes = result.notes;
  setEditorText(JSON.stringify(result.form, null, 2));
  renderDictionaryInspector();
  renderStructureReport();
  await initVisualizer(result.form);
  if (switchTo) switchTab(switchTo);
}

function renderStructureReport() {
  const output = $("json-validation-output");
  output.replaceChildren();
  const issues = fdValidateStructure(state.currentForm, { compileQuery: vizCompileProblem });
  const errors = issues.filter((i) => i.level === "error");
  const warns = issues.filter((i) => i.level === "warn");
  output.append(el("div", { class: errors.length ? "err" : "ok", text: errors.length
    ? `FormDefinition: ${errors.length} błędów i ${warns.length} ostrzeżeń względem reguł konkursu. Kliknij błąd, aby przejść do miejsca w JSON.`
    : `FormDefinition zgodny z regułami technicznymi COI (${warns.length} ostrzeżeń dobrych praktyk).` }));
  for (const issue of [...errors, ...warns]) {
    output.append(el("div", { class: `${issue.level === "error" ? "err" : "warn"} link`, title: "Przejdź do miejsca w edytorze",
      text: `${issue.level === "error" ? "✗" : "⚠"} [${issue.rule}] ${issue.message}`, onclick: () => {
        const offset = issue.path ? locateJsonPath(issue.path) : 0;
        if (offset === null) toast("Nie udało się wskazać miejsca w edytorze.");
        else focusEditorAt(offset, 1);
      } }));
  }
  if (state.conversionNotes.length) {
    const details = el("details", { class: "conversion-notes" },
      el("summary", { text: `Konwersja z formatu backendu do FormDefinition: ${state.conversionNotes.length} uwag` }),
      el("ul", {}, state.conversionNotes.map((n) => el("li", { text: n }))));
    output.append(details);
  }
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
  if (fdIsFormDefinition(form)) {
    fdWalk(form, ({ field }) => { if (field && !FD_SECTION_TYPES.includes(field.type)) count += 1; });
    return count;
  }
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
        const idKey = Object.keys(item).find((k) => /(^id$|_id$)/.test(k) && typeof item[k] === "string");
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
  const parsed = parseEditor();
  if (parsed.empty) { output.append(el("div", { class: "err", text: "Edytor jest pusty." })); return; }
  if (parsed.error) {
    output.append(el("div", { class: "err", text: `Nieprawidłowy JSON: ${parsed.error.message}` }));
    if (parsed.location) focusEditorAt(parsed.location.offset);
    return;
  }
  if (fdIsFormDefinition(parsed.value)) {
    const previous = state.currentForm;
    state.currentForm = parsed.value;
    renderStructureReport();
    state.currentForm = previous;
    return;
  }
  /* Format wewnętrzny backendu — walidacja serwerowa (schemat Pydantic + spójność). */
  try {
    const result = await api("/forms/validate-json", { method: "POST", json: { form: parsed.value } });
    output.append(el("div", { class: "warn", text: "To format wewnętrzny backendu, nie FormDefinition. „Zastosuj i wizualizuj” skonwertuje go do formatu COI." }));
    if (result.valid) output.append(el("div", { class: "ok", text: "JSON zgodny ze schematem wewnętrznym i spójny." }));
    for (const error of [...result.schema_errors, ...result.consistency_errors]) output.append(el("div", { class: "err", text: error }));
    for (const warning of result.warnings) output.append(el("div", { class: "warn", text: `⚠ ${warning}` }));
  } catch (error) {
    output.append(el("div", { class: "err", text: error.message }));
  }
}

async function applyJson() {
  const result = parseEditor();
  if (result.empty) { toast("Edytor jest pusty."); return; }
  if (result.error) {
    toast(`Nieprawidłowy JSON: ${result.error.message}`);
    if (result.location) focusEditorAt(result.location.offset);
    return;
  }
  const wasInternal = !fdIsFormDefinition(result.value);
  await openForm(result.value, { switchTo: null });
  if (!state.currentForm) return;
  refreshEditor();
  switchTab("visualizer");
  toast(wasInternal ? "Skonwertowano JSON backendu do FormDefinition i odświeżono wizualizację."
    : "Wizualizacja odświeżona z edytowanego JSON-a.", "info");
}

function downloadJson() {
  const text = $("json-editor").value;
  if (!text.trim()) { toast("Edytor jest pusty."); return; }
  downloadBlob(text, jsonFilename(state.currentForm?.title || state.sourceForm?.metadata?.source_filename));
}

async function loadExample() {
  try {
    const response = await fetch("examples/wniosek-o-stwierdzenie-nadplaty-podatku.json");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    await openForm(await response.json(), { switchTo: "visualizer" });
    toast("Wczytano przykładowy formularz COI (wzór z materiałów konkursu).", "info");
  } catch (error) { toast(`Nie udało się wczytać przykładu: ${error.message}`); }
}

/* Identyfikatory słowników dostępnych w lokalnej bazie COI (jedno zapytanie, bez 404 dla nieznanych). */
function knownDictionaryIds() {
  if (!state.dictIds) {
    state.dictIds = api("/dictionaries")
      .then((list) => new Set(list.dictionaries.map((d) => d.dictionary_id)))
      .catch(() => { state.dictIds = null; return new Set(); });
  }
  return state.dictIds;
}

async function getDictionaryItems(dictionaryId) {
  if (state.dictCache.has(dictionaryId)) return state.dictCache.get(dictionaryId);
  if (!(await knownDictionaryIds()).has(dictionaryId)) return [];
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
  return el("ul", { class: "dict-items" }, items.slice(0, 50).map((item) => el("li", { text: item })),
    items.length > 50 ? el("li", { class: "muted", text: `… i ${items.length - 50} kolejnych` }) : null);
}

/* Słowniki użyte w FormDefinition (dictionaryName) + propozycje nowych słowników z backendu. */
function renderDictionaryInspector() {
  const mappedBox = $("mapped-dicts");
  const proposedBox = $("proposed-dicts");
  mappedBox.replaceChildren();
  proposedBox.replaceChildren();
  const usage = new Map();
  fdWalk(state.currentForm, ({ field }) => {
    if (!field?.dictionaryName) return;
    if (!usage.has(field.dictionaryName)) usage.set(field.dictionaryName, []);
    usage.get(field.dictionaryName).push(field);
  });
  if (!usage.size) mappedBox.append(el("p", { class: "muted", text: "Formularz nie korzysta ze słowników." }));
  for (const [name, fields] of usage) {
    const dict = fdFindDictionary(name);
    const card = el("div", { class: "dict-card" },
      el("h4", {}, name, " ", el("span", { class: `badge ${dict ? "badge-real" : "badge-new"}`, text: dict ? "COI" : "spoza katalogu" })),
      el("div", { class: "dict-meta", text: `Pola: ${fields.map((f) => `${f.title || f.techName} (${f.type})`).join(", ")}` }),
      dict ? el("div", { class: "muted small", text: `Dozwolony typ pola: ${dict.allowedTypes.join(", ")}` }) : null,
      el("div", { class: "muted small", text: "Wczytywanie pozycji…" }));
    mappedBox.append(card);
    vizOptions(fields[0]).then((items) => {
      card.lastChild.replaceWith(items.length ? dictItemsList(items)
        : el("p", { class: "muted small", text: "Brak pozycji offline — słownik dostarcza platforma e-Usług (TERYT)." }));
    });
  }
  const proposed = state.sourceForm?.dictionaries?.proposed_dictionaries || [];
  if (!proposed.length) proposedBox.append(el("p", { class: "muted", text: "Brak propozycji nowych słowników." }));
  for (const entry of proposed) {
    proposedBox.append(el("div", { class: "dict-card" },
      el("h4", {}, entry.name, " ", el("span", { class: "badge badge-new", text: "NOWY" })),
      el("div", { class: "dict-meta" }, el("code", { text: entry.proposed_id }), ` → pole ${entry.target_field_id}`),
      el("p", { class: "small", text: entry.reason }),
      el("p", { class: "small muted", text: "Struktura: pozycje { id, name, techName } — w FormDefinition wstawione jako options pola." }),
      dictItemsList((entry.items || []).map((i) => i.label))));
  }
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

  $("btn-load-example").addEventListener("click", loadExample);
  $("btn-viz-prev").addEventListener("click", vizPrev);
  $("btn-viz-next").addEventListener("click", vizNext);
  $("btn-viz-abort").addEventListener("click", vizAbort);
  $("btn-viz-download").addEventListener("click", vizDownloadAnswers);
  $("btn-viz-reset").addEventListener("click", () => { if (viz.form) initVisualizer(viz.form); });
}

bindEvents();
loadConfig();
