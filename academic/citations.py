from __future__ import annotations

from .models import AcademicWork


def _author_last(name: str) -> str:
    parts = [p for p in name.replace(",", " ").split() if p]
    return parts[-1].upper() if parts else "AUTOR DESCONHECIDO"


def format_reference(work: AcademicWork, style: str = "abnt") -> str:
    style = (style or "abnt").lower()
    authors = work.authors or ["Autor desconhecido"]
    year = str(work.year or "s.d.")
    doi_url = f"https://doi.org/{work.doi}" if work.doi else work.url
    if style == "apa":
        author_text = ", ".join(authors[:6]) + (", et al." if len(authors) > 6 else "")
        venue = f" {work.venue}." if work.venue else ""
        return f"{author_text} ({year}). {work.title}.{venue} {doi_url}".strip()
    if style == "chicago":
        author_text = ", ".join(authors[:3]) + (", et al." if len(authors) > 3 else "")
        venue = f" {work.venue}" if work.venue else ""
        return f'{author_text}. "{work.title}."{venue} ({year}). {doi_url}'.strip()
    if style == "vancouver":
        author_text = ", ".join(authors[:6]) + (" et al" if len(authors) > 6 else "")
        return f"{author_text}. {work.title}. {work.venue or work.source}. {year}. {doi_url}".strip()
    if style == "bibtex":
        key = _author_last(authors[0]).title() + year
        fields = [
            f"  title = {{{work.title}}}",
            f"  author = {{{' and '.join(authors)}}}",
            f"  year = {{{year}}}",
        ]
        if work.venue:
            fields.append(f"  journal = {{{work.venue}}}")
        if work.doi:
            fields.append(f"  doi = {{{work.doi}}}")
        if work.url:
            fields.append(f"  url = {{{work.url}}}")
        return "@article{" + key + ",\n" + ",\n".join(fields) + "\n}"
    author_text = "; ".join(_author_last(a) + ", " + " ".join(a.split()[:-1]) for a in authors[:6])
    if len(authors) > 6:
        author_text += " et al."
    venue = f" {work.venue}," if work.venue else ""
    return f"{author_text}. **{work.title}**.{venue} {year}. {doi_url}".strip()


def bibliography(works: list[AcademicWork], style: str = "abnt", limit: int = 12) -> str:
    return "\n".join(f"{i}. {format_reference(work, style)}" for i, work in enumerate(works[:limit], start=1))
