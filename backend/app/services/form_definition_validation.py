"""Walidator strukturalny FormDefinition względem reguł konkursu COI.

Port ``fdValidateStructure`` z ``frontend/formdef.js``: reguły 1–12 z "Reguł technicznych",
katalog walidatorów i słowników, dane inicjalne oraz wybrane zalecenia "Standardów". Działa na
surowym słowniku JSON (tak jak wersja JS), dzięki czemu raportuje także struktury, których model
Pydantic by nie przyjął. Wyrażeń jsonata nie kompilujemy (brak biblioteki jsonata w Pythonie) —
sprawdzamy tylko zbalansowanie nawiasów i cudzysłowów oraz odwołania do ``techName``.
"""

import re
from collections.abc import Callable, Iterator
from typing import Any

from pydantic import ValidationError

from backend.app.schemas.form_definition import (
    FD_CHOICE_TYPES,
    FD_DICTIONARIES,
    FD_FIELD_TYPES,
    FD_PRESENTATION_TYPES,
    FD_SECTION_TYPES,
    FD_STATE_TYPES_FIELD,
    FD_STATE_TYPES_SECTION,
    FD_TAB_TYPES,
    FD_VALIDATORS,
    FD_VALIDATORS_BY_TYPE,
    FormDefinition,
    FormDefinitionIssue,
)
from backend.app.services.form_definition_export import (
    EXTERNAL_SYSTEM,
    FD_CONSENT_LABEL,
    FD_OWN_DATA_SECTION,
    INITIAL_DATA_ATTRIBUTES,
    SEARCH_THRESHOLD,
    TITLE_MAX_LENGTH,
    find_official_dictionary,
)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
TITLE_PREFIX_RE = re.compile(r"^(Wniosek|Zgłoszenie|Deklaracja|Oświadczenie)\b")
TECH_REFERENCE_RE = re.compile(r"[\"']([^\"']+)[\"']\s+in\s+field\.techName")
OWN_DATA_DEFAULT_RE = re.compile(r"^(MLPZ|PESEL)\.(name|surname|pesel)$")


def _children(field: Any) -> list[Any]:
    """Pytania-dzieci sekcji (schemat: repetingSectionGrid; przykłady jsonaty: repeatingSectionGrid)."""
    if not isinstance(field, dict):
        return []
    children = field.get("repetingSectionGrid") or field.get("repeatingSectionGrid") or []
    return children if isinstance(children, list) else []


def _grid_key(field: Any) -> str:
    if isinstance(field, dict) and "repetingSectionGrid" not in field and "repeatingSectionGrid" in field:
        return "repeatingSectionGrid"
    return "repetingSectionGrid"


