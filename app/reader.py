"""Step 3: file -> clean text -> position-preserving chunks. Plain code, no AI."""
import io
import re
from pathlib import Path

import docx
import pymupdf

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
MIN_TEXT_CHARS = 200
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


CHUNKER_VERSION = 2  # bump when chunking changes; stored chunks are rebuilt on the next start
SECTION_WORDS = re.compile(
    r"^(professional |personal |career |core |technical |key |selected |academic |work |relevant )*"
    r"(summary|profile|objective|about me|experience|work history|employment|education|qualifications?|skills|"
    r"competencies|projects?|certifications?|courses?|coursework|achievements|awards|publications|internships?|"
    r"languages|extracurriculars?|activities|interests|hobbies|contact|references|tools|technologies)\b",
    re.IGNORECASE)
BULLET_START = re.compile(r"^\s*(?:[•●▪◦·*\-–�]\s*|\d{1,2}[.)]\s+)")
LABEL_START = re.compile(r"^[A-Z][\w&/,.()+# -]{1,40}:(\s|$)")  # "Software & Integration: Flask, ..."
ENTRY_LINE = re.compile(r"\s\|\s|\b(19|20)\d{2}\s*[-–]\s*((19|20)\d{2}|present|current|now)\b", re.IGNORECASE)
SENTENCE = re.compile(r".+?(?:[.!?](?=\s)|$)", re.DOTALL)


def _is_heading(line: str, after_first_section: bool) -> bool:
    words = re.sub(r"[^A-Za-z&/ ]", "", line).strip()
    if not words or len(line) > 50 or ENTRY_LINE.search(line) or ":" in line.rstrip(":"):
        return False
    if SECTION_WORDS.match(words) and len(words.split()) <= 5:
        return True
    # An all-caps line above the first real section is the candidate's name or title, which must never
    # become context for the embeddings (no personal details in matching).
    return after_first_section and line.isupper() and len(words) >= 4


def chunk(text: str) -> list[dict]:
    """Rebuild logical units from visual PDF lines: one bullet, one 'Label: items' line, one entry line or one
    sentence group each, never crossing a section heading. Every chunk keeps its exact [start, end) offsets."""
    units: list[list] = []  # [start, end, section]
    section = ""
    for match in re.finditer(r"[^\n]+", text):
        line = match.group().strip()
        if not line:
            continue
        start = match.start() + match.group().index(line[0])
        if _is_heading(line, bool(section)):
            section = line.rstrip(":").title()
            units.append(None)  # hard break: nothing joins across a heading
            continue
        current = units[-1] if units else None
        unfinished = current is not None and not re.search(r"[.!?]\s*$", text[current[0]:current[1]])
        new_unit = BULLET_START.match(line) or LABEL_START.match(line) or ENTRY_LINE.search(line)
        if current is not None and unfinished and not new_unit:
            current[1] = match.end()  # wrapped line: continue the same sentence or bullet
        else:
            units.append([start, match.end(), section])

    chunks = []
    for unit in filter(None, units):
        start, end, unit_section = unit
        if end - start <= MAX_CHUNK_CHARS:
            chunks.append(_chunk(text, start, end, unit_section))
            continue
        group_start = group_end = start  # long paragraph: pack whole sentences up to the size limit
        for sentence in SENTENCE.finditer(text, start, end):
            if sentence.end() - group_start > MAX_CHUNK_CHARS and group_end > group_start:
                chunks.append(_chunk(text, group_start, group_end, unit_section))
                group_start = sentence.start()
            group_end = sentence.end()
        chunks.append(_chunk(text, group_start, group_end, unit_section))
    return [c for c in chunks if c["text"].strip()]


def _chunk(text: str, start: int, end: int, section: str = "") -> dict:
    while start < end and text[start].isspace():
        start += 1
    return {"text": text[start:end], "start": start, "end": end, "section": section}


def embedding_text(chunk: dict) -> str:
    """What gets embedded: the section gives a bare line its meaning ("Education: BTech, EEE ...")."""
    body = re.sub(r"\s+", " ", BULLET_START.sub("", chunk["text"])).strip()
    return f"{chunk['section']}: {body}" if chunk.get("section") else body


def quality(text: str, chunks: list[dict]) -> str:
    """'ok' or the reason the resume goes to Needs attention instead of being scored."""
    if len(text.strip()) < MIN_TEXT_CHARS:
        return "text_too_short"
    if not chunks:
        return "no_readable_chunks"
    return "ok"
