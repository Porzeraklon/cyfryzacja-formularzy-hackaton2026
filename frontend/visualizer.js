/* formularzex — wizualizator e-Usługi renderujący oficjalny FormDefinition.
 * [OWNERSHIP: Branch 2] — frontend/. Zależy od: formdef.js, vendor/jsonata.min.js (MIT, lokalnie)
 * oraz helperów z app.js ($, el, toast, api, downloadBlob, jsonFilename, getDictionaryItems).
 *
 * Obsługuje: strony MAIN/FINISH i przejścia flow.links, sekcje i sekcje powtarzające się
 * (repetingSectionGrid), 11 typów pól, walidatory z tablicy `validation`, słowniki (dictionaryName),
 * readOnly i dane inicjalne (EXTERNAL_SYSTEM), stany dynamiczne `states` (SHOW, READ_ONLY) liczone
 * przez jsonata na modelu tabs[].questions[].field.__userAnswer, układ grid-area z x/y/rows/cols.
 */
"use strict";

const viz = {
  form: null,
  order: [],
  pageIdx: 0,
  values: new Map(),       // field.id → wartość; sekcja powtarzająca się → [ { childId: wartość } ]
  nodes: [],               // zarejestrowane elementy bieżącej strony
  exprCache: new Map(),
  queryErrors: new Set(),
  fieldById: new Map(),
  parentOf: new Map(),
  touched: new Set(),
};

const FILE_RULES = { formats: ".jpg, .pdf, .png, .jpeg, .doc, .odt, .docx, .ods", maxBytes: 10 * 1024 * 1024, maxFiles: 1 };
const TEXTAREA_MAX = 8000;

/* Element.append zamienia null na tekst "null" — pomijamy puste elementy. */
function vizAppend(parent, ...items) {
  parent.append(...items.flat().filter((item) => item !== null && item !== undefined && item !== false));
}

/* ---------------- jsonata ---------------- */

function vizCompile(query) {
  if (viz.exprCache.has(query)) return viz.exprCache.get(query);
  let compiled = null;
  try { compiled = typeof jsonata === "function" ? jsonata(query) : null; } catch (error) { compiled = { error }; }
  viz.exprCache.set(query, compiled);
  return compiled;
}

function vizCompileProblem(query) {
  if (typeof jsonata !== "function") return null;
  try { jsonata(query); return null; } catch (error) { return error.message; }
}

/* Wartość pola w postaci, w jakiej czyta ją jsonata (opcje po nazwie, jak w przykładach COI). */
function vizAnswer(field, value) {
  if (value === undefined || value === null || value === "") return undefined;
  if (Array.isArray(value)) return value.length ? value : undefined;
  if (field.type === "NUMBER") {
    const n = Number(String(value).replace(",", "."));
    return Number.isFinite(n) ? n : value;
  }
  return value;
}

/* Model danych dla zapytań: kopia formularza z __userAnswer na każdym polu (obie pisownie gridu). */
function vizBuildModel() {
  const fieldModel = (field) => {
    const copy = { ...field };
    const children = fdChildren(field);
    if (field.type === "REPEATING_SECTION") {
      const entries = viz.values.get(field.id) || [];
      copy.__userAnswer = entries.map((entry) => children.map((c) => ({
        fieldTechName: c.field.techName, fieldId: c.field.id, answers: vizAnswer(c.field, entry[c.field.id]),
      })));
      const grid = children.map((c) => ({ ...c, field: { ...c.field,
        __userAnswer: entries.map((entry) => vizAnswer(c.field, entry[c.field.id])).filter((v) => v !== undefined).flat() } }));
      copy.repetingSectionGrid = grid;
      copy.repeatingSectionGrid = grid;
    } else if (children.length) {
      const grid = children.map((c) => ({ ...c, field: fieldModel(c.field) }));
      copy.repetingSectionGrid = grid;
      copy.repeatingSectionGrid = grid;
    } else {
      copy.__userAnswer = vizAnswer(field, viz.values.get(field.id));
    }
    return copy;
  };
  return {
    title: viz.form.title,
    tabs: (viz.form.tabs || []).map((tab) => ({ ...tab, questions: (tab.questions || []).map((q) => ({ ...q, field: fieldModel(q.field) })) })),
  };
}

