"""Draft a job (title, description, checklist) from JD text with plain rules. No AI: HR reviews every item before approval."""
import re

from app.schemas import PROTECTED_TERMS

MAX_ITEMS = 15
MAX_TEXT = 300
BULLET = re.compile(r"^\s*(?:[•●▪◦·–*-]|\d{1,2}[.)]|[a-z][.)])\s+")
REQ_HEADING = re.compile(r"requirement|qualification|skill|tech stack|must[- ]?have|looking for|eligibility|who you are|you have",
                         re.IGNORECASE)
MUST_WORDS = re.compile(r"\b(must|mandatory|required|essential)\b", re.IGNORECASE)
METADATA = re.compile(r"^\W*(?!note\b)[A-Za-z][\w /&-]{0,30}:\s", re.IGNORECASE)  # "Experience Required: Fresher"
OPEN_END = re.compile(r"(,|\b(or|and|of|the|to|with|in|for|a|an|such as))$", re.IGNORECASE)
YEARS = re.compile(r"(\d{1,2})\s*(?:\+|(?:-|–|to)\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", BULLET.sub("", line).replace("*", "")).strip(" :;,")


def _is_heading(line: str) -> bool:
    """'Requirements', 'Tech Stack & Requirements:', 'Day to day you will:' are headings; 'Cloud: Azure' is an item."""
    text = line.replace("*", "").strip()
    if not text or BULLET.match(line) or not (text[0].isupper() or text[0].isdigit()):
        return False
    if text.endswith(":"):
        return True
    return len(text) <= 60 and ":" not in text and not text.endswith(".")


def draft_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if match := re.match(r"\W*job\s*title\s*:\s*(.+)", line, re.IGNORECASE):
            return match.group(1).strip()[:200]
        if "hiring" in line.lower() and "|" in line:
            parts = [p.strip() for p in line.split("|")]
            return re.sub(r"^\W+", "", parts[1])[:200] if len(parts) > 1 else fallback
    return fallback


def draft_requirements(text: str) -> list[dict]:
    items: list[dict] = []  # {"text", "must"}; ids and weights are assigned at the end
    in_requirements = must_section = False
    previous_was_item = False
    all_bullets: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if previous_was_item and not BULLET.match(line) and (line[0].islower() or OPEN_END.search(items[-1]["text"])):
            # wrapped continuation of the previous bullet
            items[-1]["text"] += " " + _clean(line)
            continue
        if _is_heading(line):
            in_requirements = bool(REQ_HEADING.search(line))
            must_section = bool(MUST_WORDS.search(line)) or "must" in line.lower()
            previous_was_item = False
            continue
        if BULLET.match(line):
            all_bullets.append(_clean(line))
        if in_requirements:
            items.append({"text": _clean(line), "must": must_section or bool(MUST_WORDS.search(line))})
            previous_was_item = True
        elif MUST_WORDS.search(line) and not METADATA.match(line) and not re.search(r"how to apply|submit", line, re.IGNORECASE):
            # "Note: Excellent communication skills are must" outside any requirements section.
            items.append({"text": _clean(re.sub(r"^\W*note\s*:", "", line, flags=re.IGNORECASE)), "must": True})
            previous_was_item = True
        else:
            previous_was_item = False

    if not items:  # no recognisable requirements section: fall back to every bullet
        items = [{"text": b, "must": False} for b in all_bullets]

    if (years := YEARS.search(text)) and not any("year" in i["text"].lower() for i in items):
        items.insert(0, {"text": f"{years.group(1)}+ years of professional experience", "must": False})

    seen, result = set(), []
    for item in items:
        body = item["text"][:MAX_TEXT].rsplit(" ", 1)[0] if len(item["text"]) > MAX_TEXT else item["text"]
        key = body.lower()
        if len(body) < 3 or key in seen or PROTECTED_TERMS.search(body):
            continue
        seen.add(key)
        result.append({"id": f"R{len(result) + 1}", "text": body, "type": "must" if item["must"] else "nice",
                       "weight": 4 if item["must"] else 2})
        if len(result) == MAX_ITEMS:
            break
    return result


def draft_job(text: str, fallback_title: str) -> dict:
    return {"title": draft_title(text, fallback_title), "description": text.strip()[:20000],
            "requirements": draft_requirements(text)}
