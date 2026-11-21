/* COI Form Digitizer — model FormDefinition (oficjalny format importu e-Usług COI).
 * [OWNERSHIP: Branch 2] — frontend/.
 *
 * Źródła wymagań (materiały konkursowe):
 *  - importowanyPlikJsonSchemat.json + OpisImportowanegoPlikuJSON.docx (struktura JSON),
 *  - "Reguły techniczne konfiguracji formularzy i pól" (reguły 1–12, typy pól, walidatory, jsonata),
 *  - "Słowniki" (6 predefiniowanych słowników), "Dane inicjalne" (readOnly + EXTERNAL_SYSTEM),
 *  - "Standardy i dobre praktyki projektowania formularzy" (sekcje predefiniowane, strona końcowa).
 *
 * Zawiera: katalogi (typy, walidatory, słowniki, źródła danych inicjalnych), walidatory wartości,
 * konwerter formatu wewnętrznego backendu (metadata/pages/sections/fields) → FormDefinition
 * oraz walidator strukturalny FormDefinition względem reguł konkursu.
 */
"use strict";

/* ====================== Katalogi ====================== */

const FD_TAB_TYPES = ["MAIN", "FINISH"];
const FD_SECTION_TYPES = ["SECTION", "REPEATING_SECTION"];
const FD_FIELD_TYPES = [
  "TEXT_FIELD", "CHECKBOX", "NUMBER", "CONSENT_SECTION", "SELECT", "TEXTAREA",
  "FILE_UPLOADER", "RADIO", "DATEPICKER", "TEXT_BLOCK", "INFO_SECTION",
];
/* Pola prezentacyjne — bez wprowadzania danych (bez atrybutu obligatoryjności). */
const FD_PRESENTATION_TYPES = ["TEXT_BLOCK", "INFO_SECTION"];
const FD_CHOICE_TYPES = ["SELECT", "RADIO", "CHECKBOX"];
const FD_STATE_TYPES_FIELD = ["SHOW", "READ_ONLY"];
const FD_STATE_TYPES_SECTION = ["SHOW"];

const FD_VALIDATORS = [
  "REQUIRED", "EMAIL", "PESEL", "NIP", "REGON", "ZIP_CODE", "EPUAP", "ADE", "SERIAL_NUMBER_ID_CARD",
];
/* Typ pola → walidatory, które można na nim ustawić (Reguły techniczne, specyfikacja komponentów). */
const FD_VALIDATORS_BY_TYPE = {
  TEXT_FIELD: FD_VALIDATORS,
  TEXTAREA: ["REQUIRED"],
  NUMBER: ["REQUIRED"],
  SELECT: ["REQUIRED"],
  RADIO: ["REQUIRED"],
  CHECKBOX: ["REQUIRED"],
  DATEPICKER: ["REQUIRED"],
  FILE_UPLOADER: ["REQUIRED"],
  CONSENT_SECTION: ["REQUIRED"],
};

const FD_VOIVODESHIPS = [
  "dolnośląskie", "kujawsko-pomorskie", "lubelskie", "lubuskie", "łódzkie", "małopolskie",
  "mazowieckie", "opolskie", "podkarpackie", "podlaskie", "pomorskie", "śląskie",
  "świętokrzyskie", "warmińsko-mazurskie", "wielkopolskie", "zachodniopomorskie",
];
const FD_COUNTRIES = [
  "Polska", "Afganistan", "Albania", "Algieria", "Andora", "Angola", "Arabia Saudyjska", "Argentyna",
  "Armenia", "Australia", "Austria", "Azerbejdżan", "Bangladesz", "Belgia", "Białoruś", "Boliwia",
  "Bośnia i Hercegowina", "Brazylia", "Bułgaria", "Chile", "Chiny", "Chorwacja", "Cypr", "Czarnogóra",
  "Czechy", "Dania", "Egipt", "Ekwador", "Estonia", "Etiopia", "Filipiny", "Finlandia", "Francja",
  "Grecja", "Gruzja", "Hiszpania", "Holandia", "Indie", "Indonezja", "Irak", "Iran", "Irlandia",
  "Islandia", "Izrael", "Japonia", "Jordania", "Kanada", "Kazachstan", "Kenia", "Kolumbia",
  "Korea Południowa", "Kosowo", "Kuba", "Liban", "Liechtenstein", "Litwa", "Luksemburg", "Łotwa",
  "Macedonia Północna", "Malezja", "Malta", "Maroko", "Meksyk", "Mołdawia", "Monako", "Mongolia",
  "Niemcy", "Nigeria", "Norwegia", "Nowa Zelandia", "Pakistan", "Peru", "Portugalia",
  "Republika Południowej Afryki", "Rosja", "Rumunia", "San Marino", "Serbia", "Singapur", "Słowacja",
  "Słowenia", "Stany Zjednoczone", "Syria", "Szwajcaria", "Szwecja", "Tajlandia", "Tunezja", "Turcja",
  "Ukraina", "Urugwaj", "Uzbekistan", "Watykan", "Wenezuela", "Węgry", "Wielka Brytania", "Wietnam",
  "Włochy", "Zjednoczone Emiraty Arabskie",
];
const FD_DIAL_CODES = [
  "+48 Polska", "+49 Niemcy", "+420 Czechy", "+421 Słowacja", "+380 Ukraina", "+375 Białoruś",
  "+370 Litwa", "+371 Łotwa", "+372 Estonia", "+7 Rosja", "+43 Austria", "+41 Szwajcaria",
  "+32 Belgia", "+31 Holandia", "+352 Luksemburg", "+33 Francja", "+34 Hiszpania", "+351 Portugalia",
  "+39 Włochy", "+30 Grecja", "+36 Węgry", "+40 Rumunia", "+359 Bułgaria", "+385 Chorwacja",
  "+386 Słowenia", "+381 Serbia", "+45 Dania", "+46 Szwecja", "+47 Norwegia", "+358 Finlandia",
  "+353 Irlandia", "+44 Wielka Brytania", "+354 Islandia", "+1 Stany Zjednoczone / Kanada",
  "+61 Australia", "+64 Nowa Zelandia", "+81 Japonia", "+86 Chiny", "+91 Indie", "+90 Turcja",
  "+972 Izrael", "+971 Zjednoczone Emiraty Arabskie", "+55 Brazylia", "+52 Meksyk", "+54 Argentyna",
];

/* 6 słowników predefiniowanych z materiału "Słowniki". `backendId` = identyfikator w lokalnej bazie COI
 * (GET /api/v1/dictionaries/{id}); `items` = dane wbudowane, gdy backend ich nie ma (praca offline). */