function vizEvalStates(field, model, entryIndex) {
  const result = { show: true, readOnly: false };
  for (const st of field.states || []) {
    if (!st?.query) continue;
    const compiled = vizCompile(st.query);
    let value = true;
    if (!compiled || compiled.error) {
      viz.queryErrors.add(`${field.techName}: ${compiled?.error?.message || "brak biblioteki jsonata"}`);
    } else {
      try {
        value = compiled.evaluate(model, entryIndex === undefined ? {} : { repeatingSectionIndex: entryIndex });
      } catch (error) {
        viz.queryErrors.add(`${field.techName}: ${error.message}`);
        value = true;
      }
    }
    const truthy = Array.isArray(value) ? value.some(Boolean) : !!value;
    if (st.type === "SHOW" && !truthy) result.show = false;
    if (st.type === "READ_ONLY" && truthy) result.readOnly = true;
  }
  return result;
}

/* ---------------- Inicjalizacja ---------------- */

function vizInitialValue(field) {
  if (field.defaultValueType === "EXTERNAL_SYSTEM") return FD_SAMPLE_REGISTRY[field.defaultValue] ?? "";
  const def = field.defaultValue;
  if (field.type === "CHECKBOX") {
    if (vizHasOptions(field)) return Array.isArray(def) ? [...def] : def ? [def] : [];
    return def === true || def === "true";
  }
  if (field.type === "CONSENT_SECTION") return (field.consentSectionConfig?.consents || []).map(() => false);
  if (field.type === "FILE_UPLOADER") return [];
  if (Array.isArray(def)) return def[0] ?? "";
  return def ?? "";
}

function vizHasOptions(field) {
  return !!field.dictionaryName || (field.options || []).length > 0;
}

function vizIndex(form) {
  viz.fieldById.clear();
  viz.parentOf.clear();
  fdWalk(form, ({ field, parent }) => {
    if (!field?.id) return;
    viz.fieldById.set(field.id, field);
    if (parent) viz.parentOf.set(field.id, parent);
  });
}

async function initVisualizer(form) {
  viz.form = form;
  viz.order = fdTabOrder(form);
  viz.pageIdx = 0;
  viz.values = new Map();
  viz.exprCache.clear();
  viz.queryErrors.clear();
  viz.touched.clear();
  vizIndex(form);
  fdWalk(form, ({ field, parent }) => {
    if (!field?.id || parent?.type === "REPEATING_SECTION") return;
    if (field.type === "REPEATING_SECTION") viz.values.set(field.id, [vizNewEntry(field)]);
    else if (!FD_SECTION_TYPES.includes(field.type) && !FD_PRESENTATION_TYPES.includes(field.type)) viz.values.set(field.id, vizInitialValue(field));
  });
  $("viz-title").textContent = form.title || "Wizualizacja e-Usługi";
  $("viz-messages").replaceChildren();
  await renderCurrentPage();
}

function vizNewEntry(section) {
  const entry = {};
  for (const child of fdChildren(section)) entry[child.field.id] = vizInitialValue(child.field);
  return entry;
}

/* ---------------- Słowniki ---------------- */

/* Etykiety słowników liczone raz na tablicę pozycji (słownik miejscowości ma dziesiątki tysięcy wpisów). */
const vizLabelCache = new WeakMap();
function vizLabels(items) {
  if (!vizLabelCache.has(items)) vizLabelCache.set(items, items.map((i) => i.label));
  return vizLabelCache.get(items);
}

async function vizOptions(field) {
  if ((field.options || []).length) return field.options.map((o) => o.name);
  if (!field.dictionaryName) return [];
  const dict = fdFindDictionary(field.dictionaryName);
  if (dict?.backendId) {
    const items = await getDictionaryItems(dict.backendId);
    if (items.length) return vizLabels(items);
  }
  if (dict?.items) return dict.items;
  return vizLabels(await getDictionaryItems(field.dictionaryName));
}

/* Duże listy (np. miejscowości) nie są renderowane jako <select> z tysiącami <option> — to blokowało
 * przeglądarkę przy każdym otwarciu. Zamiast tego combobox: pole tekstowe + lista co najwyżej
 * VIZ_COMBO_LIMIT pasujących pozycji, budowana dopiero przy otwarciu / wpisywaniu. */
const VIZ_COMBO_THRESHOLD = 200;
const VIZ_COMBO_LIMIT = 50;

