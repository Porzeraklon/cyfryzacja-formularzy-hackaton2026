"""Polityka licencyjna: ZAKAZ GPL/AGPL wśród zainstalowanych zależności.

Dozwolone wyłącznie licencje permisywne (MIT, Apache-2.0, BSD, ISC, PSF, MPL-2.0).
Wyjątek: pakiety dual-licensed z opcją permisywną (wybieramy opcję permisywną).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Pakiety dual-licensed, dla których wybieramy licencję permisywną.
DUAL_LICENSE_ALLOWLIST = {
    "odfpy": "Apache-2.0 OR GPL-2.0-or-later — wybieramy Apache-2.0",
}

_COPYLEFT_RE = re.compile(r"\b(A?GPL|LGPL)\b|GNU\s+(Affero\s+)?General\s+Public", re.IGNORECASE)


def installed_licenses() -> list[dict]:
    result = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_no_gpl_agpl_dependencies():
    try:
        packages = installed_licenses()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        pytest.fail(f"pip-licenses niedostępny (pip install -e '.[dev]'): {exc}")

    offenders = []
    for package in packages:
        name = package.get("Name", "")
        license_text = package.get("License", "") or ""
        if name.lower() in DUAL_LICENSE_ALLOWLIST:
            continue
        if _COPYLEFT_RE.search(license_text):
            offenders.append(f"{name} {package.get('Version', '')}: {license_text}")

    assert not offenders, (
        "Wykryto zależności na licencjach copyleft (GPL/AGPL/LGPL) — ZAKAZANE:\n"
        + "\n".join(offenders)
        + "\nZastąp je odpowiednikami permisywnymi (np. pypdf zamiast PyMuPDF)."
    )


def test_core_dependencies_declare_permissive_licenses():
    """Kontrola prewencyjna: żadna zależność nie może być PyMuPDF/fitz (AGPL)."""
    import tomllib

    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    project = pyproject["project"]
    declarations = list(project["dependencies"])
    for extra_deps in project.get("optional-dependencies", {}).values():
        declarations.extend(extra_deps)
    banned = {"pymupdf", "pymupdfb", "fitz"}
    for declaration in declarations:
        name = re.split(r"[<>=!~\[;\s]", declaration.strip(), maxsplit=1)[0].lower()
        assert name not in banned, f"Zakazana zależność {name} (AGPL) w pyproject.toml"