const FD_DICTIONARIES = [
  { name: "Województwo", aliases: ["WOJEWODZTWO", "WOJEWODZTWA", "dict_voivodeships"], allowedTypes: ["SELECT"],
    backendId: "dict_voivodeships", items: FD_VOIVODESHIPS, group: "TERYT" },
  { name: "Powiat", aliases: ["POWIAT", "POWIATY", "dict_districts"], allowedTypes: ["SELECT"],
    backendId: "dict_districts", items: null, group: "TERYT" },
  { name: "Gmina", aliases: ["GMINA", "GMINY", "dict_communities"], allowedTypes: ["SELECT"],
    backendId: "dict_communities", items: null, group: "TERYT" },
  { name: "Miejscowość", aliases: ["MIEJSCOWOSC", "MIEJSCOWOSCI", "TERYT_4G_DOWODOWY", "dict_places"],
    allowedTypes: ["SELECT"], backendId: "dict_places", items: null, group: "TERYT" },
  { name: "Kraje", aliases: ["KRAJE", "KRAJ", "PANSTWA", "dict_countries"], allowedTypes: ["SELECT"],
    backendId: "dict_countries", items: FD_COUNTRIES },
  { name: "Numery kierunkowe", aliases: ["NUMERY_KIERUNKOWE", "NUMERY KIERUNKOWE", "dict_dial_codes"],
    allowedTypes: ["SELECT", "RADIO", "CHECKBOX"], backendId: "dict_dial_codes", items: FD_DIAL_CODES },
];

/* Dane inicjalne: źródło → dozwolone atrybuty (materiał "Dane inicjalne"). */
const FD_INITIAL_DATA = {
  PESEL: ["pesel", "name", "surname", "birthDate", "citizenship", "motherName", "birthPlace", "motherSurname",
    "sex", "fatherName", "secondName", "birthCountry", "foreignBirthUSC", "familyName", "fatherSurname",
    "maritalStatus"],
  PESEL_ADDRESS: ["temporaryAddressVoivodeship", "temporaryAddressDistrict", "temporaryAddressCommunity",
    "temporaryAddressPlace", "temporaryAddressStreetType", "temporaryAddressStreet",
    "temporaryAddressHouseNumber", "temporaryAddressApartmentNumber", "temporaryAddressPostalCode",
    "permanentAddressVoivodeship", "permanentAddressDistrict", "permanentAddressCommunity",
    "permanentAddressPlace", "permanentAddressStreetType", "permanentAddressStreet",
    "permanentAddressPostalCode", "permanentAddressHouseNumber", "permanentAddressApartmentNumber"],
  MLPZ: ["name", "secondName", "surname", "pesel", "phoneNumber", "email", "country", "voivodeship", "district",
    "community", "place", "postCode", "street", "building", "apartment", "institutionName",
    "descriptiveOrganizationName"],
};
const FD_INITIAL_DATA_LABELS = { PESEL: "Rejestr PESEL", PESEL_ADDRESS: "Dane zameldowania", MLPZ: "Dane zalogowanego użytkownika" };

/* Przykładowe dane rejestrów — wizualizator nie łączy się z rejestrami (100% offline). */
const FD_SAMPLE_REGISTRY = {
  "MLPZ.name": "JAN", "MLPZ.surname": "KOWALSKI", "MLPZ.pesel": "44051401458", "MLPZ.phoneNumber": "600100200",
  "MLPZ.email": "jan.kowalski@example.pl", "MLPZ.country": "Polska", "MLPZ.voivodeship": "mazowieckie",
  "MLPZ.community": "Warszawa", "MLPZ.place": "Warszawa", "MLPZ.postCode": "00-950", "MLPZ.street": "Polna",
  "MLPZ.building": "12", "MLPZ.apartment": "4",
  "PESEL.pesel": "44051401458", "PESEL.name": "JAN", "PESEL.surname": "KOWALSKI", "PESEL.birthDate": "1944-05-14",
  "PESEL.citizenship": "polskie", "PESEL.sex": "mężczyzna", "PESEL.birthPlace": "Warszawa",
  "PESEL_ADDRESS.permanentAddressVoivodeship": "mazowieckie", "PESEL_ADDRESS.permanentAddressPlace": "Warszawa",
  "PESEL_ADDRESS.permanentAddressStreet": "Polna", "PESEL_ADDRESS.permanentAddressHouseNumber": "12",
  "PESEL_ADDRESS.permanentAddressPostalCode": "00-950",
};

const FD_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/* ====================== Narzędzia ====================== */

function fdNormalize(text) {
  return String(text ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/ł/g, "l").replace(/Ł/g, "L")
    .trim().toUpperCase().replace(/[\s-]+/g, "_");
}

function fdFindDictionary(name) {
  if (!name) return null;
  const key = fdNormalize(name);
  return FD_DICTIONARIES.find((d) => fdNormalize(d.name) === key || d.aliases.some((a) => fdNormalize(a) === key)) || null;
}