function vizCombobox(id, describedBy, options, initial, commit, title) {
  const listId = `${id}-list`;
  let matches = [];
  let active = -1;
  let committed = initial;
  const commitValue = commit;
  commit = (value) => { committed = value; commitValue(value); };
  const input = el("input", { type: "text", id, role: "combobox", autocomplete: "off", class: "combo-input",
    placeholder: "Wybierz lub wpisz, aby wyszukać…", "aria-autocomplete": "list", "aria-expanded": "false",
    "aria-controls": listId, "aria-describedby": describedBy, "aria-label": title || null });
  input.value = initial;
  const list = el("ul", { id: listId, class: "combo-list hidden", role: "listbox" });
  const box = el("div", { class: "combo" }, input, list);

  const normalize = (s) => s.toLocaleLowerCase("pl");
  const close = () => { list.classList.add("hidden"); input.setAttribute("aria-expanded", "false"); active = -1; };
  const choose = (value) => {
    input.value = value;
    close();
    commit(value);
  };
  const setActive = (i) => {
    const items = list.children;
    if (items[active]) items[active].classList.remove("active");
    active = i;
    if (items[active]) {
      items[active].classList.add("active");
      items[active].scrollIntoView({ block: "nearest" });
      input.setAttribute("aria-activedescendant", items[active].id);
    } else input.removeAttribute("aria-activedescendant");
  };
  const open = (query) => {
    if (input.readOnly) return;
    const q = normalize(query.trim());
    matches = [];
    let total = 0;
    if (!q) {
      matches = options.slice(0, VIZ_COMBO_LIMIT);
      total = options.length;
    } else {
      const starts = [];
      const contains = [];
      for (const name of options) {
        const idx = normalize(name).indexOf(q);
        if (idx === 0) starts.push(name);
        else if (idx > 0) contains.push(name);
      }
      total = starts.length + contains.length;
      matches = starts.concat(contains).slice(0, VIZ_COMBO_LIMIT);
    }
    const rows = matches.map((name, i) => el("li", { id: `${listId}-${i}`, role: "option", class: "combo-option",
      onmousedown: (e) => { e.preventDefault(); choose(name); } }, name));
    if (!matches.length) rows.push(el("li", { class: "combo-empty", text: "Brak wyników" }));
    else if (total > matches.length) {
      rows.push(el("li", { class: "combo-more", text: `Pokazano ${matches.length} z ${total} — doprecyzuj wyszukiwanie` }));
    }
    list.replaceChildren(...rows);
    list.classList.remove("hidden");
    input.setAttribute("aria-expanded", "true");
    active = -1;
  };

  let timer = null;
  input.addEventListener("focus", () => open(""));
  input.addEventListener("click", () => { if (list.classList.contains("hidden")) open(""); });
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => open(input.value), 120);
    if (!input.value) commit("");
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (list.classList.contains("hidden")) open(input.value); setActive(Math.min(active + 1, matches.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(active - 1, 0)); }
    else if (e.key === "Enter" && active >= 0 && matches[active]) { e.preventDefault(); choose(matches[active]); }
    else if (e.key === "Escape") close();
  });
  input.addEventListener("blur", () => {
    close();
    // Dozwolone są tylko wartości ze słownika — dokładne dopasowanie (bez względu na wielkość liter) akceptujemy.
    const typed = input.value.trim();
    if (typed === committed) return;
    if (!typed) { commit(""); return; }
    const exact = options.find((name) => normalize(name) === normalize(typed));
    if (exact) choose(exact);
    else commit("");
  });
  return box;
}

/* ---------------- Render ---------------- */

function vizGridStyle(question) {
  const x = Number(question.x) || 0;
  const y = Number(question.y);
  const rows = Number(question.rows) || 1;
  const cols = Number(question.cols) || 1;
  if (!Number.isFinite(y)) return null;
  return `grid-area: ${y + 1} / ${x + 1} / span ${rows} / span ${cols}`;
}

function vizSortQuestions(questions) {
  return [...(questions || [])].sort((a, b) => ((a.y ?? 0) - (b.y ?? 0)) || ((a.x ?? 0) - (b.x ?? 0))
    || ((a.field?.questionOrder ?? 0) - (b.field?.questionOrder ?? 0)));
}

function vizGrid(questions) {
  const cols = Math.max(1, ...questions.map((q) => (Number(q.x) || 0) + (Number(q.cols) || 1)));
  const grid = el("div", { class: "viz-grid" });
  grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  return grid;
}

let renderToken = 0;

