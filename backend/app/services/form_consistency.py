"""Walidacja spójności wygenerowanego JSON-a formularza COI (ponad sam schemat).

Sprawdza unikalność identyfikatorów oraz poprawność referencji w przepływach
warunkowych i słownikach. Używane przez POST /forms/validate-json.
"""

from backend.app.schemas.form_schema import (
    ConditionalFlow,
    FlowAction,
    FormDocumentSchema,
    TargetType,
)


def validate_form_consistency(
    form: FormDocumentSchema, known_dictionary_ids: set[str] | None = None
) -> tuple[list[str], list[str]]:
    """Zwraca (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    page_ids: set[str] = set()
    section_ids: set[str] = set()
    field_ids: set[str] = set()

    def _collect_fields(fields: list, owner: str) -> None:
        for field in fields:
            if field.field_id in field_ids:
                errors.append(f"Zduplikowany field_id: '{field.field_id}' (w {owner}).")
            field_ids.add(field.field_id)
            if field.fields:
                _collect_fields(field.fields, f"grupa {field.field_id}")

    for page in form.pages:
        if page.page_id in page_ids:
            errors.append(f"Zduplikowany page_id: '{page.page_id}'.")
        page_ids.add(page.page_id)
        for section in page.sections:
            if section.section_id in section_ids:
                errors.append(f"Zduplikowany section_id: '{section.section_id}'.")
            section_ids.add(section.section_id)
            _collect_fields(section.fields, f"sekcja {section.section_id}")

    known_dictionary_ids = known_dictionary_ids or set()
    proposed_ids = {p.proposed_id for p in form.dictionaries.proposed_dictionaries}
    mapped_ids = {m.dictionary_id for m in form.dictionaries.mapped_dictionaries}
    all_dictionary_ids = known_dictionary_ids | proposed_ids | mapped_ids

    def _check_flow(flow: ConditionalFlow, context: str) -> None:
        if flow.source_field_id not in field_ids:
            errors.append(
                f"{context}: reguła odwołuje się do nieistniejącego pola źródłowego "
                f"'{flow.source_field_id}'."
            )
        targets = {
            TargetType.FIELD: field_ids,
            TargetType.SECTION: section_ids,
            TargetType.PAGE: page_ids,
        }
        if flow.target_id not in targets[flow.target_type]:
            errors.append(
                f"{context}: reguła odwołuje się do nieistniejącego celu "
                f"{flow.target_type.value} '{flow.target_id}'."
            )
        if flow.action == FlowAction.SET_VALUE and flow.action_value is None:
            warnings.append(
                f"{context}: akcja SET_VALUE dla '{flow.target_id}' nie ma action_value."
            )

    for page in form.pages:
        for flow in page.visibility_rules:
            _check_flow(flow, f"strona {page.page_id}")
        for section in page.sections:
            for flow in section.visibility_rules:
                _check_flow(flow, f"sekcja {section.section_id}")
            for field in section.fields:
                for flow in field.dependencies:
                    _check_flow(flow, f"pole {field.field_id}")
                if field.dictionary_ref and field.dictionary_ref not in all_dictionary_ids:
                    errors.append(
                        f"pole {field.field_id}: dictionary_ref '{field.dictionary_ref}' "
                        "nie istnieje w bazie COI ani w proponowanych słownikach."
                    )
    for flow in form.conditional_flows:
        _check_flow(flow, "conditional_flows")

    for mapped in form.dictionaries.mapped_dictionaries:
        if mapped.field_id not in field_ids:
            errors.append(
                f"mapped_dictionaries: pole '{mapped.field_id}' nie istnieje w formularzu."
            )
        if known_dictionary_ids and mapped.dictionary_id not in known_dictionary_ids:
            warnings.append(
                f"mapped_dictionaries: słownik '{mapped.dictionary_id}' nie występuje "
                "w lokalnej bazie COI."
            )
    for proposed in form.dictionaries.proposed_dictionaries:
        if proposed.target_field_id not in field_ids:
            errors.append(
                f"proposed_dictionaries: pole docelowe '{proposed.target_field_id}' "
                "nie istnieje w formularzu."
            )
        if not proposed.items:
            warnings.append(
                f"proposed_dictionaries: słownik '{proposed.proposed_id}' nie ma pozycji."
            )

    return errors, warnings