function fdSlug(text, fallback = "pole") {
  const slug = String(text ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[łŁ]/g, "l")
    .replace(/[^a-zA-Z0-9]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase().slice(0, 60);
  return slug || fallback;
}

/* Deterministyczny UUID (wariant RFC 4122, wersja 4-kształtna) z ziarna — stabilne id przy ponownym eksporcie. */
function fdUuid(seed) {
  let h1 = 1779033703, h2 = 3144134277, h3 = 1013904242, h4 = 2773480762;
  const str = String(seed);
  for (let i = 0; i < str.length; i++) {
    const k = str.charCodeAt(i);
    h1 = h2 ^ Math.imul(h1 ^ k, 597399067);
    h2 = h3 ^ Math.imul(h2 ^ k, 2869860233);
    h3 = h4 ^ Math.imul(h3 ^ k, 951274213);
    h4 = h1 ^ Math.imul(h4 ^ k, 2716044179);
  }
  h1 = Math.imul(h3 ^ (h1 >>> 18), 597399067);
  h2 = Math.imul(h4 ^ (h2 >>> 22), 2869860233);
  h3 = Math.imul(h1 ^ (h3 >>> 17), 951274213);
  h4 = Math.imul(h2 ^ (h4 >>> 19), 2716044179);
  const hex = [h1 ^ h2 ^ h3 ^ h4, h2 ^ h1, h3 ^ h1, h4 ^ h1].map((n) => (n >>> 0).toString(16).padStart(8, "0")).join("");
  const variant = ((parseInt(hex[16], 16) & 0x3) | 0x8).toString(16);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-${variant}${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

/* Lista pytań-dzieci sekcji (schemat: repetingSectionGrid; przykłady jsonaty: repeatingSectionGrid). */
function fdChildren(field) {
  if (!field) return [];
  return field.repetingSectionGrid || field.repeatingSectionGrid || [];
}

function fdIsFormDefinition(obj) {
  return !!obj && typeof obj === "object" && !Array.isArray(obj) && Array.isArray(obj.tabs);
}

function fdIsInternalForm(obj) {
  return !!obj && typeof obj === "object" && Array.isArray(obj.pages);
}

/* Normalizacja wpisu walidacji: string ("PESEL") lub obiekt ({type|name: "PESEL", ...}). */
function fdValidatorName(entry) {
  if (typeof entry === "string") return entry.trim().toUpperCase();
  if (entry && typeof entry === "object") return String(entry.type || entry.name || "").trim().toUpperCase();
  return "";
}

function fdHasValidator(field, name) {
  return (field?.validation || []).some((v) => fdValidatorName(v) === name);
}

/* Iteracja po wszystkich polach: cb({field, question, tab, tabIndex, path, parent, depth}). */
function fdWalk(form, cb) {
  (form?.tabs || []).forEach((tab, ti) => {
    (tab?.questions || []).forEach((q, qi) => {
      const path = `tabs.${ti}.questions.${qi}`;
      cb({ field: q?.field, question: q, tab, tabIndex: ti, path, parent: null, depth: 0 });
      const key = q?.field?.repetingSectionGrid ? "repetingSectionGrid" : "repeatingSectionGrid";
      fdChildren(q?.field).forEach((child, ci) => {
        cb({ field: child?.field, question: child, tab, tabIndex: ti, path: `${path}.field.${key}.${ci}`,
          parent: q?.field, depth: 1 });
        fdChildren(child?.field).forEach((g, gi) => cb({ field: g?.field, question: g, tab, tabIndex: ti,
          path: `${path}.field.${key}.${ci}.field.${key}.${gi}`, parent: child?.field, depth: 2 }));
      });
    });
  });
}

/* Kolejność stron wg flow.links (start = strona MAIN, do której nie prowadzi żadne przejście). */
function fdTabOrder(form) {
  const tabs = form?.tabs || [];
  const links = form?.flow?.links || [];
  if (!links.length) return [...tabs];
  const byId = new Map(tabs.map((t) => [t.id, t]));
  const targets = new Set(links.map((l) => l.target));
  let current = tabs.find((t) => !targets.has(t.id) && t.type !== "FINISH") || tabs[0];
  const order = [];
  const seen = new Set();
  while (current && !seen.has(current.id)) {
    order.push(current);
    seen.add(current.id);
    const next = links.find((l) => l.source === current.id && byId.has(l.target) && !seen.has(l.target));
    current = next ? byId.get(next.target) : null;
  }
  return order;
}

/* ====================== Walidatory wartości ====================== */

function fdPeselErrors(value) {
  const digits = value.trim();
  if (!/^\d{11}$/.test(digits)) return ["PESEL musi składać się z 11 cyfr."];
  const weights = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3];
  const sum = weights.reduce((acc, w, i) => acc + Number(digits[i]) * w, 0);
  const errors = [];
  if ((10 - (sum % 10)) % 10 !== Number(digits[10])) errors.push("Nieprawidłowa suma kontrolna PESEL.");
  const yy = Number(digits.slice(0, 2));
  const mm = Number(digits.slice(2, 4));
  const dd = Number(digits.slice(4, 6));
  const century = { 0: 1900, 1: 2000, 2: 2100, 3: 2200, 4: 1800 }[Math.floor(mm / 20)];
  if (century === undefined) errors.push("Nieprawidłowy miesiąc w numerze PESEL.");
  else {
    const year = century + yy;
    const month = mm % 20;
    const date = new Date(Date.UTC(year, month - 1, dd));
    if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== dd) {
      errors.push("Nieprawidłowa data urodzenia w numerze PESEL.");
    }
  }
  return errors;
}

function fdNipErrors(value) {
  const digits = value.replace(/[\s-]/g, "");
  if (!/^\d{10}$/.test(digits)) return ["NIP musi składać się z 10 cyfr."];
  const weights = [6, 5, 7, 2, 3, 4, 5, 6, 7];
  const sum = weights.reduce((acc, w, i) => acc + Number(digits[i]) * w, 0) % 11;
  return sum === 10 || sum !== Number(digits[9]) ? ["Nieprawidłowa suma kontrolna NIP."] : [];
}

function fdRegonErrors(value) {
  const digits = value.replace(/[\s-]/g, "");
  let weights;
  if (/^\d{9}$/.test(digits)) weights = [8, 9, 2, 3, 4, 5, 6, 7];
  else if (/^\d{14}$/.test(digits)) weights = [2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8];
  else return ["REGON musi składać się z 9 lub 14 cyfr."];
  const sum = weights.reduce((acc, w, i) => acc + Number(digits[i]) * w, 0) % 11;
  return sum === 10 || sum !== Number(digits[digits.length - 1]) ? ["Nieprawidłowa suma kontrolna REGON."] : [];
}

/* Seria i numer dowodu osobistego: 3 litery + 6 cyfr, cyfra kontrolna na 4. pozycji (wagi 7,3,1,*,7,3,1,7,3). */
function fdIdCardErrors(value) {
  const text = value.replace(/\s/g, "").toUpperCase();
  if (!/^[A-Z]{3}\d{6}$/.test(text)) return ["Wpisz serię i numer dowodu: 3 litery i 6 cyfr, np. ABA300000."];
  const weights = [7, 3, 1, 0, 7, 3, 1, 7, 3];
  const num = (ch) => (/\d/.test(ch) ? Number(ch) : ch.charCodeAt(0) - 55);
  const sum = [...text].reduce((acc, ch, i) => acc + num(ch) * weights[i], 0);
  return sum % 10 === Number(text[3]) ? [] : ["Nieprawidłowa seria i numer dowodu osobistego (cyfra kontrolna)."];
}

const FD_VALUE_VALIDATORS = {
  EMAIL: (v) => (/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v.trim()) ? [] : ["Wpisz poprawny adres e-mail, np. jan@example.pl."]),
  PESEL: fdPeselErrors,
  NIP: fdNipErrors,
  REGON: fdRegonErrors,
  ZIP_CODE: (v) => (/^\d{2}-\d{3}$/.test(v.trim()) ? [] : ["Wpisz kod pocztowy w formacie 00-000."]),
  EPUAP: (v) => (/^\/[^/\s]+\/[^/\s]+$/.test(v.trim()) ? [] : ["Wpisz adres skrytki ePUAP w formacie /nazwa_urzedu/SkrytkaESP."]),
  ADE: (v) => (/^AE:PL-\d{5}-\d{5}-[A-Z]{5}-\d{2}$/.test(v.trim().toUpperCase())
    ? [] : ["Wpisz adres do e-Doręczeń w formacie AE:PL-12345-67890-ABCDE-12."]),
  SERIAL_NUMBER_ID_CARD: fdIdCardErrors,
  /* Zgodność wstecz z formatem wewnętrznym backendu. */
  POSTAL_CODE: (v) => FD_VALUE_VALIDATORS.ZIP_CODE(v),
  PL_PHONE: (v) => (/^(?:\+48)?\d{9}$/.test(v.replace(/[\s\-()]/g, "")) ? [] : ["Numer telefonu musi mieć 9 cyfr."]),
};

