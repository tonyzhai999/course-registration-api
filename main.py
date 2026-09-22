import re
from typing import Any

from bs4 import BeautifulSoup
from fastapi import FastAPI, File, HTTPException, UploadFile

app = FastAPI(title="Course Catalog API")

# In-memory catalog. Key = normalized course code (no spaces), value = course dict.
catalog: dict[str, dict[str, Any]] = {}

COURSE_CODE_RE = re.compile(r"\b[A-Za-z]{2,}\s*\d{3,4}[A-Za-z]?\b")


def normalize_code(code: str) -> str:
    """COSC 3506, COSC3506, and cosc-3506 all become COSC3506."""
    return re.sub(r"[^A-Za-z0-9]", "", code or "").upper()


def parse_course_list(text: str) -> list[str]:
    """Turn messy prerequisite / cross-list text into a list of course codes."""
    raw = (text or "").strip()
    if not raw or raw.lower() in {"none", "n/a", "na", "-", "—", "nil"}:
        return []

    found = COURSE_CODE_RE.findall(raw)
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in found:
        pretty = " ".join(item.split()).upper()
        key = normalize_code(pretty)
        if key and key not in seen:
            seen.add(key)
            cleaned.append(pretty)
    return cleaned


def parse_html(html: str) -> dict[str, dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("No table found in catalog HTML")

    rows = table.find_all("tr")
    if len(rows) < 2:
        raise ValueError("Catalog table has no data rows")

    headers = [cell.get_text(" ", strip=True).lower() for cell in rows[0].find_all(["th", "td"])]

    def col(*names: str) -> int | None:
        for i, header in enumerate(headers):
            if any(name in header for name in names):
                return i
        return None

    i_code = col("course code", "code")
    i_title = col("title", "name")
    i_credits = col("credit")
    i_prereq = col("prerequisite")
    i_cross = col("cross")

    if i_code is None or i_title is None:
        raise ValueError("Could not identify course code / title columns")

    parsed: dict[str, dict[str, Any]] = {}
    for row in rows[1:]:
        cells = [td.get_text(" ", strip=True) for td in row.find_all(["td", "th"])]
        if not cells or i_code >= len(cells):
            continue

        code = cells[i_code].strip()
        if not code:
            continue

        credits_raw = cells[i_credits] if i_credits is not None and i_credits < len(cells) else "0"
        digits = re.sub(r"[^\d.]", "", credits_raw) or "0"
        try:
            credits = int(float(digits))
        except ValueError:
            credits = 0

        course = {
            "course_code": code,
            "title": cells[i_title] if i_title < len(cells) else "",
            "credits": credits,
            "prerequisites": parse_course_list(
                cells[i_prereq] if i_prereq is not None and i_prereq < len(cells) else ""
            ),
            "cross_listed": parse_course_list(
                cells[i_cross] if i_cross is not None and i_cross < len(cells) else ""
            ),
        }
        parsed[normalize_code(code)] = course

    return parsed


@app.post("/api/v1/admin/catalog/import")
async def import_catalog(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("latin-1")

    try:
        parsed = parse_html(html)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    catalog.clear()
    catalog.update(parsed)
    return {"imported": len(parsed)}


@app.get("/api/v1/catalog/courses/{course_code}")
def get_course(course_code: str):
    course = catalog.get(normalize_code(course_code))
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course