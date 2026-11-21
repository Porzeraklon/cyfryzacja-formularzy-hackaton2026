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

function localFieldErrors(value, rules, required) {
  const text = value === null || value === undefined ? "" : String(value).trim();
  if (text === "" || value === false) {
    return required ? ["To pole jest wymagane."] : [];
  }
  if (!rules) return [];
  const errors = [];
  if (rules.min_length != null && text.length < rules.min_length) errors.push(`Minimum ${rules.min_length} znaków.`);
  if (rules.max_length != null && text.length > rules.max_length) errors.push(`Maksimum ${rules.max_length} znaków.`);
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

function setProgress(percent, label, indeterminate = false) {
  $("progress-wrap").classList.remove("hidden");
  const bar = $("progress-bar");
  bar.classList.toggle("indeterminate", indeterminate);
  bar.style.width = indeterminate ? "40%" : `${percent}%`;
  $("progress-label").textContent = label;
}

async function convertFiles() {
  const files = state.selectedFiles;
  if (!files.length) return;
  const valid = [];
  const localErrors = [];
  for (const file of files) {
    const error = clientFileError(file);
    if (error) localErrors.push({ filename: file.name, status: "error", error: { code: "CLIENT_VALIDATION", message: error } });
    else valid.push(file);
  }
  if (state.config && valid.length > state.config.max_batch_files) {
    toast(`Za dużo plików w batchu (limit ${state.config.max_batch_files}).`);
    return;
  }
  $("btn-convert").disabled = true;
  const results = [...localErrors];
  try {
    if (valid.length === 1) {
      setProgress(50, `Przetwarzanie: ${valid[0].name}…`, true);
      const body = new FormData();
      body.append("file", valid[0]);
      const response = await api("/forms/convert", { method: "POST", body });
      results.push({ filename: valid[0].name, status: "success", ...response });
    } else if (valid.length > 1) {
      setProgress(50, `Przetwarzanie zbiorcze: ${valid.length} plików…`, true);
      const body = new FormData();
      valid.forEach((file) => body.append("files", file));
      const response = await api("/forms/convert-batch", { method: "POST", body });
      results.push(...response.results);
    }
    setProgress(100, "Zakończono.");
  } catch (error) {
    setProgress(100, "Błąd przetwarzania.");
    if (valid.length === 1) {
      results.push({ filename: valid[0].name, status: "error", error: { code: error.code || "HTTP_ERROR", message: error.message } });
    } else {
      toast(error.message);
    }
  } finally {
    $("btn-convert").disabled = false;
  }
  state.results = results;
  renderResults();
  const firstSuccess = results.find((r) => r.status === "success");
  if (firstSuccess) openForm(firstSuccess.form);
}

function renderResults() {
  const list = $("results-list");
  list.replaceChildren();
  const succeeded = state.results.filter((r) => r.status === "success").length;
  $("batch-summary").textContent = `Wyniki: ${succeeded}/${state.results.length} poprawnie przetworzonych.`;
  for (const result of state.results) {
    const details = el("div", { class: "result-details" });
    if (result.status === "error") {
      details.append(el("div", { class: "err", text: `${result.error.code}: ${result.error.message}` }));
    } else {
      const p = result.processing;
      details.append(el("div", { class: "muted", text:
        `${p.pages_count} stron • ${p.fields_count} pól • ${p.processing_time_ms} ms • ${p.mime_type} • tryb ${p.pipeline_mode}` }));
      for (const warning of result.warnings || []) {
        details.append(el("div", { class: "warn", text: `⚠ ${warning.code}: ${warning.message}` }));
      }
    }
    list.append(el("li", { class: result.status === "success" ? "result-success" : "result-error" },
      el("span", { class: "file-name", text: result.filename }),
      el("span", { class: `badge ${result.status === "success" ? "badge-real" : "badge-mock"}`, text: result.status === "success" ? "OK" : "BŁĄD" }),
      result.status === "success"
        ? el("button", { class: "btn btn-secondary small", type: "button", onclick: () => openForm(result.form) }, "Otwórz JSON i wizualizację")
        : null,
      details,
    ));
  }
}

/* ---------------- JSON + inspektor słowników ---------------- */

function openForm(form) {
  state.currentForm = form;
  $("json-editor").value = JSON.stringify(form, null, 2);
  $("json-validation-output").replaceChildren();
  renderDictionaryInspector(form);
  initVisualizer(form);
  switchTab("json");
}

async function validateJson() {
  const output = $("json-validation-output");
  output.replaceChildren();
  let parsed;
  try { parsed = JSON.parse($("json-editor").value); }
  catch (error) {
    output.append(el("div", { class: "err", text: `Nieprawidłowy JSON: ${error.message}` }));
    return;
  }
  try {
    const result = await api("/forms/validate-json", { method: "POST", json: { form: parsed } });
    if (result.valid) output.append(el("div", { class: "ok", text: "JSON zgodny ze schematem COI i spójny." }));
    for (const error of [...result.schema_errors, ...result.consistency_errors]) {
      output.append(el("div", { class: "err", text: error }));
    }
    for (const warning of result.warnings) output.append(el("div", { class: "warn", text: `⚠ ${warning}` }));
  } catch (error) {
    output.append(el("div", { class: "err", text: error.message }));
  }
}

function applyJson() {
  let parsed;
  try { parsed = JSON.parse($("json-editor").value); }
  catch (error) { toast(`Nieprawidłowy JSON: ${error.message}`); return; }
  state.currentForm = parsed;
  renderDictionaryInspector(parsed);
  initVisualizer(parsed);
  switchTab("visualizer");
  toast("Wizualizacja odświeżona z edytowanego JSON-a.", "info");
}

function downloadJson() {
  const blob = new Blob([$("json-editor").value], { type: "application/json" });
  const link = el("a", { href: URL.createObjectURL(blob), download: "coi-form.json" });
  link.click();
  URL.revokeObjectURL(link.href);
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
        const ref = viz.refs.get(targetId);
        if (ref?.input) ref.input.value = flow.action_value ?? "";
      } else if (!met) viz.appliedSetValue.delete(key);
    }
  }
  if (showRules > 0 && !showMet) visible = false;
  return { visible, required };
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