async function renderCurrentPage() {
  if (!viz.form) return;
  const token = ++renderToken;
  const tab = viz.order[viz.pageIdx];
  renderPageNav();
  const formEl = $("viz-form");
  if (!tab) {
    viz.nodes = [];
    formEl.replaceChildren(el("p", { class: "muted", text: "Brak stron — zaimportuj plik lub zastosuj JSON." }));
    return;
  }
  const nodes = [];
  const fragment = document.createDocumentFragment();
  const questions = vizSortQuestions(tab.questions);
  const grid = vizGrid(questions);
  for (const q of questions) {
    const node = await renderQuestion(q, nodes);
    if (token !== renderToken) return;
    grid.append(node);
  }
  fragment.append(grid);
  if (tab.type === "FINISH") fragment.append(renderPreview());
  if (token !== renderToken) return;
  viz.nodes = nodes;
  formEl.replaceChildren(fragment);
  refreshStates();
}

async function renderQuestion(question, nodes) {
  const field = question.field || {};
  const style = vizGridStyle(question);
  if (FD_SECTION_TYPES.includes(field.type)) {
    const titleId = `sec-${field.id}`;
    const sectionEl = el("section", { class: "viz-section", "aria-labelledby": titleId, style },
      el("h3", { id: titleId, text: field.title || "" }),
      field.description ? el("p", { class: "section-desc", text: field.description }) : null);
    const node = { field, wrap: sectionEl, kind: "section" };
    nodes.push(node);
    if (field.type === "REPEATING_SECTION") {
      const body = el("div", { class: "repeatable-group" });
      sectionEl.append(body);
      node.body = body;
      await renderRepeating(field, body, nodes);
    } else {
      const children = vizSortQuestions(fdChildren(field));
      const grid = vizGrid(children);
      for (const child of children) grid.append(await renderControl(child, nodes, vizStore(child.field.id)));
      sectionEl.append(grid);
    }
    return sectionEl;
  }
  /* Pole poza sekcją (niezgodne z regułą 4) — renderujemy, walidator strukturalny to zgłasza. */
  return renderControl(question, nodes, vizStore(field.id));
}

function vizStore(fieldId) {
  return { get: () => viz.values.get(fieldId), set: (v) => viz.values.set(fieldId, v), key: fieldId };
}

async function renderRepeating(section, body, nodes) {
  const entries = viz.values.get(section.id) || [];
  for (let i = nodes.length - 1; i >= 0; i--) if (nodes[i].repeatOf === section.id) nodes.splice(i, 1);
  body.replaceChildren();
  const children = vizSortQuestions(fdChildren(section));
  for (let index = 0; index < entries.length; index++) {
    const entry = entries[index];
    const entryEl = el("fieldset", { class: "repeatable-entry" },
      el("legend", { class: "repeatable-entry-head", text: `${section.title || "Wpis"} — ${index + 1}` }));
    const grid = vizGrid(children);
    for (const child of children) {
      const store = { get: () => entry[child.field.id], set: (v) => { entry[child.field.id] = v; }, key: `${child.field.id}-${index}` };
      grid.append(await renderControl(child, nodes, store, { entryIndex: index, repeatOf: section.id }));
    }
    entryEl.append(grid);
    if (entries.length > 1) {
      entryEl.append(el("button", { class: "btn btn-secondary small", type: "button", onclick: async () => {
        entries.splice(index, 1);
        await renderRepeating(section, body, nodes);
        refreshStates();
      } }, "Usuń"));
    }
    body.append(entryEl);
  }
  body.append(el("button", { class: "btn btn-secondary small", type: "button", onclick: async () => {
    entries.push(vizNewEntry(section));
    await renderRepeating(section, body, nodes);
    refreshStates();
  } }, `Dodaj kolejny wpis`));
}

function vizRequiredMark(field) {
  return fdHasValidator(field, "REQUIRED") ? el("span", { class: "required-mark", "aria-hidden": "true", text: " *" }) : null;
}

