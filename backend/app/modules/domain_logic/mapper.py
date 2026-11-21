"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Logika domenowa (deterministyczna, bez LLM, offline) nakładana na wynik ekstraktora:

1. sekcje predefiniowane ze Standardów (Urząd, Podmiot wniosku, Twoje dane z MLPZ, Adres,
   Dane do kontaktu, Zgody) - rozpoznane sekcje LLM są zastępowane kanonicznymi, brakujące wstawiane;
2. walidatory COI po etykietach (PESEL, NIP, REGON, ZIP_CODE, EMAIL, EPUAP, ADE, dowód osobisty),
   typy daty/liczby, zamiana historycznych POSTAL_CODE/PL_PHONE;
3. słowniki: predefiniowane COI po etykiecie, słowniki bazy po zbiorze opcji, propozycje nowych;
4. zgody z checkboxów -> consent_section, długie pouczenia -> text_block;
5. przepływy warunkowe dla opcji „inne”/„tak” i pola doprecyzowującego;
6. struktura: brak pustych stron/sekcji, strona końcowa FINISH tylko z info_section.

`enrich` nie mutuje wejścia i jest idempotentne: enrich(enrich(x)) ma tę samą strukturę co enrich(x).
"""

import json
import logging
from collections.abc import Iterator
from functools import cached_property
from pathlib import Path

from backend.app.modules.domain_logic import rules
from backend.app.modules.domain_logic import standard_sections as std
from backend.app.schemas.form_schema import (
    CHOICE_FIELD_TYPES,
    PRESENTATION_FIELD_TYPES,
    ConditionalFlow,
    ConditionOperator,
    ConsentItem,
    CustomValidator,
    DataType,
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FlowAction,
    FormDocumentSchema,
    MappedDictionary,
    PageDefinition,
    PageType,
    ProposedDictionary,
    SectionDefinition,
    TargetType,
    ValidationRules,
)

logger = logging.getLogger(__name__)

LABEL_MATCH_CONFIDENCE = 0.9
OPTIONS_MATCH_THRESHOLD = 0.8
MIN_PROPOSED_OPTIONS = 3
TEXT_BLOCK_MIN_CHARS = 200
_INPUT_TEXT_TYPES = (FieldType.TEXT, FieldType.TEXTAREA)
_YES_NO = {"tak", "nie"}


def _iter_fields(fields: list[FieldDefinition]) -> Iterator[FieldDefinition]:
    for field in fields:
        yield field
        if field.fields:
            yield from _iter_fields(field.fields)


def _all_fields(form: FormDocumentSchema) -> Iterator[FieldDefinition]:
    for page in form.pages:
        for section in page.sections:
            yield from _iter_fields(section.fields)


def _flow_key(flow: ConditionalFlow) -> str:
    return json.dumps(flow.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


class _Ids:
    """Rejestr zajętych identyfikatorów; `uid` rezerwuje unikalny id na bazie podanego."""

    def __init__(self, form: FormDocumentSchema) -> None:
        self.used: set[str] = set()
        for page in form.pages:
            self.used.add(page.page_id)
            for section in page.sections:
                self.used.add(section.section_id)
                self.used.update(f.field_id for f in _iter_fields(section.fields))

    def uid(self, base: str) -> str:
        candidate, suffix = base, 2
        while candidate in self.used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        self.used.add(candidate)
        return candidate


class DomainLogicEnricher:
    """Implementacja protokołu DomainEnricher."""

    def __init__(self, dictionaries_dir: Path) -> None:
        self._dictionaries_dir = Path(dictionaries_dir)

    # ------------------------------------------------------------------ słowniki bazy
    @cached_property
    def _dictionaries(self) -> dict[str, tuple[str, list[DictionaryItem]]]:
        """dictionary_id -> (nazwa, pozycje) ze wszystkich plików *.json katalogu słowników."""
        result: dict[str, tuple[str, list[DictionaryItem]]] = {}
        if not self._dictionaries_dir.is_dir():
            return result
        for path in sorted(self._dictionaries_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                for entry in raw.get("dictionaries", []):
                    items = [DictionaryItem(code=i["code"], label=i["label"]) for i in entry.get("items", [])]
                    result[entry["dictionary_id"]] = (entry.get("name") or entry["dictionary_id"], items)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logger.warning("Pominięto plik słowników %s: %s", path.name, exc)
        return result

    # ------------------------------------------------------------------ API
    def enrich(self, form: FormDocumentSchema) -> FormDocumentSchema:
        form = form.model_copy(deep=True)
        ids = _Ids(form)
        remap: dict[str, str] = {}

        main_pages, finish_page = self._split_finish(form, ids)
        for page in main_pages:
            # Pola sekcji kanonicznych (predefined) są wzorcowe - heurystyki dotyczą tylko pól z LLM,
            # dlatego wzbogacamy je PRZED zastąpieniem sekcji (pola dodatkowe trafiają już gotowe).
            for section in page.sections:
                if section.predefined:
                    continue
                for field in _iter_fields(section.fields):
                    self._enrich_field(field)
                self._merge_consents(section, ids)
                self._detail_flows(section, form)
            page.sections = [self._standardize_section(s, ids, remap) for s in page.sections]
        self._insert_standard_sections(main_pages, ids)

        main_pages = self._cleanup_pages(main_pages)
        if not main_pages:  # nic nie zostało - strona danych wnioskodawcy ze standardów
            main_pages = [PageDefinition(page_id=ids.uid("page_1"), title="Dane wnioskodawcy", order=1)]
            self._insert_standard_sections(main_pages, ids)
        if finish_page is None or not any(s.fields for s in finish_page.sections):
            finish_page = PageDefinition(
                page_id=finish_page.page_id if finish_page else ids.uid("page_finish"),
                title="Podsumowanie",
                order=1,
                page_type=PageType.FINISH,
                sections=std.finish_sections(ids.uid),
            )
        form.pages = [*main_pages, finish_page]
        for order, page in enumerate(form.pages, start=1):
            page.order = order
            for sec_order, section in enumerate(page.sections, start=1):
                section.order = sec_order

        self._remap_flows(form, remap)
        self._build_dictionaries(form)
        self._drop_dangling(form)
        return form

    # ------------------------------------------------------------------ strony
    @staticmethod
    def _split_finish(
        form: FormDocumentSchema, ids: _Ids
    ) -> tuple[list[PageDefinition], PageDefinition | None]:
        """Oddziela stronę FINISH; jej pola inne niż informacyjne przenosi na ostatnią stronę MAIN."""
        main = [p for p in form.pages if p.page_type != PageType.FINISH]
        finish_pages = [p for p in form.pages if p.page_type == PageType.FINISH]
        finish = finish_pages[0] if finish_pages else None
        moved: list[SectionDefinition] = []
        for page in finish_pages:
            for section in page.sections:
                info = [f for f in section.fields if f.field_type in PRESENTATION_FIELD_TYPES]
                other = [f for f in section.fields if f.field_type not in PRESENTATION_FIELD_TYPES]
                for field in info:
                    field.field_type = FieldType.INFO_SECTION
                    field.required = False
                if other:
                    # Cała sekcja bez pól informacyjnych przechodzi z tym samym id; podzielona - z nowym.
                    new_id = section.section_id if not info else ids.uid(f"{section.section_id}_dane")
                    moved.append(section.model_copy(update={"fields": other, "section_id": new_id}))
                section.fields = info
        if finish is not None:
            for page in finish_pages[1:]:
                finish.sections.extend(page.sections)
            finish.sections = [s for s in finish.sections if s.fields]
        if moved:
            if not main:
                main.append(PageDefinition(page_id="page_main", title="Informacje o sprawie", order=1))
            main[-1].sections.extend(moved)
        return main, finish

    @staticmethod
    def _cleanup_pages(pages: list[PageDefinition]) -> list[PageDefinition]:
        result = []
        for page in pages:
            page.sections = [s for s in page.sections if s.fields]
            if page.sections:
                result.append(page)
        return result

    # ------------------------------------------------------------------ sekcje standardowe
    def _standardize_section(
        self, section: SectionDefinition, ids: _Ids, remap: dict[str, str]
    ) -> SectionDefinition:
        """Zastępuje rozpoznaną sekcję LLM jej kanoniczną wersją ze Standardów."""
        if section.predefined:
            return section
        title = rules.norm(section.title)
        labels = [rules.norm(f.label) for f in section.fields]
        address_like = sum(1 for label in labels if rules.ADDRESS_FIELD.search(label))

        canonical: SectionDefinition | None = None
        if rules.OFFICE_SECTION.search(title):
            canonical = std.office_section(ids.uid)
        elif rules.APPLICANT_SECTION.search(title) and not rules.NOT_OWN_DATA.search(title):
            canonical = std.applicant_data_section(ids.uid)
        elif rules.ADDRESS_SECTION.search(title) and address_like >= 2:
            prefix = rules.slug(section.title, "adres")
            canonical = std.address_section(ids.uid, prefix=prefix, title=section.title)
        elif rules.CONTACT_SECTION.search(title):
            canonical = std.contact_section(ids.uid)
        elif rules.CONSENT_SECTION.search(title) and section.fields and all(
            f.field_type in (FieldType.CHECKBOX, FieldType.CONSENT_SECTION) for f in section.fields
        ):
            canonical = std.consents_section(ids.uid, self._consents_from(section.fields))
            for field in section.fields:  # zgody LLM są już w consents
                remap[field.field_id] = canonical.fields[0].field_id
            section.fields = []
        if canonical is None:
            return section

        by_label = {rules.norm(f.label): f for f in canonical.fields}
        extras: list[FieldDefinition] = []
        for field in section.fields:
            match = by_label.get(rules.norm(field.label)) or self._canonical_match(field, canonical)
            if match is not None:
                remap[field.field_id] = match.field_id
            else:
                extras.append(field)
        canonical.fields.extend(extras)
        canonical.section_id = section.section_id  # referencje do sekcji pozostają ważne
        canonical.visibility_rules = section.visibility_rules
        if canonical.predefined == std.STD_ADDRESS and section.description:
            canonical.description = section.description
        return canonical

    @staticmethod
    def _canonical_match(field: FieldDefinition, canonical: SectionDefinition) -> FieldDefinition | None:
        """Dopasowanie pola LLM do pola kanonicznego po rodzaju danych (imię, PESEL, telefon...)."""
        label = rules.norm(field.label)
        checks = (
            (rules.FIRST_NAME, ("imie",)),
            (rules.LAST_NAME, ("nazwisko",)),
            (rules.PESEL, ("pesel",)),
            (rules.PHONE_FIELD, ("telefon",)),
            (rules.EMAIL_FIELD, ("email",)),
        )
        for pattern, bases in checks:
            if pattern.search(label):
                for candidate in canonical.fields:
                    if any(candidate.field_id.startswith(base) for base in bases):
                        return candidate
        if canonical.predefined == std.STD_ADDRESS and rules.ADDRESS_FIELD.search(label):
            for candidate in canonical.fields:
                if rules.norm(candidate.label) and rules.norm(candidate.label) in label:
                    return candidate
        return None

    @staticmethod
    def _consents_from(fields: list[FieldDefinition]) -> list[ConsentItem]:
        consents: list[ConsentItem] = []
        for field in fields:
            if field.field_type == FieldType.CONSENT_SECTION:
                consents.extend(field.consents or [])
            elif field.options:
                consents.extend(ConsentItem(description=o.label, required=field.required) for o in field.options)
            elif field.label:
                consents.append(ConsentItem(description=field.label, required=field.required))
        known = {rules.norm(c.description) for c in consents}
        consents.extend(c for c in std.DEFAULT_CONSENTS if rules.norm(c.description) not in known)
        return consents

    @staticmethod
    def _insert_standard_sections(pages: list[PageDefinition], ids: _Ids) -> None:
        """Wstawia brakujące sekcje standardowe: dane stron na 1. stronie, kontakt i zgody na ostatniej."""
        present = {s.predefined for p in pages for s in p.sections if s.predefined}
        if not pages:
            return
        first, last = pages[0], pages[-1]
        head = [
            (std.STD_OFFICE, std.office_section),
            (std.STD_APPLICANT_ROLE, std.applicant_role_section),
            (std.STD_APPLICANT_DATA, std.applicant_data_section),
            (std.STD_ADDRESS, std.address_section),
        ]
        position = 0
        for key, factory in head:
            existing = next((i for i, s in enumerate(first.sections) if s.predefined == key), None)
            if existing is not None:
                position = max(position, existing + 1)
                continue
            if key in present:
                continue
            first.sections.insert(position, factory(ids.uid))
            position += 1
        if std.STD_CONTACT not in present:
            consent_index = next(
                (i for i, s in enumerate(last.sections) if s.predefined == std.STD_CONSENTS), len(last.sections)
            )
            last.sections.insert(consent_index, std.contact_section(ids.uid))
        if std.STD_CONSENTS not in present:
            last.sections.append(std.consents_section(ids.uid))

    # ------------------------------------------------------------------ pola
    def _enrich_field(self, field: FieldDefinition) -> None:
        if field.field_type in PRESENTATION_FIELD_TYPES:
            field.required = False
            field.validation_rules = None
            return
        rules_ = field.validation_rules
        if rules_ and rules_.custom_validator == CustomValidator.POSTAL_CODE:
            rules_.custom_validator = CustomValidator.ZIP_CODE
        if rules_ and rules_.custom_validator == CustomValidator.PL_PHONE:
            rules_.custom_validator = None

        if field.field_type in _INPUT_TEXT_TYPES and len(field.label) >= TEXT_BLOCK_MIN_CHARS and not (
            field.label.rstrip().endswith(("?", ":"))
        ):
            field.help_text = field.label if not field.help_text else f"{field.label}\n{field.help_text}"
            field.label = ""
            field.field_type = FieldType.TEXT_BLOCK
            field.required = False
            field.validation_rules = None
            return

        label = rules.norm(field.label or field.name)
        if field.field_type == FieldType.TEXT:
            if rules.DATE_LABEL.search(label):
                field.field_type = FieldType.DATE
                self._set_rules(field, data_type=DataType.DATE)
            elif rules.NUMBER_LABEL.search(label):
                field.field_type = FieldType.NUMBER
                self._set_rules(field, data_type=DataType.NUMBER)
            elif not (field.validation_rules and field.validation_rules.custom_validator):
                validator = rules.validator_for(field.label or field.name)
                if validator is not None:
                    self._set_rules(field, custom_validator=validator)

        if field.dictionary_ref is None and field.field_type in (FieldType.TEXT, *CHOICE_FIELD_TYPES):
            dictionary_id = rules.dictionary_for(field.label or field.name)
            if dictionary_id is not None:
                if dictionary_id in rules.SELECT_ONLY_DICTIONARIES or field.field_type == FieldType.TEXT:
                    field.field_type = FieldType.SELECT
                field.dictionary_ref = dictionary_id
                field.options = None
                if field.validation_rules and field.validation_rules.custom_validator:
                    field.validation_rules.custom_validator = None
                return
        if field.dictionary_ref is None and field.field_type in CHOICE_FIELD_TYPES and field.options:
            match = self._match_options(field.options)
            if match is not None:
                field.dictionary_ref = match[0]

    @staticmethod
    def _set_rules(field: FieldDefinition, **values: object) -> None:
        if field.validation_rules is None:
            field.validation_rules = ValidationRules(**values)
        else:
            for key, value in values.items():
                if getattr(field.validation_rules, key) is None:
                    setattr(field.validation_rules, key, value)

    def _match_options(self, options: list[DictionaryItem]) -> tuple[str, float] | None:
        """Słownik bazy, którego pozycje pokrywają opcje pola (po znormalizowanych etykietach)."""
        labels = {rules.norm(o.label) for o in options if o.label}
        if len(labels) < 2 or labels <= _YES_NO:
            return None
        best: tuple[str, float] | None = None
        for dictionary_id, (_name, items) in self._dictionaries.items():
            known = {rules.norm(i.label) for i in items}
            overlap = len(labels & known) / len(labels)
            if overlap >= OPTIONS_MATCH_THRESHOLD and (best is None or overlap > best[1]):
                best = (dictionary_id, overlap)
        return best

    @staticmethod
    def _merge_consents(section: SectionDefinition, ids: _Ids) -> None:
        """Checkboxy-oświadczenia w sekcji -> jedno pole consent_section (Standardy: zgody)."""
        consent_fields = [
            f for f in section.fields
            if f.field_type == FieldType.CHECKBOX and not f.options and not f.dictionary_ref
            and rules.is_consent(f.label) and not f.dependencies
        ]
        if not consent_fields:
            return
        first = consent_fields[0]
        merged = FieldDefinition(
            field_id=ids.uid(f"{first.field_id}_zgody"),
            name=f"{first.name}_zgody",
            label="Oświadczenia i zgody",
            field_type=FieldType.CONSENT_SECTION,
            required=any(f.required for f in consent_fields),
            consents=[ConsentItem(description=f.label, required=f.required) for f in consent_fields],
        )
        index = section.fields.index(first)
        section.fields = [f for f in section.fields if f not in consent_fields]
        section.fields.insert(index, merged)

    @staticmethod
    def _detail_flows(section: SectionDefinition, form: FormDocumentSchema) -> None:
        """Opcja „inne”/„tak” + następne pole doprecyzowujące -> SHOW i REQUIRE tego pola."""
        targeted = {flow.target_id for flow in form.conditional_flows}
        targeted.update(d.target_id for f in section.fields for d in f.dependencies)
        existing = {_flow_key(flow) for flow in form.conditional_flows}
        for source, target in zip(section.fields, section.fields[1:], strict=False):
            if source.field_type not in CHOICE_FIELD_TYPES or not source.options:
                continue
            if target.field_type not in _INPUT_TEXT_TYPES or target.field_id in targeted:
                continue
            if not rules.DETAIL_LABEL.search(rules.norm(target.label)):
                continue
            option = next(
                (o for o in source.options if rules.OTHER_OPTION.search(rules.norm(o.label))), None
            ) or next((o for o in source.options if rules.YES_OPTION.search(rules.norm(o.label))), None)
            if option is None:
                continue
            for action in (FlowAction.SHOW, FlowAction.REQUIRE):
                flow = ConditionalFlow(
                    source_field_id=source.field_id,
                    operator=ConditionOperator.EQUALS,
                    value=option.label,
                    target_type=TargetType.FIELD,
                    target_id=target.field_id,
                    action=action,
                )
                if _flow_key(flow) not in existing:
                    form.conditional_flows.append(flow)
                    existing.add(_flow_key(flow))
            targeted.add(target.field_id)

    # ------------------------------------------------------------------ referencje i słowniki
    @staticmethod
    def _flows(form: FormDocumentSchema) -> Iterator[ConditionalFlow]:
        yield from form.conditional_flows
        for page in form.pages:
            yield from page.visibility_rules
            for section in page.sections:
                yield from section.visibility_rules
                for field in _iter_fields(section.fields):
                    yield from field.dependencies

    def _remap_flows(self, form: FormDocumentSchema, remap: dict[str, str]) -> None:
        """Przepina zależności z pól LLM zastąpionych polami kanonicznymi."""
        if not remap:
            return
        for flow in self._flows(form):
            flow.source_field_id = remap.get(flow.source_field_id, flow.source_field_id)
            if flow.target_type == TargetType.FIELD:
                flow.target_id = remap.get(flow.target_id, flow.target_id)

    def _build_dictionaries(self, form: FormDocumentSchema) -> None:
        block = form.dictionaries
        fields = {f.field_id: f for f in _all_fields(form)}
        canonical = {
            f.field_id
            for page in form.pages
            for section in page.sections
            if section.predefined
            for f in _iter_fields(section.fields)
        }
        previous = {m.field_id: m for m in block.mapped_dictionaries}
        proposed = {p.target_field_id: p for p in block.proposed_dictionaries if p.target_field_id in fields}
        proposed_ids = {p.proposed_id for p in proposed.values()}

        mapped: list[MappedDictionary] = []
        for field in fields.values():
            ref = field.dictionary_ref
            if ref and ref in self._dictionaries:
                old = previous.get(field.field_id)
                confidence = old.confidence if old and old.dictionary_id == ref else LABEL_MATCH_CONFIDENCE
                if field.options and rules.dictionary_for(field.label) is None:
                    match = self._match_options(field.options)
                    if match and match[0] == ref:
                        confidence = round(min(0.95, match[1] * 0.95), 2)
                mapped.append(
                    MappedDictionary(
                        field_id=field.field_id,
                        dictionary_id=ref,
                        dictionary_name=self._dictionaries[ref][0],
                        confidence=confidence,
                    )
                )
            elif ref and ref in proposed_ids:
                continue
            elif (
                ref is None
                and field.field_type in CHOICE_FIELD_TYPES
                and field.field_id not in proposed
                and field.field_id not in canonical
            ):
                labels = {rules.norm(o.label) for o in field.options or []}
                if len(field.options or []) >= MIN_PROPOSED_OPTIONS and not labels <= _YES_NO:
                    proposal = ProposedDictionary(
                        proposed_id=f"prop_{field.field_id}",
                        name=f"{field.label or field.name} (propozycja)",
                        target_field_id=field.field_id,
                        reason=(
                            "Lista wyboru wywnioskowana z treści formularza; brak odpowiadającego "
                            "słownika w lokalnej bazie COI."
                        ),
                        items=list(field.options or []),
                    )
                    proposed[field.field_id] = proposal
                    field.dictionary_ref = proposal.proposed_id
            elif ref and not any(p.proposed_id == ref for p in block.proposed_dictionaries):
                # Nieznany słownik bez definicji - zostaw opcje pola, usuń martwą referencję.
                field.dictionary_ref = None
        # Mapowania spoza bazy (np. ręczne) pozostają, jeśli pole istnieje.
        for old in block.mapped_dictionaries:
            known_field = old.field_id in fields and old.dictionary_id not in self._dictionaries
            if known_field and not any(m.field_id == old.field_id for m in mapped):
                mapped.append(old)
        block.mapped_dictionaries = mapped
        block.proposed_dictionaries = list(proposed.values())

    def _drop_dangling(self, form: FormDocumentSchema) -> None:
        """Usuwa zależności wskazujące nieistniejące pola/sekcje/strony (po zastąpieniach)."""
        field_ids = {f.field_id for f in _all_fields(form)}
        targets = {
            TargetType.FIELD: field_ids,
            TargetType.SECTION: {s.section_id for p in form.pages for s in p.sections},
            TargetType.PAGE: {p.page_id for p in form.pages},
        }

        def valid(flow: ConditionalFlow) -> bool:
            return flow.source_field_id in field_ids and flow.target_id in targets[flow.target_type]

        def dedupe(flows: list[ConditionalFlow]) -> list[ConditionalFlow]:
            seen: set[str] = set()
            result = []
            for flow in flows:
                key = _flow_key(flow)
                if valid(flow) and key not in seen:
                    seen.add(key)
                    result.append(flow)
            return result

        form.conditional_flows = dedupe(form.conditional_flows)
        for page in form.pages:
            page.visibility_rules = dedupe(page.visibility_rules)
            for section in page.sections:
                section.visibility_rules = dedupe(section.visibility_rules)
                for field in _iter_fields(section.fields):
                    field.dependencies = dedupe(field.dependencies)
