"""Convert one document to an EPUB for the ``send-file`` path (SAT-830).

A sibling of the RSS digest pipeline: no fetch, no dedup, no processed state.
The EPUB is assembled with ``job_epub.build_job_epub`` — the same builder the
digests use — so a one-off document inherits the Kindle-tested table CSS and
paragraph spacing rather than growing a second, divergent renderer.

The entry points take ``bytes`` plus a filename, not a path: in a serverless
run the document arrives as an upload body or an object fetch and never touches
a local filesystem. Reading a file is the CLI's job.

PDF text comes from ``pdfplumber`` (pure Python, MIT). Chosen over ``pypdf``,
which silently drops ruled tables — an 8-page source whose only data table
vanished is what motivated the switch — and over a ``pdftotext`` shell-out,
which would put a system package in the deploy image and make output depend on
which host ran it. ``pdfplumber`` returns tables as rows/columns, so they are
re-rendered as Markdown tables instead of relying on whitespace alignment
surviving the trip.

Scanned/image-only PDFs yield no text: there is no OCR on this path. That case
raises rather than delivering a blank EPUB.
"""

from __future__ import annotations

import io
from pathlib import PurePath

from .job_epub import JobSection, build_job_epub

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
PDF_SUFFIX = ".pdf"
SUPPORTED_SUFFIXES = MARKDOWN_SUFFIXES | {PDF_SUFFIX}


class UnsupportedFileType(ValueError):
    """The input file's extension is not one this path can convert."""


def read_as_markdown(data: bytes, *, filename: str) -> str:
    """Return ``data`` as Markdown source, dispatching on ``filename``'s suffix.

    Markdown and plain text are decoded verbatim; PDFs are extracted page by
    page. Raises ``UnsupportedFileType`` for any other extension.
    """
    suffix = PurePath(filename).suffix.lower()
    if suffix == PDF_SUFFIX:
        return _extract_pdf(data)
    if suffix in MARKDOWN_SUFFIXES:
        return data.decode("utf-8")
    raise UnsupportedFileType(
        f"cannot convert {suffix!r}; supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )


def _extract_pdf(data: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [_page_markdown(page) for page in pdf.pages]
    # Blank-line join keeps page boundaries as paragraph breaks, so the last
    # line of one page does not run into the first line of the next.
    return "\n\n".join(p for p in pages if p.strip())


def _page_markdown(page) -> str:
    """Page text with any ruled tables re-rendered as Markdown.

    Table regions are cut out of the text layer first, otherwise every cell
    would appear twice — once as loose text, once inside the Markdown table.
    Tables are appended after the page's prose rather than interleaved at their
    original position; page order is preserved, position within a page is not.
    """
    tables = page.find_tables()
    if not tables:
        return page.extract_text() or ""

    prose_region = page
    for table in tables:
        prose_region = prose_region.outside_bbox(table.bbox)

    parts = [prose_region.extract_text() or ""]
    parts.extend(_table_to_markdown(table.extract()) for table in tables)
    return "\n\n".join(part for part in parts if part.strip())


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    """Render extracted cells as a Markdown table, or "" if there is nothing in it."""
    cleaned = [
        [(cell or "").replace("\n", " ").replace("|", r"\|").strip() for cell in row]
        for row in rows
    ]
    cleaned = [row for row in cleaned if any(cell for cell in row)]
    if not cleaned:
        return ""

    header, *body = cleaned
    width = len(header)
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * width) + "|",
    ]
    for row in body:
        # Pad short rows and drop overflow so every row matches the header
        # width — Markdown renderers silently mangle ragged tables.
        cells = (row + [""] * width)[:width]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def title_from_filename(filename: str) -> str:
    """Human-readable title from a filename (``my-note.md`` -> ``my note``)."""
    stem = PurePath(filename).stem
    return stem.replace("-", " ").replace("_", " ").strip() or stem


def build_single_file_epub(
    data: bytes, *, filename: str, title: str | None = None
) -> bytes:
    """Build a one-section EPUB from ``data``.

    Raises ``ValueError`` when the document yields no usable text — a scanned
    PDF would otherwise be delivered as a silently blank document.
    """
    markdown = read_as_markdown(data, filename=filename)
    if not markdown.strip():
        raise ValueError(
            f"no text could be extracted from {filename} "
            "(an image-only or scanned PDF has no text layer)"
        )
    book_title = title or title_from_filename(filename)
    return build_job_epub(
        [JobSection(title=book_title, markdown=markdown)],
        book_title=book_title,
    )