/* Kontrolka pola. `store` = get/set wartości (zwykłe pole lub komórka wpisu sekcji powtarzającej się). */
async function renderControl(question, nodes, store, ctx = {}) {
  const field = question.field || {};
  const id = `in-${store.key}`;
  const wrap = el("div", { class: `viz-field viz-${String(field.type).toLowerCase()}`, style: vizGridStyle(question), "data-tech": field.techName || "" });
  const errorEl = el("div", { class: "field-error hidden", id: `${id}-err`, role: "alert" });
  const descId = field.description ? `${id}-desc` : null;
  const descEl = field.description && !FD_PRESENTATION_TYPES.includes(field.type)
    ? el("div", { class: "help", id: descId, text: field.description }) : null;
  const describedBy = [descId, `${id}-err`].filter(Boolean).join(" ");
  const node = { field, wrap, errorEl, store, kind: "field", entryIndex: ctx.entryIndex, repeatOf: ctx.repeatOf, controls: [] };
  nodes.push(node);
  const commit = (value) => {
    store.set(value);
    viz.touched.add(store.key);
    refreshStates();
    vizShowErrors(node, vizFieldErrors(node));
  };
  const label = (forId) => el("label", { for: forId }, field.title || field.techName || "", vizRequiredMark(field), vizTooltip(field, id));
  const badge = field.defaultValueType === "EXTERNAL_SYSTEM"
    ? el("div", { class: "registry-badge", text: `Dane z rejestru: ${FD_INITIAL_DATA_LABELS[String(field.defaultValue).split(".")[0]] || field.defaultValue}` })
    : null;

  switch (field.type) {
    case "TEXT_BLOCK":
      vizAppend(wrap, field.title ? el("h4", { text: field.title }) : null, ...vizParagraphs(field.description));
      node.kind = "static";
      return wrap;
    case "INFO_SECTION":
      vizAppend(wrap, el("div", { class: "info-section", role: "note" }, field.title ? el("strong", { text: field.title }) : null,
        ...vizParagraphs(field.description)));
      node.kind = "static";
      return wrap;
    case "TEXTAREA": {
      const counter = el("div", { class: "char-counter", "aria-live": "polite" });
      const input = el("textarea", { id, maxlength: TEXTAREA_MAX, "aria-describedby": describedBy,
        oninput: (e) => { counter.textContent = `${e.target.value.length}/${TEXTAREA_MAX}`; commit(e.target.value); } });
      input.value = store.get() ?? "";
      counter.textContent = `${input.value.length}/${TEXTAREA_MAX}`;
      node.controls = [input];
      vizAppend(wrap, label(id), descEl, input, counter, badge, errorEl);
      return wrap;
    }
    case "SELECT": {
      const options = await vizOptions(field);
      if (options.length > VIZ_COMBO_THRESHOLD) {
        const input = vizCombobox(id, describedBy, options, store.get() ?? "", commit, field.title || "");
        node.controls = [input.querySelector("input")];
        vizAppend(wrap, label(`${id}`), descEl, input, badge, errorEl);
        return wrap;
      }
      const input = el("select", { id, "aria-describedby": describedBy, onchange: (e) => commit(e.target.value) },
        el("option", { value: "" }, "Wybierz"), options.map((name) => el("option", { value: name }, name)));
      input.value = store.get() ?? "";
      node.controls = [input];
      let search = null;
      if (field.searchEnabled || options.length > 15) {
        search = el("input", { type: "search", class: "select-search", placeholder: "Szukaj na liście…",
          "aria-label": `Szukaj: ${field.title || ""}`, oninput: (e) => {
            const q = e.target.value.trim().toLowerCase();
            [...input.options].forEach((o, i) => { o.hidden = i > 0 && q && !o.textContent.toLowerCase().includes(q); });
          } });
        node.controls.push(search);
      }
      const missing = field.dictionaryName && !options.length
        ? el("div", { class: "warn small", text: `Słownik „${field.dictionaryName}” nie jest dostępny offline — brak pozycji do wyboru.` }) : null;
      vizAppend(wrap, label(id), descEl, search, input, missing, badge, errorEl);
      return wrap;
    }
    case "RADIO":
    case "CHECKBOX": {
      if (field.type === "CHECKBOX" && !vizHasOptions(field)) {
        const input = el("input", { type: "checkbox", id, "aria-describedby": describedBy, onchange: (e) => commit(e.target.checked) });
        input.checked = store.get() === true;
        node.controls = [input];
        vizAppend(wrap, el("label", { class: "checkbox-label", for: id }, input, field.title || "", vizRequiredMark(field)), descEl, errorEl);
        return wrap;
      }
      const options = await vizOptions(field);
      const multi = field.type === "CHECKBOX";
      const group = el("fieldset", { class: "choice-group", "aria-describedby": describedBy },
        el("legend", {}, field.title || "", vizRequiredMark(field), vizTooltip(field, id)));
      const current = store.get();
      options.forEach((name, i) => {
        const input = el("input", { type: multi ? "checkbox" : "radio", name: `g-${store.key}`, id: `${id}-${i}`, value: name,
          onchange: () => {
            if (multi) commit([...group.querySelectorAll("input:checked")].map((x) => x.value));
            else commit(name);
          } });
        input.checked = multi ? (current || []).includes(name) : current === name;
        node.controls.push(input);
        vizAppend(group, el("label", { class: "choice", for: input.id }, input, name));
      });
      vizAppend(wrap, group, descEl, badge, errorEl);
      return wrap;
    }
    case "CONSENT_SECTION": {
      const cfg = field.consentSectionConfig || {};
      const consents = cfg.consents || [];
      const group = el("fieldset", { class: "choice-group consent-group", "aria-describedby": describedBy },
        el("legend", {}, field.title || "Zgody i oświadczenia"));
      const values = store.get() || consents.map(() => false);
      const boxes = consents.map((consent, i) => {
        const required = consent.required ?? cfg.required ?? false;
        const input = el("input", { type: "checkbox", id: `${id}-${i}`, onchange: (e) => {
          const next = [...(store.get() || consents.map(() => false))];
          next[i] = e.target.checked;
          commit(next);
        } });
        input.checked = !!values[i];
        vizAppend(group, el("label", { class: "choice consent", for: input.id }, input,
          el("span", {}, consent.description || "", required ? el("span", { class: "required-mark", text: " *" }) : null)));
        return input;
      });
      node.controls = boxes;
      if (field.selectAllEnabled ?? cfg.selectAllEnabled) {
        vizAppend(group, el("button", { class: "btn btn-secondary small", type: "button", onclick: () => {
          boxes.forEach((b) => { b.checked = true; });
          commit(consents.map(() => true));
        } }, "Zaznacz wszystkie"));
      }
      const files = (cfg.files || []).map((f) => el("li", { text: typeof f === "string" ? f : f?.name || JSON.stringify(f) }));
      vizAppend(wrap, group, files.length ? el("ul", { class: "consent-files" }, files) : null, errorEl);
      return wrap;
    }
    case "FILE_UPLOADER": {
      const list = el("ul", { class: "file-names" });
      const renderNames = () => list.replaceChildren(...(store.get() || []).map((n) => el("li", { text: n })));
      const input = el("input", { type: "file", id, class: "visually-hidden", accept: FILE_RULES.formats.replace(/\s/g, ""),
        "aria-describedby": describedBy, onchange: (e) => {
          const files = [...e.target.files];
          const errors = [];
          if (files.length > FILE_RULES.maxFiles) errors.push(`Możesz dodać maksymalnie ${FILE_RULES.maxFiles} plik.`);
          if (files.some((f) => f.size > FILE_RULES.maxBytes)) errors.push("Plik jest większy niż 10 MB.");
          if (errors.length) { vizShowErrors(node, errors); e.target.value = ""; return; }
          commit(files.map((f) => f.name));
          renderNames();
        } });
      node.controls = [input];
      const box = el("div", { class: "file-box" },
        el("label", { class: "btn btn-secondary", for: id }, "Dodaj lub przeciągnij plik"), input, list,
        el("table", { class: "file-rules" },
          el("tr", {}, el("td", { text: "Dopuszczalny format pliku:" }), el("td", { text: FILE_RULES.formats })),
          el("tr", {}, el("td", { text: "Maksymalny rozmiar pliku:" }), el("td", { text: "10 MB" })),
          el("tr", {}, el("td", { text: "Maksymalna liczba plików:" }), el("td", { text: String(FILE_RULES.maxFiles) }))));
      renderNames();
      vizAppend(wrap, el("div", { class: "field-label" }, field.title || "", vizRequiredMark(field), vizTooltip(field, id)), descEl, box, errorEl);
      return wrap;
    }
    case "NUMBER":
    case "DATEPICKER":
    case "TEXT_FIELD":
    default: {
      const type = field.type === "NUMBER" ? "text" : field.type === "DATEPICKER" ? "date" : "text";
      const attrs = { id, type, "aria-describedby": describedBy, oninput: (e) => commit(e.target.value) };
      if (field.type === "NUMBER") attrs.inputmode = "decimal";
      if (fdHasValidator(field, "ZIP_CODE")) attrs.placeholder = "__-___";
      if (/ulica/i.test(field.title || "")) attrs.placeholder = "Np. Polna, aleja Leśna, plac Lipowy, skwer Słoneczny";
      const input = el("input", attrs);
      input.value = store.get() ?? "";
      node.controls = [input];
      vizAppend(wrap, label(id), descEl, input, badge, errorEl);
      return wrap;
    }
  }
}

