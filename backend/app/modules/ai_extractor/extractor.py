"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Lokalny OCR (RapidOCR/onnxruntime, fallback pytesseract) + lokalny model AI/LLM
(llama-cpp-python, wagi GGUF w MODELS_DIR) rozpoznający układ dokumentu:
strony tematyczne, sekcje, etykiety i typy kontrolek -> FormDocumentSchema.
100% offline, niski koszt CPU/RAM. Zero wywołań zewnętrznych API.
"""

from pathlib import Path

from backend.app.interfaces.pipeline import ParsedDocument
from backend.app.schemas.form_schema import FormDocumentSchema


class LocalStructureExtractor:
    """Implementacja protokołu StructureExtractor."""

    def __init__(self, models_dir: Path) -> None:
        self._models_dir = models_dir

    def extract(self, parsed: ParsedDocument) -> FormDocumentSchema:
        # TODO(branch-1):
        #   1. OCR dla stron obrazowych (RapidOCR, CPU, modele w MODELS_DIR).
        #   2. Lokalny LLM (llama-cpp-python): podział na strony tematyczne,
        #      wykrywanie sekcji, etykiet pól i typów kontrolek (FieldType).
        #   3. Budowa FormDocumentSchema (metadata.ai_agent_ready=True).
        raise NotImplementedError("TODO(branch-1): lokalny OCR + ekstrakcja struktury AI")