/* Błędy wartości pola wg tablicy `validation`. `value` = wartość z kontrolki (string/bool/array). */
function fdValueErrors(field, value, { skipRequired = false } = {}) {
  const empty = value === undefined || value === null || value === false
    || (typeof value === "string" && value.trim() === "") || (Array.isArray(value) && value.length === 0);
  const errors = [];
  if (empty) {
    if (!skipRequired && fdHasValidator(field, "REQUIRED")) {
      errors.push(field.type === "FILE_UPLOADER" ? "Dodaj plik." : field.type === "CHECKBOX" || field.type === "RADIO"
        || field.type === "SELECT" ? "Wybierz odpowiedź." : "To pole jest wymagane.");
    }
    return errors;
  }
  if (field.type === "NUMBER" && !Number.isFinite(Number(String(value).replace(",", ".")))) {
    errors.push("Wpisz liczbę.");
  }
  for (const entry of field.validation || []) {
    const name = fdValidatorName(entry);
    if (name === "REQUIRED" || typeof value !== "string") continue;
    if (name === "PATTERN" && entry && typeof entry === "object") {
      try { if (!new RegExp(entry.value || entry.pattern).test(value)) errors.push(entry.message || "Wartość ma nieprawidłowy format."); }
      catch { /* błędny regex w konfiguracji */ }
      continue;
    }
    const check = FD_VALUE_VALIDATORS[name];
    if (check) errors.push(...check(value));
  }
  return errors;
}

/* ====================== Konwerter: format wewnętrzny → FormDefinition ====================== */

const FD_TYPE_MAP = {
  text: "TEXT_FIELD", number: "NUMBER", date: "DATEPICKER", select: "SELECT", radio: "RADIO",
  checkbox: "CHECKBOX", textarea: "TEXTAREA", file: "FILE_UPLOADER",
};
const FD_CUSTOM_VALIDATOR_MAP = { PESEL: "PESEL", NIP: "NIP", REGON: "REGON", POSTAL_CODE: "ZIP_CODE" };
const FD_INTERNAL_DICT_MAP = { dict_voivodeships: "Województwo" };

/* Heurystyki etykiet → walidatory (Standardy: Twoje dane, Dane podmiotu, Adres, Dane do kontaktu). */
const FD_LABEL_VALIDATORS = [
  [/pesel/i, "PESEL"], [/\bnip\b/i, "NIP"], [/\bregon\b/i, "REGON"], [/kod pocztowy/i, "ZIP_CODE"],
  [/e-?mail/i, "EMAIL"], [/epuap|skrytk/i, "EPUAP"], [/e-?dor[eę]cze|\bade\b/i, "ADE"],
  [/seria i numer dowodu|numer dowodu osobistego/i, "SERIAL_NUMBER_ID_CARD"],
];
/* Heurystyki etykiet → słowniki predefiniowane. */
const FD_LABEL_DICTIONARIES = [
  [/^kraj\b|^państwo\b|obywatelstwo|kraj urodzenia|kraj zamieszkania/i, "Kraje"],
  [/^województwo/i, "Województwo"], [/^powiat/i, "Powiat"], [/^gmina/i, "Gmina"],
  [/numer kierunkowy|prefiks telefon/i, "Numery kierunkowe"],
];
/* Sekcja "Twoje dane": dane wnioskodawcy z MLPZ, tylko do odczytu (Standardy, s. 10). */
const FD_OWN_DATA_SECTION = /twoje dane|dane wnioskodawcy|dane osobowe wnioskodawcy|dane osoby sk[łl]adaj/i;
const FD_OWN_DATA_FIELDS = [[/^imi[eę]( pierwsze)?$/i, "MLPZ.name"], [/^nazwisko$/i, "MLPZ.surname"],
  [/pesel/i, "MLPZ.pesel"]];
const FD_CONSENT_LABEL = /o[śs]wiadczam|wyra[żz]am zgod|zapozna[łl]|akceptuj|zgod[ay] na/i;

function fdQuote(text) { return JSON.stringify(String(text)); }

/* Ścieżka jsonata do pola: tabs[...].questions[...].field.repetingSectionGrid[...].field */
function fdFieldPath(ref) {
  return `tabs[${fdQuote(ref.tabTitle)} in title].questions[${fdQuote(ref.sectionTech)} in field.techName]`
    + `.field.repetingSectionGrid[${fdQuote(ref.techName)} in field.techName].field`;
}

function fdConditionQuery(flow, source) {
  const path = `${fdFieldPath(source)}.__userAnswer`;
  const optionName = (code) => {
    const hit = (source.options || []).find((o) => o.code === String(code) || o.label === String(code));
    return hit ? hit.label : String(code);
  };
  const values = Array.isArray(flow.value) ? flow.value : [flow.value];
  switch (flow.operator) {
    case "EQUALS":
      if (source.type === "CHECKBOX" && !source.options?.length) return `${path} = ${flow.value === true || flow.value === "true"}`;
      return `${fdQuote(optionName(flow.value))} in ${path}`;
    case "NOT_EQUALS": return `$not(${fdQuote(optionName(flow.value))} in ${path})`;
    case "IN": return `(${values.map((v) => `${fdQuote(optionName(v))} in ${path}`).join(" or ")})`;
    case "IS_EMPTY": return `($not($exists(${path})) or ${path} = "")`;
    case "GREATER_THAN": return `$number(${path}) > ${Number(flow.value)}`;
    default: return null;
  }
}

/**
 * Konwertuje JSON backendu (FormDocumentSchema: metadata/pages/sections/fields) do FormDefinition.
 * Zwraca { form, notes } — notes = lista komunikatów (co zmapowano heurystycznie, czego nie da się wyrazić).
 */