function vizTooltip(field, id) {
  if (!field.tooltipMessage) return null;
  const tip = el("span", { class: "tooltip-text hidden", id: `${id}-tip`, role: "tooltip", text: field.tooltipMessage });
  const btn = el("button", { type: "button", class: "tooltip-btn", "aria-label": "Pokaż podpowiedź", "aria-describedby": `${id}-tip`,
    onclick: (e) => { e.preventDefault(); tip.classList.toggle("hidden"); } }, "?");
  return el("span", { class: "tooltip" }, btn, tip);
}

function vizParagraphs(text) {
  return String(text || "").split(/\n{1,}/).filter((p) => p.trim()).map((p) => el("p", { text: p.trim() }));
}

/* ---------------- Stany, widoczność, walidacja ---------------- */

function refreshStates() {
  if (!viz.form) return;
  const model = vizBuildModel();
  const hiddenSections = new Set();
  for (const node of viz.nodes) {
    if (node.kind !== "section") continue;
    const { show } = vizEvalStates(node.field, model);
    node.visible = show;
    node.wrap.classList.toggle("hidden", !show);
    if (!show) hiddenSections.add(node.field.id);
  }
  for (const node of viz.nodes) {
    if (node.kind === "section") continue;
    const parent = viz.parentOf.get(node.field.id);
    const { show, readOnly } = vizEvalStates(node.field, model, node.entryIndex);
    node.visible = show && !(parent && hiddenSections.has(parent.id));
    node.readOnly = node.field.readOnly === true || readOnly;
    node.wrap.classList.toggle("hidden", !show);
    node.wrap.classList.toggle("readonly", node.readOnly);
    for (const control of node.controls || []) {
      if (control.tagName === "INPUT" && (control.type === "text" || control.type === "date" || control.type === "search")) control.readOnly = node.readOnly;
      else control.disabled = node.readOnly;
      control.setAttribute("aria-readonly", String(node.readOnly));
    }
  }
  const messages = $("viz-query-errors");
  if (messages) {
    messages.replaceChildren(...[...viz.queryErrors].slice(0, 5).map((m) => el("div", { class: "warn", text: `⚠ Warunek jsonata: ${m}` })));
  }
}

