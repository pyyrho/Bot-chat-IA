from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass


@dataclass(slots=True)
class DocumentExtraction:
    filename: str
    text: str
    pages: int | None = None
    truncated: bool = False
    warnings: list[str] | None = None


class DocumentProcessor:
    TEXT_EXTENSIONS = {".txt", ".md", ".rst", ".csv", ".json", ".py", ".js", ".ts", ".html", ".xml", ".tex"}

    def __init__(self) -> None:
        self.max_bytes = max(256_000, int(os.getenv("AI_DOCUMENT_MAX_BYTES", "8388608")))
        self.max_chars = max(8_000, int(os.getenv("AI_DOCUMENT_MAX_CHARS", "70000")))
        self.max_pages = max(5, int(os.getenv("AI_DOCUMENT_MAX_PAGES", "80")))

    def extract(self, data: bytes, filename: str, content_type: str = "") -> DocumentExtraction:
        if len(data) > self.max_bytes:
            raise ValueError(f"Arquivo excede o limite de {self.max_bytes // 1024 // 1024} MB")
        extension = os.path.splitext(filename.lower())[1]
        if extension == ".pdf" or content_type == "application/pdf":
            return self._extract_pdf(data, filename)
        if extension in self.TEXT_EXTENSIONS or content_type.startswith("text/"):
            text = self._decode(data)
            if extension == ".json":
                try:
                    text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
                except json.JSONDecodeError:
                    pass
            return self._finish(filename, text)
        raise ValueError("Formato não suportado. Envie PDF, TXT, Markdown, CSV, JSON, código ou texto simples.")

    def compact_for_prompt(self, text: str, focus: str = "", max_chars: int = 11500) -> str:
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) <= max_chars:
            return text
        chunks = self._chunks(text)
        terms = {t for t in re.findall(r"[\wÀ-ÿ-]{4,}", focus.lower())}
        scored: list[tuple[float, int, str]] = []
        for index, chunk in enumerate(chunks):
            lower = chunk.lower()
            overlap = sum(1 for term in terms if term in lower)
            heading = 1.2 if re.search(r"(?:^|\n)(?:#{1,4}\s|[A-ZÀ-Ý][A-ZÀ-Ý\s]{6,}:?)", chunk) else 0.0
            edge = 0.7 if index < 2 or index >= len(chunks) - 2 else 0.0
            scored.append((overlap * 1.7 + heading + edge, index, chunk))
        selected = sorted(sorted(scored, reverse=True)[: max(6, max_chars // 1400)], key=lambda x: x[1])
        output: list[str] = []
        used = 0
        for _, index, chunk in selected:
            label = f"[Trecho {index + 1}]\n"
            remaining = max_chars - used - len(label)
            if remaining <= 200:
                break
            piece = chunk[:remaining]
            output.append(label + piece)
            used += len(label) + len(piece) + 2
        return "\n\n".join(output)

    def _extract_pdf(self, data: bytes, filename: str) -> DocumentExtraction:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Instale pypdf para analisar PDFs: pip install pypdf") from exc
        reader = PdfReader(io.BytesIO(data))
        warnings: list[str] = []
        pages: list[str] = []
        for index, page in enumerate(reader.pages[: self.max_pages]):
            try:
                value = page.extract_text() or ""
            except Exception:
                value = ""
            if value.strip():
                pages.append(f"[Página {index + 1}]\n{value}")
        if not pages:
            warnings.append("O PDF não possui texto extraível; pode ser uma digitalização sem OCR.")
        if len(reader.pages) > self.max_pages:
            warnings.append(f"Somente as primeiras {self.max_pages} páginas foram processadas.")
        result = self._finish(filename, "\n\n".join(pages))
        result.pages = len(reader.pages)
        result.warnings = warnings
        return result

    def _finish(self, filename: str, text: str) -> DocumentExtraction:
        clean = text.replace("\x00", "").strip()
        truncated = len(clean) > self.max_chars
        return DocumentExtraction(filename, clean[: self.max_chars], truncated=truncated, warnings=[])

    @staticmethod
    def _decode(data: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _chunks(text: str, size: int = 1800) -> list[str]:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if current and len(current) + len(paragraph) + 2 > size:
                chunks.append(current)
                current = paragraph
            else:
                current = f"{current}\n\n{paragraph}".strip()
        if current:
            chunks.append(current)
        return chunks