function fdConvertInternal(internal, { knownDictionaries = [] } = {}) {
  const notes = [];
  const seed = internal?.metadata?.form_id || internal?.metadata?.title || "form";
  const uid = (key) => fdUuid(`${seed}:${key}`);
  const usedTech = new Set();
  const uniqueTech = (base) => {
    let tech = fdSlug(base);
    let n = 2;
    while (usedTech.has(tech)) tech = `${fdSlug(base)}_${n++}`;
    usedTech.add(tech);
    return tech;
  };
  const proposedIds = new Set((internal?.dictionaries?.proposed_dictionaries || []).map((p) => p.proposed_id));
  const mappedNames = new Map((internal?.dictionaries?.mapped_dictionaries || []).map((m) => [m.dictionary_id, m.dictionary_name]));

  const title = String(internal?.metadata?.title || "Formularz").slice(0, 255);
  if ((internal?.metadata?.title || "").length > 255) notes.push("Tytuł przycięto do 255 znaków (reguła 1).");

  /* Rejestr pól (id wewnętrzne → referencja do ścieżki jsonata) wypełniany w trakcie konwersji. */
  const droppedRules = [];
  const refs = new Map();
  const sectionRefs = new Map();
  const pendingStates = [];
  let questionOrder = 0;

  const convertField = (f, ctx, y) => {
    let type = FD_TYPE_MAP[f.field_type] || "TEXT_FIELD";
    const label = f.label || f.name || "Pole";
    const validation = [];
    if (f.required) validation.push("REQUIRED");
    const custom = f.validation_rules?.custom_validator;
    if (custom && FD_CUSTOM_VALIDATOR_MAP[custom]) validation.push(FD_CUSTOM_VALIDATOR_MAP[custom]);
    else if (custom) notes.push(`Pole „${label}”: walidator ${custom} nie występuje w katalogu COI — pominięto.`);
    if (f.validation_rules?.pattern || f.validation_rules?.min_length || f.validation_rules?.max_length) {
      droppedRules.push(label);
    }
    if (type === "TEXT_FIELD") {
      for (const [re, name] of FD_LABEL_VALIDATORS) if (re.test(label) && !validation.includes(name)) validation.push(name);
    }

    let dictionaryName;
    let options = [];
    const dictRef = f.dictionary_ref;
    const official = dictRef ? (fdFindDictionary(dictRef) || fdFindDictionary(FD_INTERNAL_DICT_MAP[dictRef])) : null;
    if (official) {
      dictionaryName = official.name;
    } else if (!dictRef && (type === "TEXT_FIELD" || type === "SELECT")) {
      const hit = FD_LABEL_DICTIONARIES.find(([re]) => re.test(label));
      if (hit) {
        dictionaryName = hit[1];
        if (type !== "SELECT") notes.push(`Pole „${label}”: zamieniono na listę rozwijaną ze słownikiem ${hit[1]}.`);
        type = "SELECT";
      }
    }
    if (!dictionaryName && FD_CHOICE_TYPES.includes(type)) {
      let items = f.options || [];
      if (!items.length && dictRef) {
        const proposed = (internal?.dictionaries?.proposed_dictionaries || []).find((p) => p.proposed_id === dictRef);
        const known = knownDictionaries.find((d) => d.dictionary_id === dictRef);
        items = proposed?.items || known?.items || [];
        if (!items.length) notes.push(`Pole „${label}”: słownik ${dictRef} nie należy do słowników COI i nie ma pozycji.`);
        else notes.push(`Pole „${label}”: słownik spoza katalogu COI (${mappedNames.get(dictRef) || dictRef}) wstawiono jako opcje pola.`);
      } else if (dictRef && proposedIds.has(dictRef)) {
        notes.push(`Pole „${label}”: proponowany słownik ${dictRef} wstawiono jako opcje pola (w COI go nie ma).`);
      }
      options = items.map((o) => ({ id: uid(`opt:${f.field_id}:${o.code}`), name: o.label, techName: fdSlug(o.code, "opcja") }));
    }
    if (type === "SELECT" && dictionaryName === "Numery kierunkowe") { /* dozwolone */ }

    const out = {
      id: uid(`field:${f.field_id}`),
      type,
      techName: uniqueTech(f.name || f.field_id || label),
      title: label,
      description: f.help_text || "",
      options,
      validation,
      readOnly: false,
      defaultValue: f.initial_value === null || f.initial_value === undefined ? "" : String(f.initial_value),
      defaultValueType: "",
      states: [],
      questionOrder: ++questionOrder,
      tooltipMessage: f.placeholder ? `Np. ${String(f.placeholder).replace(/^np\.\s*/i, "")}` : "",
    };
    if (dictionaryName) out.dictionaryName = dictionaryName;
    const dictSize = fdFindDictionary(dictionaryName)?.items?.length ?? (dictionaryName ? 99 : 0);
    if (type === "SELECT" && (dictSize > 15 || options.length > 15)) out.searchEnabled = true;
    if (dictionaryName === "Kraje" && !out.defaultValue) out.defaultValue = "Polska";

    if ((FD_OWN_DATA_SECTION.test(ctx.sectionTitle) || FD_OWN_DATA_SECTION.test(ctx.tabTitle)) && ctx.tabIndex === 0
      && !/innej osoby|pe[łl]nomocnik|podmiot/i.test(ctx.sectionTitle)) {
      const hit = FD_OWN_DATA_FIELDS.find(([re]) => re.test(label));
      if (hit) {
        out.readOnly = true;
        out.defaultValue = hit[1];
        out.defaultValueType = "EXTERNAL_SYSTEM";
        notes.push(`Pole „${label}”: dane inicjalne ${hit[1]} (sekcja „Twoje dane”, tylko do odczytu).`);
      }
    }
    refs.set(f.field_id, { tabTitle: ctx.tabTitle, sectionTech: ctx.sectionTech, techName: out.techName, type,
      options: (f.options || []), out });
    pendingStates.push({ target: out, rules: f.dependencies || [], kind: "FIELD", id: f.field_id });
    return { x: 0, y, rows: 1, cols: 1, field: out };
  };

  const makeSection = (type, sectionTitle, tech, description, ctx, y) => ({
    x: 0, y, rows: 1, cols: 1,
    field: {
      id: uid(`section:${tech}`), type, techName: uniqueTech(tech), title: sectionTitle, description: description || "",
      states: [], questionOrder: ++questionOrder, repetingSectionGrid: [],
    },
  });

  const pages = [...(internal?.pages || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const tabs = pages.map((page, tabIndex) => {
    const tab = { id: uid(`tab:${page.page_id}`), title: page.title || `Strona ${tabIndex + 1}`, type: "MAIN", questions: [] };
    if (page.visibility_rules?.length) notes.push(`Strona „${tab.title}”: warunki widoczności stron nie mają odpowiednika w FormDefinition (stany są tylko na polach i sekcjach) — pominięto.`);
    const sections = [...(page.sections || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    for (const section of sections) {
      const fields = section.fields || [];
      const consentLike = fields.length > 0 && fields.every((f) => f.field_type === "checkbox" && FD_CONSENT_LABEL.test(f.label || ""));
      const plain = fields.filter((f) => f.field_type !== "repeatable_group");
      const groups = fields.filter((f) => f.field_type === "repeatable_group");
      if (plain.length || !groups.length) {
        const q = makeSection("SECTION", section.title, section.section_id || section.title, section.description,
          null, tab.questions.length);
        const ctx = { tabTitle: tab.title, sectionTech: q.field.techName, sectionTitle: section.title, tabIndex };
        sectionRefs.set(section.section_id, q.field);
        pendingStates.push({ target: q.field, rules: section.visibility_rules || [], kind: "SECTION", id: section.section_id });
        if (consentLike) {
          q.field.repetingSectionGrid.push({ x: 0, y: 0, rows: 1, cols: 1, field: {
            id: uid(`consent:${section.section_id}`), type: "CONSENT_SECTION", techName: uniqueTech(`${section.section_id}_zgody`),
            title: section.title, validation: fields.some((f) => f.required) ? ["REQUIRED"] : [],
            selectAllEnabled: fields.length > 1, questionOrder: ++questionOrder, states: [],
            consentSectionConfig: { consents: fields.map((f) => ({ description: f.label, required: !!f.required })),
              required: fields.some((f) => f.required), files: [] },
          } });
          notes.push(`Sekcja „${section.title}”: oświadczenia z checkboxów zamieniono na sekcję zgód CONSENT_SECTION (Standardy: zgody nie jako checkbox).`);
        } else {
          plain.forEach((f, i) => q.field.repetingSectionGrid.push(convertField(f, ctx, i)));
        }
        if (!plain.length) q.field.repetingSectionGrid.push({ x: 0, y: 0, rows: 1, cols: 1, field: {
          id: uid(`info:${section.section_id}`), type: "TEXT_BLOCK", techName: uniqueTech(`${section.section_id}_opis`),
          title: "", description: section.description || section.title, states: [], questionOrder: ++questionOrder } });
        tab.questions.push(q);
      }
      for (const group of groups) {
        const q = makeSection("REPEATING_SECTION", group.label || section.title, group.name || group.field_id,
          group.help_text, null, tab.questions.length);
        const ctx = { tabTitle: tab.title, sectionTech: q.field.techName, sectionTitle: section.title, tabIndex };
        (group.fields || []).forEach((sub, i) => q.field.repetingSectionGrid.push(convertField(sub, ctx, i)));
        if (!(group.fields || []).length) notes.push(`Sekcja powtarzająca się „${q.field.title}” nie ma kolumn — backend nie zwrócił pól tabeli.`);
        sectionRefs.set(group.field_id, q.field);
        pendingStates.push({ target: q.field, rules: group.dependencies || [], kind: "SECTION", id: group.field_id });
        tab.questions.push(q);
        notes.push(`Grupa „${q.field.title}” → REPEATING_SECTION na poziomie strony (reguła 6: bez zagnieżdżania sekcji).`);
      }
    }
    return tab;
  });

  /* Stany dynamiczne (jsonata) z zależności warunkowych. */
  const allFlows = [...(internal?.conditional_flows || [])];
  for (const page of pages) for (const s of page.sections || []) {
    allFlows.push(...(s.visibility_rules || []));
    for (const f of s.fields || []) allFlows.push(...(f.dependencies || []));
  }
  const seen = new Set();
  for (const flow of allFlows) {
    const key = JSON.stringify(flow);
    if (seen.has(key)) continue;
    seen.add(key);
    const source = refs.get(flow.source_field_id);
    const target = flow.target_type === "SECTION" ? sectionRefs.get(flow.target_id)
      : flow.target_type === "FIELD" ? refs.get(flow.target_id)?.out : null;
    if (!source || !target) {
      notes.push(`Zależność ${flow.source_field_id} → ${flow.target_id}: brak pola/sekcji lub cel typu strona — pominięto.`);
      continue;
    }
    let query = fdConditionQuery(flow, source);
    if (!query) continue;
    if (flow.action === "HIDE") query = `$not(${query})`;
    if (flow.action === "SHOW" || flow.action === "HIDE") target.states.push({ type: "SHOW", query });
    else notes.push(`Zależność ${flow.action} (${flow.source_field_id} → ${flow.target_id}) nie ma odpowiednika w COI (dostępne: SHOW, READ_ONLY) — pominięto.`);
  }

  /* Strona końcowa FINISH — wyłącznie sekcje informacyjne (reguła 7, Standardy: Podsumowanie i podgląd). */
  const finish = { id: uid("tab:finish"), title: "Podsumowanie", type: "FINISH", questions: [] };
  const finishSection = (tech, sectionTitle, text, y) => ({ x: 0, y, rows: 1, cols: 1, field: {
    id: uid(`section:${tech}`), type: "SECTION", techName: uniqueTech(tech), title: sectionTitle, states: [],
    questionOrder: ++questionOrder, repetingSectionGrid: [{ x: 0, y: 0, rows: 1, cols: 1, field: {
      id: uid(`info:${tech}`), type: "INFO_SECTION", techName: uniqueTech(`${tech}_info`), description: text,
      states: [], questionOrder: ++questionOrder } }] } });
  finish.questions.push(finishSection("podsumowanie", "Podsumowanie",
    "Po wysłaniu wniosku urząd rozpatrzy sprawę. Jeśli będą potrzebne dodatkowe informacje, urzędnik skontaktuje się z Tobą.", 0));
  finish.questions.push(finishSection("podglad", "Podgląd",
    "Zanim przejdziesz do wyboru sposobu podpisu, sprawdź dane na podglądzie. Jeśli zobaczysz błędy, wróć do edycji.", 1));
  tabs.push(finish);

  if (droppedRules.length) {
    notes.push(`Reguły długości/regex (${droppedRules.join(", ")}) nie mają odpowiednika w FormDefinition — pominięto.`);
  }
  const links = tabs.slice(0, -1).map((tab, i) => ({ id: uid(`link:${i}`), source: tab.id, target: tabs[i + 1].id }));
  return { form: { title, tabs, flow: { links } }, notes };
}

/* ====================== Walidator strukturalny FormDefinition ====================== */

/** Zwraca listę { level: "error"|"warn", path, rule, message } — reguły 1–12 + Standardy. */
function fdValidateStructure(form, { compileQuery } = {}) {
  const issues = [];
  const err = (path, rule, message) => issues.push({ level: "error", path, rule, message });
  const warn = (path, rule, message) => issues.push({ level: "warn", path, rule, message });

  if (!fdIsFormDefinition(form)) {
    err("", "Format", "To nie jest FormDefinition — brak tablicy „tabs”.");
    return issues;
  }
  if (typeof form.title !== "string" || !form.title.trim()) err("title", "Reguła 1", "Brak tytułu formularza.");
  else {
    if (form.title.length > 255) err("title", "Reguła 1", `Tytuł ma ${form.title.length} znaków (maks. 255).`);
    if (!/^(Wniosek|Zgłoszenie|Deklaracja|Oświadczenie)\b/.test(form.title.trim())) {
      warn("title", "Standardy: Tytuł", "Tytuł powinien zaczynać się od: Wniosek, Zgłoszenie, Deklaracja lub Oświadczenie.");
    }
  }

  const tabs = form.tabs;
  if (tabs.length < 2) err("tabs", "Reguła 2a", "Formularz musi mieć co najmniej dwie strony.");
  const ids = new Map();
  const checkId = (id, path, what) => {
    if (typeof id !== "string" || !id) { err(`${path}.id`, "Opis JSON", `${what}: brak identyfikatora.`); return; }
    if (!FD_UUID_RE.test(id)) warn(`${path}.id`, "Opis JSON", `${what}: identyfikator „${id}” nie jest UUID.`);
    if (ids.has(id)) err(`${path}.id`, "Opis JSON", `${what}: zduplikowany identyfikator „${id}”.`);
    ids.set(id, path);
  };

  const finishTabs = tabs.filter((t) => t?.type === "FINISH");
  if (finishTabs.length !== 1) err("tabs", "Reguła 2b", `Wymagana dokładnie jedna strona FINISH (jest ${finishTabs.length}).`);
  tabs.forEach((tab, ti) => {
    const path = `tabs.${ti}`;
    checkId(tab?.id, path, `Strona ${ti + 1}`);
    if (!FD_TAB_TYPES.includes(tab?.type)) err(`${path}.type`, "Reguła 2c", `Strona „${tab?.title}”: typ musi być MAIN lub FINISH.`);
    if (!tab?.title) warn(`${path}.title`, "Opis JSON", `Strona ${ti + 1}: brak tytułu.`);
    if (!Array.isArray(tab?.questions) || tab.questions.length === 0) {
      err(`${path}.questions`, "Reguła 3", `Strona „${tab?.title}”: musi zawierać co najmniej jedno pole.`);
    }
  });

  /* Przepływ stron. */
  const links = form.flow?.links;
  if (tabs.length > 1 && (!Array.isArray(links) || !links.length)) err("flow", "Reguła 9", "Strony muszą być połączone w flow.links.");
  const tabIds = new Set(tabs.map((t) => t?.id));
  (links || []).forEach((link, li) => {
    checkId(link?.id, `flow.links.${li}`, `Przejście ${li + 1}`);
    if (!tabIds.has(link?.source)) err(`flow.links.${li}.source`, "Reguła 9", `Przejście ${li + 1}: nieznana strona źródłowa.`);
    if (!tabIds.has(link?.target)) err(`flow.links.${li}.target`, "Reguła 9", `Przejście ${li + 1}: nieznana strona docelowa.`);
  });
  if (Array.isArray(links) && links.length) {
    const order = fdTabOrder(form);
    const reached = new Set(order.map((t) => t.id));
    tabs.forEach((tab, ti) => { if (!reached.has(tab?.id)) err(`tabs.${ti}`, "Reguła 9", `Strona „${tab?.title}” nie jest osiągalna przez flow.links.`); });
    if (order.length && order[order.length - 1].type !== "FINISH") err("flow", "Reguła 2b", "Przepływ musi kończyć się stroną FINISH.");
    finishTabs.forEach((t) => { if ((links || []).some((l) => l.source === t.id)) warn("flow", "Reguła 2b", "Ze strony FINISH nie powinno być przejść."); });
  }

  /* Pola. */
  const techNames = new Map();
  const allTech = new Set();
  fdWalk(form, ({ field }) => { if (field?.techName) allTech.add(field.techName); });
  let hasOwnData = false;
  let hasContact = false;

  fdWalk(form, ({ field, question, tab, path, parent, depth }) => {
    const fpath = `${path}.field`;
    if (!field || typeof field !== "object") { err(path, "Opis JSON", "Pytanie bez obiektu „field”."); return; }
    const name = field.title || field.techName || field.type;
    checkId(field.id, fpath, `Pole „${name}”`);
    ["x", "y", "rows", "cols"].forEach((k) => {
      if (question[k] !== undefined && typeof question[k] !== "number") err(`${path}.${k}`, "Opis JSON", `Pole „${name}”: ${k} musi być liczbą.`);
    });
    if ((question.x || 0) > 0) warn(`${path}.x`, "Standardy: układ", `Pole „${name}”: x > 0 — zalecany układ jednokolumnowy.`);

    if (!field.techName) err(`${fpath}.techName`, "Reguła 12", `Pole „${name}”: brak nazwy technicznej (techName).`);
    else {
      if (techNames.has(field.techName)) err(`${fpath}.techName`, "Reguła 12", `Zduplikowany techName „${field.techName}” — zapytania jsonata będą niejednoznaczne.`);
      techNames.set(field.techName, fpath);
    }

    const isSection = FD_SECTION_TYPES.includes(field.type);
    if (depth === 0 && !isSection) err(`${fpath}.type`, "Reguła 4/5", `Pole „${name}” musi być zagnieżdżone w sekcji lub sekcji powtarzającej się.`);
    if (depth > 0 && isSection) err(`${fpath}.type`, "Reguła 6", `Sekcja „${name}” jest zagnieżdżona w innej sekcji.`);
    if (!isSection && !FD_FIELD_TYPES.includes(field.type)) err(`${fpath}.type`, "Reguła 12", `Pole „${name}”: nieznany typ „${field.type}”.`);

    if (isSection) {
      if (!field.title) err(`${fpath}.title`, "Reguła 12", `Sekcja ${field.techName || ""}: brak nazwy sekcji (title).`);
      if (!fdChildren(field).length) err(fpath, "Reguła 3/4", `Sekcja „${name}” nie zawiera pól.`);
      if (field.repeatingSectionGrid && !field.repetingSectionGrid) warn(fpath, "Opis JSON", `Sekcja „${name}”: schemat używa klucza „repetingSectionGrid”.`);
      if (FD_OWN_DATA_SECTION.test(field.title || "") || /^twoje dane$/i.test(field.title || "")) hasOwnData = true;
      if (/dane do kontaktu/i.test(field.title || "")) hasContact = true;
    }

    const isInput = !isSection && !FD_PRESENTATION_TYPES.includes(field.type);
    if (tab?.type === "FINISH" && !isSection && field.type !== "INFO_SECTION") {
      if (isInput) err(fpath, "Reguła 7", `Strona końcowa: pole „${name}” (${field.type}) — dozwolone tylko INFO_SECTION.`);
      else warn(fpath, "Reguła 7", `Strona końcowa: ${field.type} — reguła dopuszcza tylko INFO_SECTION.`);
    }
    if (isInput && !Array.isArray(field.validation)) err(`${fpath}.validation`, "Reguła 8", `Pole „${name}”: brak atrybutu obligatoryjności (tablica „validation”).`);
    (field.validation || []).forEach((v, vi) => {
      const vname = fdValidatorName(v);
      if (vname === "PATTERN") return;
      if (!FD_VALIDATORS.includes(vname)) err(`${fpath}.validation.${vi}`, "Walidatory", `Pole „${name}”: nieznany walidator „${vname}”.`);
      else if (FD_VALIDATORS_BY_TYPE[field.type] && !FD_VALIDATORS_BY_TYPE[field.type].includes(vname)) {
        err(`${fpath}.validation.${vi}`, "Walidatory", `Pole „${name}” (${field.type}): walidator ${vname} jest niedostępny dla tego typu.`);
      }
    });

    if (field.type === "INFO_SECTION" && !field.description) err(`${fpath}.description`, "Reguła 12", `Sekcja informacyjna ${field.techName}: brak opisu.`);
    if (field.type === "CONSENT_SECTION") {
      const consents = field.consentSectionConfig?.consents;
      if (!Array.isArray(consents) || !consents.length) err(`${fpath}.consentSectionConfig`, "Reguła 12", `Sekcja zgód „${name}”: brak zgód (consents).`);
      (consents || []).forEach((c, ci) => { if (!c?.description) err(`${fpath}.consentSectionConfig.consents.${ci}`, "Reguła 12", `Sekcja zgód „${name}”: zgoda ${ci + 1} bez opisu.`); });
    } else if (field.consentSectionConfig) warn(`${fpath}.consentSectionConfig`, "Opis JSON", `Pole „${name}”: consentSectionConfig dotyczy tylko CONSENT_SECTION.`);
    if (field.type === "CHECKBOX" && FD_CONSENT_LABEL.test(field.title || "")) warn(fpath, "Standardy: Oświadczenie", `Pole „${name}”: zgody i oświadczenia zbieraj w CONSENT_SECTION, nie w checkboxie.`);

    /* Słowniki i opcje (reguła 11, materiał Słowniki). */
    if (field.dictionaryName) {
      if (!FD_CHOICE_TYPES.includes(field.type)) err(`${fpath}.dictionaryName`, "Reguła 11", `Pole „${name}”: słownik można podpiąć tylko do SELECT, RADIO lub CHECKBOX.`);
      const dict = fdFindDictionary(field.dictionaryName);
      if (!dict) warn(`${fpath}.dictionaryName`, "Słowniki", `Pole „${name}”: słownik „${field.dictionaryName}” nie należy do predefiniowanych — opisz go jako propozycję.`);
      else if (!dict.allowedTypes.includes(field.type)) err(`${fpath}.dictionaryName`, "Słowniki", `Słownik ${dict.name} można przypisać wyłącznie do listy rozwijanej (SELECT).`);
      if ((field.options || []).length) err(`${fpath}.options`, "Reguła 12", `Pole „${name}” korzysta ze słownika — tablica options musi być pusta.`);
    } else if (FD_CHOICE_TYPES.includes(field.type)) {
      const opts = field.options;
      if (field.type !== "CHECKBOX" && (!Array.isArray(opts) || !opts.length)) err(`${fpath}.options`, "Reguła 12", `Pole „${name}” (${field.type}): brak opcji i słownika.`);
      if (!Array.isArray(opts)) err(`${fpath}.options`, "Reguła 12", `Pole „${name}”: options musi być tablicą (może być pusta).`);
      (opts || []).forEach((o, oi) => {
        if (!o?.id || !o?.name || !o?.techName) err(`${fpath}.options.${oi}`, "Opis JSON", `Pole „${name}”: opcja ${oi + 1} wymaga id, name i techName.`);
      });
      if (field.type === "RADIO" && (opts || []).length > 5) warn(fpath, "Standardy: typ pola", `Pole „${name}”: więcej niż 5 opcji — rozważ listę rozwijaną.`);
    }
    const optionCount = field.dictionaryName ? (fdFindDictionary(field.dictionaryName)?.items?.length ?? 99) : (field.options || []).length;
    if (field.type === "SELECT" && optionCount > 15 && !field.searchEnabled) warn(fpath, "Standardy: typ pola", `Lista „${name}” ma ponad 15 opcji — włącz wyszukiwanie (searchEnabled).`);

    /* Dane inicjalne. */
    if (field.defaultValueType === "EXTERNAL_SYSTEM" && /^(MLPZ|PESEL)\.(name|surname|pesel)$/.test(field.defaultValue || "")) hasOwnData = true;
    if (field.defaultValueType && field.defaultValueType !== "EXTERNAL_SYSTEM") err(`${fpath}.defaultValueType`, "Dane inicjalne", `Pole „${name}”: defaultValueType może mieć tylko wartość EXTERNAL_SYSTEM lub być puste.`);
    if (field.defaultValueType === "EXTERNAL_SYSTEM") {
      if (field.readOnly !== true) err(`${fpath}.readOnly`, "Dane inicjalne", `Pole „${name}” zasilane z rejestru musi mieć readOnly: true.`);
      const [src, attr] = String(field.defaultValue || "").split(".");
      if (!FD_INITIAL_DATA[src]) err(`${fpath}.defaultValue`, "Dane inicjalne", `Pole „${name}”: nieznane źródło „${src}” (dozwolone: PESEL, PESEL_ADDRESS, MLPZ).`);
      else if (!FD_INITIAL_DATA[src].includes(attr)) err(`${fpath}.defaultValue`, "Dane inicjalne", `Pole „${name}”: źródło ${src} nie ma atrybutu „${attr}”.`);
    }
    if (field.defaultValue !== undefined && typeof field.defaultValue !== "string" && !Array.isArray(field.defaultValue)) {
      warn(`${fpath}.defaultValue`, "Opis JSON", `Pole „${name}”: defaultValue powinno być tekstem.`);
    }

    /* Stany dynamiczne (reguła 10). */
    (field.states || []).forEach((st, si) => {
      const allowed = isSection ? FD_STATE_TYPES_SECTION : FD_STATE_TYPES_FIELD;
      if (!allowed.includes(st?.type)) err(`${fpath}.states.${si}.type`, "Reguła 10", `„${name}”: stan „${st?.type}” niedozwolony (dozwolone: ${allowed.join(", ")}).`);
      if (!st?.query) { err(`${fpath}.states.${si}.query`, "Reguła 10", `„${name}”: stan bez warunku (query).`); return; }
      if (compileQuery) {
        const problem = compileQuery(st.query);
        if (problem) err(`${fpath}.states.${si}.query`, "Reguła 10", `„${name}”: błąd składni jsonata — ${problem}`);
      }
      for (const m of String(st.query).matchAll(/["']([^"']+)["']\s+in\s+field\.techName/g)) {
        if (!allTech.has(m[1])) err(`${fpath}.states.${si}.query`, "Reguła 10", `„${name}”: warunek odwołuje się do nieistniejącego techName „${m[1]}”.`);
      }
    });
  });

  if (!hasOwnData) warn("tabs", "Standardy: Twoje dane", "Brak obowiązkowej sekcji „Twoje dane” (Imię, Nazwisko, PESEL z MLPZ, tylko do odczytu).");
  if (!hasContact) warn("tabs", "Standardy: Dane do kontaktu", "Brak sekcji „Dane do kontaktu” na przedostatniej stronie.");
  return issues;
}