function vizFieldErrors(node) {
  if (node.kind !== "field" || !node.visible) return [];
  return fdValueErrors(node.field, node.store.get(), { skipRequired: node.readOnly })
    .concat(node.field.type === "CONSENT_SECTION" ? vizConsentErrors(node) : []);
}

function vizConsentErrors(node) {
  const cfg = node.field.consentSectionConfig || {};
  const values = node.store.get() || [];
  const missing = (cfg.consents || []).some((c, i) => (c.required ?? cfg.required ?? false) && !values[i]);
  return missing ? ["Zaznacz wymagane zgody i oświadczenia."] : [];
}

function vizShowErrors(node, errors) {
  node.errorEl.replaceChildren(...errors.map((e) => el("div", { text: e })));
  node.errorEl.classList.toggle("hidden", errors.length === 0);
  for (const control of node.controls || []) {
    control.classList.toggle("invalid", errors.length > 0);
    if (errors.length) control.setAttribute("aria-invalid", "true");
    else control.removeAttribute("aria-invalid");
  }
}

function validateCurrentPage() {
  let firstInvalid = null;
  for (const node of viz.nodes) {
    if (node.kind !== "field") continue;
    const errors = vizFieldErrors(node);
    vizShowErrors(node, errors);
    if (errors.length && !firstInvalid) firstInvalid = node;
  }
  if (firstInvalid) firstInvalid.controls?.[0]?.focus();
  return !firstInvalid;
}

/* ---------------- Nawigacja ---------------- */

function renderPageNav() {
  const nav = $("viz-pages");
  nav.replaceChildren();
  viz.order.forEach((tab, index) => {
    nav.append(el("button", {
      class: `viz-page-btn ${index === viz.pageIdx ? "active" : ""}`, type: "button",
      "aria-current": index === viz.pageIdx ? "step" : null,
      onclick: () => { if (index <= viz.pageIdx || validateCurrentPage()) { viz.pageIdx = index; renderCurrentPage(); } },
    }, `${index + 1}. ${tab.title || "Strona"}`));
  });
  const tab = viz.order[viz.pageIdx];
  const last = viz.pageIdx >= viz.order.length - 1;
  $("btn-viz-prev").classList.toggle("hidden", viz.pageIdx === 0);
  $("btn-viz-next").classList.toggle("hidden", last);
  $("btn-viz-download").classList.toggle("hidden", !(tab?.type === "FINISH"));
}