async function renderCurrentPage() {
  if (!viz.form) return;
  const pages = visiblePages();
  if (viz.pageIdx >= pages.length) viz.pageIdx = Math.max(0, pages.length - 1);
  renderPageNav();
  const formEl = $("viz-form");
  formEl.replaceChildren();
  viz.refs.clear();
  const page = pages[viz.pageIdx];
  if (!page) {
    formEl.append(el("p", { class: "muted", text: "Brak widocznych stron — zaimportuj plik lub zastosuj JSON." }));
    return;
  }
  if (page.description) formEl.append(el("p", { class: "muted", text: page.description }));
  const sections = [...(page.sections || [])].sort((a, b) => a.order - b.order);
  for (const section of sections) {
    const sectionEl = el("section", { class: "viz-section" },
      el("h3", { text: section.title }),
      section.description ? el("p", { class: "section-desc", text: section.description }) : null);
    const fields = [...(section.fields || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    for (const field of fields) sectionEl.append(await renderField(field));
    formEl.append(sectionEl);
    viz.refs.set(`section:${section.section_id}`, { wrap: sectionEl });
  }
  applyFlows();
}

async function renderField(field) {
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
      wrap.append(helpEl, errorEl);
      viz.refs.set(field.field_id, { wrap, input, errorEl, label: null, field });
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
      const renderEntries = () => {
        group.replaceChildren();
        entries.forEach((entry, index) => {
          const entryEl = el("div", { class: "repeatable-entry" });
          for (const sub of field.fields || []) {
            const subInput = el("input", { type: "text", id: `input-${field.field_id}-${index}-${sub.field_id}`,
              placeholder: sub.placeholder || "",
              oninput: (e) => { entry[sub.field_id] = e.target.value; } });
            subInput.value = entry[sub.field_id] ?? "";
            entryEl.append(el("div", { class: "viz-field" }, el("label", { text: sub.label }), subInput));
          }
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
      wrap.append(label, group, helpEl, errorEl);
      viz.refs.set(field.field_id, { wrap, input: null, errorEl, label, field });
      return wrap;
    }
    default:
      input = el("input", { type: "text", id: `input-${field.field_id}`, placeholder: field.placeholder || "", oninput: (e) => commit(e.target.value) });
      input.value = viz.values[field.field_id] ?? "";
  }
  wrap.append(label, input, helpEl, errorEl);
  viz.refs.set(field.field_id, { wrap, input, errorEl, label, field });
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

function showFieldErrors(fieldId, errors) {
  const ref = viz.refs.get(fieldId);
  if (!ref) return;
  ref.errorEl.replaceChildren();
  ref.errorEl.classList.toggle("hidden", errors.length === 0);
  for (const error of errors) ref.errorEl.append(el("div", { text: error }));
  if (ref.input && ref.input.classList) ref.input.classList.toggle("invalid", errors.length > 0);
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

function validateCurrentPage() {
  const pages = visiblePages();
  const page = pages[viz.pageIdx];
  if (!page) return true;
  let ok = true;
  for (const section of page.sections || []) {
    const sectionState = computeState("SECTION", section.section_id, false);
    if (!sectionState.visible) continue;
    for (const field of section.fields || []) {
      const { visible, required } = computeState("FIELD", field.field_id, field.required);
      if (!visible) continue;
      const errors = localFieldErrors(viz.values[field.field_id], field.validation_rules, required);
      showFieldErrors(field.field_id, errors);
      if (errors.length) ok = false;
    }
  }
  return ok;
}

function showSummary() {
  const messages = $("viz-messages");
  messages.replaceChildren();
  if (!validateCurrentPage()) {
    messages.append(el("div", { class: "err", text: "Popraw błędy na bieżącej stronie przed podsumowaniem." }));
    return;
  }
  const payload = { form_id: viz.form.metadata?.form_id, title: viz.form.metadata?.title, values: {} };
  for (const page of visiblePages()) {
    for (const section of page.sections || []) {
      if (!computeState("SECTION", section.section_id, false).visible) continue;
      for (const field of section.fields || []) {
        const { visible } = computeState("FIELD", field.field_id, field.required);
        if (visible) payload.values[field.field_id] = viz.values[field.field_id];
      }
    }
  }
  const summary = $("viz-summary");
  summary.textContent = JSON.stringify(payload, null, 2);
  summary.classList.remove("hidden");
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
      $("auth-status").textContent = "dev-token pobany";
      toast("Pobrano testowy token JWT.", "info");
    } catch (error) { toast(error.message); }
  });

  $("btn-validate-json").addEventListener("click", validateJson);
  $("btn-apply-json").addEventListener("click", applyJson);
  $("btn-download-json").addEventListener("click", downloadJson);

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
  $("btn-viz-reset").addEventListener("click", () => { if (viz.form) initVisualizer(viz.form); });
}

bindEvents();
loadConfig();
