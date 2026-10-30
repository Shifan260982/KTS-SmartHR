"""Step 3: file -> clean text -> position-preserving chunks. Plain code, no AI."""
import io
import re
from pathlib import Path

import docx
import pymupdf

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
MIN_TEXT_CHARS = 200
MIN_CHUNK_CHARS = 60
MAX_CHUNK_CHARS = 400
WHITE = 0xFFFFFF
MIN_FONT_SIZE = 4


def extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}")
    if ext == ".pdf":
        if not data.startswith(b"%PDF"):
            raise ValueError("File is not a valid PDF")
        return _pdf_text(data)
    if ext == ".docx":
        if not data.startswith(b"PK"):
            raise ValueError("File is not a valid DOCX")
        return _docx_text(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _pdf_text(data: bytes) -> str:
    lines = []
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        if pdf.needs_pass:
            raise ValueError("PDF is password protected")
        for page in pdf:
            for block in page.get_text("dict", sort=True)["blocks"]:
                for line in block.get("lines", []):
                    # Drop white or tiny spans: a known trick to stuff invisible keywords into a resume.
                    text = "".join(
                        span["text"] for span in line["spans"]
                        if span["size"] >= MIN_FONT_SIZE and span["color"] != WHITE
                    ).strip()
                    if text:
                        lines.append(text)
    # Word's Symbol/Wingdings bullets arrive as private-use code points that render as boxes.
    return re.sub(r"[-]", "•", "\n".join(lines))


def _docx_text(data: bytes) -> str:
    document = docx.Document(io.BytesIO(data))
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(line.strip() for line in lines if line.strip())


def chunk(text: str) -> list[dict]:
    """Merge short lines, split long ones at sentence ends. Every chunk keeps its exact [start, end) offsets."""
    units = []
    for line in re.finditer(r"[^\n]+", text):
        if len(line.group()) <= MAX_CHUNK_CHARS:
            units.append((line.start(), line.end()))
        else:
            units.extend((line.start() + s.start(), line.start() + s.end())
                         for s in re.finditer(r".+?(?:[.!?](?=\s)|$)", line.group()))

    chunks, start, end = [], None, None
    for unit_start, unit_end in units:
        if not text[unit_start:unit_end].strip():
            continue
        if start is not None and unit_end - start > MAX_CHUNK_CHARS:
            chunks.append(_chunk(text, start, end))
            start = None
        if start is None:
            start = unit_start
        end = unit_end
        if end - start >= MIN_CHUNK_CHARS:
            chunks.append(_chunk(text, start, end))
            start = None
    if start is not None:
        chunks.append(_chunk(text, start, end))
    return chunks


def _chunk(text: str, start: int, end: int) -> dict:
    return {"text": text[start:end], "start": start, "end": end}


def quality(text: str, chunks: list[dict]) -> str:
    """'ok' or the reason the resume goes to Needs attention instead of being scored."""
    if len(text.strip()) < MIN_TEXT_CHARS:
        return "text_too_short"
    if not chunks:
        return "no_readable_chunks"
    return "ok"