function vizNext() {
  if (!validateCurrentPage()) {
    $("viz-messages").replaceChildren(el("div", { class: "err", role: "alert", text: "Popraw zaznaczone pola, aby przejść dalej." }));
    return;
  }
  $("viz-messages").replaceChildren();
  viz.pageIdx = Math.min(viz.pageIdx + 1, viz.order.length - 1);
  renderCurrentPage().then(() => $("viz-form").scrollIntoView({ block: "start" }));
}

function vizPrev() {
  $("viz-messages").replaceChildren();
  viz.pageIdx = Math.max(0, viz.pageIdx - 1);
  renderCurrentPage();
}

function vizAbort() {
  if (!viz.form) return;
  initVisualizer(viz.form);
  toast("Przerwano wypełnianie — wyczyszczono odpowiedzi.", "info");
}

/* ---------------- Podgląd i dane wniosku ---------------- */

function vizDisplay(field, value) {
  if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) return null;
  if (field.type === "CONSENT_SECTION") {
    const consents = field.consentSectionConfig?.consents || [];
    return consents.filter((_, i) => value[i]).map((c) => `✓ ${c.description}`).join("\n") || null;
  }
  if (value === true) return "Tak";
  if (value === false) return null;
  return Array.isArray(value) ? value.join(", ") : String(value);
}

/* Odpowiedzi z widocznych pól stron MAIN (wg tych samych stanów co w formularzu). */
function vizCollectAnswers() {
  const model = vizBuildModel();
  const pages = [];
  for (const tab of viz.order) {
    if (tab.type === "FINISH") continue;
    const sections = [];
    for (const q of vizSortQuestions(tab.questions)) {
      const section = q.field;
      if (!FD_SECTION_TYPES.includes(section?.type) || !vizEvalStates(section, model).show) continue;
      const children = vizSortQuestions(fdChildren(section)).map((c) => c.field)
        .filter((f) => !FD_PRESENTATION_TYPES.includes(f.type));
      if (section.type === "REPEATING_SECTION") {
        const entries = (viz.values.get(section.id) || []).map((entry, i) => children
          .filter((f) => vizEvalStates(f, model, i).show)
          .map((f) => ({ techName: f.techName, title: f.title, value: entry[f.id], display: vizDisplay(f, entry[f.id]) })));
        sections.push({ techName: section.techName, title: section.title, repeating: true, entries });
      } else {
        const fields = children.filter((f) => vizEvalStates(f, model).show)
          .map((f) => ({ techName: f.techName, title: f.title, value: viz.values.get(f.id), display: vizDisplay(f, viz.values.get(f.id)) }));
        sections.push({ techName: section.techName, title: section.title, fields });
      }
    }
    pages.push({ title: tab.title, sections });
  }
  return pages;
}

function renderPreview() {
  const pages = vizCollectAnswers();
  const box = el("section", { class: "viz-section preview", "aria-labelledby": "preview-title" },
    el("h3", { id: "preview-title", text: "Podgląd wniosku" }));
  const row = (f) => el("div", { class: "preview-row" }, el("dt", { text: f.title || f.techName }),
    el("dd", { text: f.display ?? "—" }));
  for (const page of pages) {
    vizAppend(box, el("h4", { text: page.title }));
    for (const section of page.sections) {
      const dl = el("dl", { class: "preview-list" });
      if (section.repeating) section.entries.forEach((entry, i) => {
        dl.append(el("div", { class: "preview-entry", text: `Wpis ${i + 1}` }), ...entry.map(row));
      });
      else dl.append(...section.fields.map(row));
      vizAppend(box, el("div", { class: "preview-section" }, el("h5", { text: section.title }), dl));
    }
  }
  return box;
}

function vizDownloadAnswers() {
  const payload = { title: viz.form.title, generatedAt: new Date().toISOString(),
    answers: vizCollectAnswers().map((p) => ({ tab: p.title, sections: p.sections.map((s) => (s.repeating
      ? { techName: s.techName, title: s.title, entries: s.entries.map((e) => Object.fromEntries(e.map((f) => [f.techName, f.value]))) }
      : { techName: s.techName, title: s.title, answers: Object.fromEntries(s.fields.map((f) => [f.techName, f.value])) })) })) };
  downloadBlob(JSON.stringify(payload, null, 2), `wniosek-${jsonFilename(viz.form.title)}`);
}