def _validator_name(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip().upper()
    if isinstance(entry, dict):
        return str(entry.get("type") or entry.get("name") or "").strip().upper()
    return ""


def _walk(form: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Iteracja po wszystkich pytaniach (do głębokości 2, jak w JS)."""
    for ti, tab in enumerate(form.get("tabs") or []):
        if not isinstance(tab, dict):
            continue
        for qi, question in enumerate(tab.get("questions") or []):
            if not isinstance(question, dict):
                continue
            path = f"tabs.{ti}.questions.{qi}"
            field = question.get("field")
            yield {"field": field, "question": question, "tab": tab, "path": path, "depth": 0}
            key = _grid_key(field)
            for ci, child in enumerate(_children(field)):
                if not isinstance(child, dict):
                    continue
                child_path = f"{path}.field.{key}.{ci}"
                child_field = child.get("field")
                yield {"field": child_field, "question": child, "tab": tab, "path": child_path, "depth": 1}
                grand_key = _grid_key(child_field)
                for gi, grand in enumerate(_children(child_field)):
                    if not isinstance(grand, dict):
                        continue
                    yield {
                        "field": grand.get("field"),
                        "question": grand,
                        "tab": tab,
                        "path": f"{child_path}.field.{grand_key}.{gi}",
                        "depth": 2,
                    }


def tab_order(form: dict[str, Any]) -> list[dict[str, Any]]:
    """Kolejność stron wg flow.links (start = strona MAIN, do której nie prowadzi żadne przejście)."""
    tabs = [t for t in form.get("tabs") or [] if isinstance(t, dict)]
    flow = form.get("flow") if isinstance(form.get("flow"), dict) else {}
    links = [link for link in (flow or {}).get("links") or [] if isinstance(link, dict)]
    if not links:
        return list(tabs)
    by_id = {t.get("id"): t for t in tabs}
    targets = {link.get("target") for link in links}
    current = next((t for t in tabs if t.get("id") not in targets and t.get("type") != "FINISH"), None)
    if current is None and tabs:
        current = tabs[0]
    order: list[dict[str, Any]] = []
    seen: set[Any] = set()
    while current is not None and current.get("id") not in seen:
        order.append(current)
        seen.add(current.get("id"))
        nxt = next(
            (
                link
                for link in links
                if link.get("source") == current.get("id")
                and link.get("target") in by_id
                and link.get("target") not in seen
            ),
            None,
        )
        current = by_id.get(nxt.get("target")) if nxt else None
    return order


def check_jsonata_syntax(query: str) -> str | None:
    """Prosta kontrola składni jsonata: zbalansowane nawiasy i zamknięte literały tekstowe."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for ch in query:
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("'", '"', "`"):
            quote = ch
        elif ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack[-1] != pairs[ch]:
                return f"niezbalansowany nawias „{ch}”."
            stack.pop()
    if quote:
        return "niezamknięty literał tekstowy."
    if stack:
        return f"niezamknięty nawias „{stack[-1]}”."
    return None


def _schema_issues(raw: dict[str, Any]) -> list[FormDefinitionIssue]:
    try:
        FormDefinition.model_validate(raw)
    except ValidationError as exc:
        return [
            FormDefinitionIssue(
                level="error",
                path=".".join(str(part) for part in err["loc"]),
                rule="Schemat JSON",
                message=f"Niezgodność z oficjalnym schematem FormDefinition: {err['msg']}.",
            )
            for err in exc.errors()
        ]
    return []


def validate_form_definition(
    fd: FormDefinition | dict[str, Any],
    *,
    compile_query: Callable[[str], str | None] | None = check_jsonata_syntax,
) -> list[FormDefinitionIssue]:
    """Zwraca listę problemów ``{level: error|warn, path, rule, message}`` (reguły 1–12 + Standardy).

    Dla surowego słownika dodatkowo raportuje niezgodności z modelem oficjalnego schematu
    (reguła "Schemat JSON").
    """
    issues: list[FormDefinitionIssue] = []

    def err(path: str, rule: str, message: str) -> None:
        issues.append(FormDefinitionIssue(level="error", path=path, rule=rule, message=message))

    def warn(path: str, rule: str, message: str) -> None:
        issues.append(FormDefinitionIssue(level="warn", path=path, rule=rule, message=message))

    if isinstance(fd, FormDefinition):
        form: Any = fd.model_dump(mode="json", by_alias=True)
    else:
        form = fd
    if not isinstance(form, dict) or not isinstance(form.get("tabs"), list):
        err("", "Format", "To nie jest FormDefinition — brak tablicy „tabs”.")
        return issues
    if not isinstance(fd, FormDefinition):
        issues.extend(_schema_issues(form))

    title = form.get("title")
    if not isinstance(title, str) or not title.strip():
        err("title", "Reguła 1", "Brak tytułu formularza.")
    else:
        if len(title) > TITLE_MAX_LENGTH:
            err("title", "Reguła 1", f"Tytuł ma {len(title)} znaków (maks. {TITLE_MAX_LENGTH}).")
        if not TITLE_PREFIX_RE.search(title.strip()):
            warn(
                "title",
                "Standardy: Tytuł",
                "Tytuł powinien zaczynać się od: Wniosek, Zgłoszenie, Deklaracja lub Oświadczenie.",
            )

    tabs: list[Any] = form["tabs"]
    if len(tabs) < 2:
        err("tabs", "Reguła 2a", "Formularz musi mieć co najmniej dwie strony.")
    ids: dict[str, str] = {}

    def check_id(value: Any, path: str, what: str) -> None:
        if not isinstance(value, str) or not value:
            err(f"{path}.id", "Opis JSON", f"{what}: brak identyfikatora.")
            return
        if not UUID_RE.match(value):
            warn(f"{path}.id", "Opis JSON", f"{what}: identyfikator „{value}” nie jest UUID.")
        if value in ids:
            err(f"{path}.id", "Opis JSON", f"{what}: zduplikowany identyfikator „{value}”.")
        ids[value] = path

    dict_tabs = [t if isinstance(t, dict) else {} for t in tabs]
    finish_tabs = [t for t in dict_tabs if t.get("type") == "FINISH"]
    if len(finish_tabs) != 1:
        err("tabs", "Reguła 2b", f"Wymagana dokładnie jedna strona FINISH (jest {len(finish_tabs)}).")
    for ti, tab in enumerate(dict_tabs):
        path = f"tabs.{ti}"
        check_id(tab.get("id"), path, f"Strona {ti + 1}")
        if tab.get("type") not in FD_TAB_TYPES:
            err(f"{path}.type", "Reguła 2c", f"Strona „{tab.get('title')}”: typ musi być MAIN lub FINISH.")
        if not tab.get("title"):
            warn(f"{path}.title", "Opis JSON", f"Strona {ti + 1}: brak tytułu.")
        questions = tab.get("questions")
        if not isinstance(questions, list) or not questions:
            err(f"{path}.questions", "Reguła 3", f"Strona „{tab.get('title')}”: musi zawierać co najmniej jedno pole.")

    # Przepływ stron.
    flow = form.get("flow") if isinstance(form.get("flow"), dict) else None
    links = flow.get("links") if flow else None
    if len(tabs) > 1 and (not isinstance(links, list) or not links):
        err("flow", "Reguła 9", "Strony muszą być połączone w flow.links.")
    tab_ids = {t.get("id") for t in dict_tabs}
    for li, link in enumerate(links if isinstance(links, list) else []):
        link = link if isinstance(link, dict) else {}
        check_id(link.get("id"), f"flow.links.{li}", f"Przejście {li + 1}")
        if link.get("source") not in tab_ids:
            err(f"flow.links.{li}.source", "Reguła 9", f"Przejście {li + 1}: nieznana strona źródłowa.")
        if link.get("target") not in tab_ids:
            err(f"flow.links.{li}.target", "Reguła 9", f"Przejście {li + 1}: nieznana strona docelowa.")
    if isinstance(links, list) and links:
        order = tab_order(form)
        reached = {t.get("id") for t in order}
        for ti, tab in enumerate(dict_tabs):
            if tab.get("id") not in reached:
                err(f"tabs.{ti}", "Reguła 9", f"Strona „{tab.get('title')}” nie jest osiągalna przez flow.links.")
        if order and order[-1].get("type") != "FINISH":
            err("flow", "Reguła 2b", "Przepływ musi kończyć się stroną FINISH.")
        for finish in finish_tabs:
            if any(isinstance(lk, dict) and lk.get("source") == finish.get("id") for lk in links):
                warn("flow", "Reguła 2b", "Ze strony FINISH nie powinno być przejść.")

    # Pola.
    tech_names: dict[str, str] = {}
    all_tech = {
        item["field"].get("techName")
        for item in _walk(form)
        if isinstance(item["field"], dict) and item["field"].get("techName")
    }
    has_own_data = False
    has_contact = False

    for item in _walk(form):
        field, question, tab, path, depth = (
            item["field"], item["question"], item["tab"], item["path"], item["depth"]
        )
        fpath = f"{path}.field"
        if not isinstance(field, dict):
            err(path, "Opis JSON", "Pytanie bez obiektu „field”.")
            continue
        ftype = field.get("type")
        name = field.get("title") or field.get("techName") or ftype
        check_id(field.get("id"), fpath, f"Pole „{name}”")
        for key in ("x", "y", "rows", "cols"):
            value = question.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                err(f"{path}.{key}", "Opis JSON", f"Pole „{name}”: {key} musi być liczbą.")
        x_value = question.get("x")
        if isinstance(x_value, (int, float)) and not isinstance(x_value, bool) and x_value > 0:
            warn(f"{path}.x", "Standardy: układ", f"Pole „{name}”: x > 0 — zalecany układ jednokolumnowy.")

        tech = field.get("techName")
        if not tech:
            err(f"{fpath}.techName", "Reguła 12", f"Pole „{name}”: brak nazwy technicznej (techName).")
        else:
            if tech in tech_names:
                err(
                    f"{fpath}.techName",
                    "Reguła 12",
                    f"Zduplikowany techName „{tech}” — zapytania jsonata będą niejednoznaczne.",
                )
            tech_names[tech] = fpath

        is_section = ftype in FD_SECTION_TYPES
        if depth == 0 and not is_section:
            err(
                f"{fpath}.type",
                "Reguła 4/5",
                f"Pole „{name}” musi być zagnieżdżone w sekcji lub sekcji powtarzającej się.",
            )
        if depth > 0 and is_section:
            err(f"{fpath}.type", "Reguła 6", f"Sekcja „{name}” jest zagnieżdżona w innej sekcji.")
        if not is_section and ftype not in FD_FIELD_TYPES:
            err(f"{fpath}.type", "Reguła 12", f"Pole „{name}”: nieznany typ „{ftype}”.")

        title_text = str(field.get("title") or "")
        if is_section:
            if not field.get("title"):
                err(f"{fpath}.title", "Reguła 12", f"Sekcja {tech or ''}: brak nazwy sekcji (title).")
            if not _children(field):
                err(fpath, "Reguła 3/4", f"Sekcja „{name}” nie zawiera pól.")
            if "repeatingSectionGrid" in field and "repetingSectionGrid" not in field:
                warn(fpath, "Opis JSON", f"Sekcja „{name}”: schemat używa klucza „repetingSectionGrid”.")
            if FD_OWN_DATA_SECTION.search(title_text) or re.match(r"^twoje dane$", title_text, re.IGNORECASE):
                has_own_data = True
            if re.search(r"dane do kontaktu", title_text, re.IGNORECASE):
                has_contact = True

        is_input = not is_section and ftype not in FD_PRESENTATION_TYPES
        if tab.get("type") == "FINISH" and not is_section and ftype != "INFO_SECTION":
            if is_input:
                err(
                    fpath,
                    "Reguła 7",
                    f"Strona końcowa: pole „{name}” ({ftype}) — dozwolone tylko INFO_SECTION.",
                )
            else:
                warn(fpath, "Reguła 7", f"Strona końcowa: {ftype} — reguła dopuszcza tylko INFO_SECTION.")
        validation = field.get("validation")
        if is_input and not isinstance(validation, list):
            err(
                f"{fpath}.validation",
                "Reguła 8",
                f"Pole „{name}”: brak atrybutu obligatoryjności (tablica „validation”).",
            )
        for vi, entry in enumerate(validation if isinstance(validation, list) else []):
            vname = _validator_name(entry)
            if vname == "PATTERN":
                continue
            if vname not in FD_VALIDATORS:
                err(f"{fpath}.validation.{vi}", "Walidatory", f"Pole „{name}”: nieznany walidator „{vname}”.")
            elif ftype in FD_VALIDATORS_BY_TYPE and vname not in FD_VALIDATORS_BY_TYPE[ftype]:
                err(
                    f"{fpath}.validation.{vi}",
                    "Walidatory",
                    f"Pole „{name}” ({ftype}): walidator {vname} jest niedostępny dla tego typu.",
                )

        if ftype == "INFO_SECTION" and not field.get("description"):
            err(f"{fpath}.description", "Reguła 12", f"Sekcja informacyjna {tech}: brak opisu.")
        config = field.get("consentSectionConfig")
        if ftype == "CONSENT_SECTION":
            consents = config.get("consents") if isinstance(config, dict) else None
            if not isinstance(consents, list) or not consents:
                err(f"{fpath}.consentSectionConfig", "Reguła 12", f"Sekcja zgód „{name}”: brak zgód (consents).")
            for ci, consent in enumerate(consents if isinstance(consents, list) else []):
                if not isinstance(consent, dict) or not consent.get("description"):
                    err(
                        f"{fpath}.consentSectionConfig.consents.{ci}",
                        "Reguła 12",
                        f"Sekcja zgód „{name}”: zgoda {ci + 1} bez opisu.",
                    )
        elif config:
            warn(
                f"{fpath}.consentSectionConfig",
                "Opis JSON",
                f"Pole „{name}”: consentSectionConfig dotyczy tylko CONSENT_SECTION.",
            )
        if ftype == "CHECKBOX" and FD_CONSENT_LABEL.search(title_text):
            warn(
                fpath,
                "Standardy: Oświadczenie",
                f"Pole „{name}”: zgody i oświadczenia zbieraj w CONSENT_SECTION, nie w checkboxie.",
            )

        # Słowniki i opcje (reguła 11, materiał Słowniki).
        options = field.get("options")
        dictionary_name = field.get("dictionaryName")
        if dictionary_name:
            if ftype not in FD_CHOICE_TYPES:
                err(
                    f"{fpath}.dictionaryName",
                    "Reguła 11",
                    f"Pole „{name}”: słownik można podpiąć tylko do SELECT, RADIO lub CHECKBOX.",
                )
            official = find_official_dictionary(dictionary_name)
            if official is None:
                warn(
                    f"{fpath}.dictionaryName",
                    "Słowniki",
                    f"Pole „{name}”: słownik „{dictionary_name}” nie należy do predefiniowanych — "
                    "opisz go jako propozycję.",
                )
            elif ftype not in FD_DICTIONARIES[official][1]:
                err(
                    f"{fpath}.dictionaryName",
                    "Słowniki",
                    f"Słownik {official} można przypisać wyłącznie do listy rozwijanej (SELECT).",
                )
            if isinstance(options, list) and options:
                err(
                    f"{fpath}.options",
                    "Reguła 12",
                    f"Pole „{name}” korzysta ze słownika — tablica options musi być pusta.",
                )
        elif ftype in FD_CHOICE_TYPES:
            if ftype != "CHECKBOX" and (not isinstance(options, list) or not options):
                err(f"{fpath}.options", "Reguła 12", f"Pole „{name}” ({ftype}): brak opcji i słownika.")
            if not isinstance(options, list):
                err(f"{fpath}.options", "Reguła 12", f"Pole „{name}”: options musi być tablicą (może być pusta).")
            for oi, option in enumerate(options if isinstance(options, list) else []):
                if not isinstance(option, dict) or not all(option.get(k) for k in ("id", "name", "techName")):
                    err(
                        f"{fpath}.options.{oi}",
                        "Opis JSON",
                        f"Pole „{name}”: opcja {oi + 1} wymaga id, name i techName.",
                    )
            if ftype == "RADIO" and isinstance(options, list) and len(options) > 5:
                warn(fpath, "Standardy: typ pola", f"Pole „{name}”: więcej niż 5 opcji — rozważ listę rozwijaną.")
        # Wszystkie słowniki COI mają ponad 15 pozycji (TERYT, Kraje, Numery kierunkowe, 16 województw).
        many_options = bool(dictionary_name) or (isinstance(options, list) and len(options) > SEARCH_THRESHOLD)
        if ftype == "SELECT" and many_options and not field.get("searchEnabled"):
            warn(
                fpath,
                "Standardy: typ pola",
                f"Lista „{name}” ma ponad {SEARCH_THRESHOLD} opcji — włącz wyszukiwanie (searchEnabled).",
            )

        # Dane inicjalne.
        default_value = field.get("defaultValue")
        default_type = field.get("defaultValueType")
        if (
            default_type == EXTERNAL_SYSTEM
            and isinstance(default_value, str)
            and OWN_DATA_DEFAULT_RE.match(default_value)
        ):
            has_own_data = True
        if default_type and default_type != EXTERNAL_SYSTEM:
            err(
                f"{fpath}.defaultValueType",
                "Dane inicjalne",
                f"Pole „{name}”: defaultValueType może mieć tylko wartość EXTERNAL_SYSTEM lub być puste.",
            )
        if default_type == EXTERNAL_SYSTEM:
            if field.get("readOnly") is not True:
                err(
                    f"{fpath}.readOnly",
                    "Dane inicjalne",
                    f"Pole „{name}” zasilane z rejestru musi mieć readOnly: true.",
                )
            source, _, attribute = str(default_value or "").partition(".")
            if source not in INITIAL_DATA_ATTRIBUTES:
                err(
                    f"{fpath}.defaultValue",
                    "Dane inicjalne",
                    f"Pole „{name}”: nieznane źródło „{source}” (dozwolone: PESEL, PESEL_ADDRESS, MLPZ).",
                )
            elif attribute not in INITIAL_DATA_ATTRIBUTES[source]:
                err(
                    f"{fpath}.defaultValue",
                    "Dane inicjalne",
                    f"Pole „{name}”: źródło {source} nie ma atrybutu „{attribute}”.",
                )
        if default_value is not None and not isinstance(default_value, (str, list)):
            warn(f"{fpath}.defaultValue", "Opis JSON", f"Pole „{name}”: defaultValue powinno być tekstem.")

        # Stany dynamiczne (reguła 10).
        states = field.get("states")
        allowed_states = FD_STATE_TYPES_SECTION if is_section else FD_STATE_TYPES_FIELD
        for si, state in enumerate(states if isinstance(states, list) else []):
            state = state if isinstance(state, dict) else {}
            if state.get("type") not in allowed_states:
                err(
                    f"{fpath}.states.{si}.type",
                    "Reguła 10",
                    f"„{name}”: stan „{state.get('type')}” niedozwolony "
                    f"(dozwolone: {', '.join(allowed_states)}).",
                )
            query = state.get("query")
            if not query:
                err(f"{fpath}.states.{si}.query", "Reguła 10", f"„{name}”: stan bez warunku (query).")
                continue
            if compile_query is not None:
                problem = compile_query(str(query))
                if problem:
                    err(f"{fpath}.states.{si}.query", "Reguła 10", f"„{name}”: błąd składni jsonata — {problem}")
            for match in TECH_REFERENCE_RE.finditer(str(query)):
                if match.group(1) not in all_tech:
                    err(
                        f"{fpath}.states.{si}.query",
                        "Reguła 10",
                        f"„{name}”: warunek odwołuje się do nieistniejącego techName „{match.group(1)}”.",
                    )

    if not has_own_data:
        warn(
            "tabs",
            "Standardy: Twoje dane",
            "Brak obowiązkowej sekcji „Twoje dane” (Imię, Nazwisko, PESEL z MLPZ, tylko do odczytu).",
        )
    if not has_contact:
        warn("tabs", "Standardy: Dane do kontaktu", "Brak sekcji „Dane do kontaktu” na przedostatniej stronie.")
    return issues


def has_errors(issues: list[FormDefinitionIssue]) -> bool:
    return any(issue.level == "error" for issue in issues)
