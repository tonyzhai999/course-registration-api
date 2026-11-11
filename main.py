import re
from typing import Any

from bs4 import BeautifulSoup
from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Student Academic History API")

# Per-student in-memory store. A student exists once history has been imported.
# { student_id: {"history": [...], "plan": [...]} }
students: dict[str, dict[str, list]] = {}

VALID_STATUSES = {"completed", "in-progress", "attempted"}


class HistoryItem(BaseModel):
    course_code: str
    term: str
    credits_earned: int = 0
    status: str


class HistoryUpdate(BaseModel):
    history: list[HistoryItem]


class PlanItem(BaseModel):
    course_code: str
    term: str


class PlanUpdate(BaseModel):
    planned_courses: list[PlanItem]


def student_exists(student_id: str) -> bool:
    return student_id in students


def ensure_student(student_id: str) -> dict:
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    return students[student_id]


def grade_score(grade: str) -> int:
    """Numeric > letter > other. Higher = more informative."""
    g = (grade or "").strip()
    if not g:
        return 0
    if re.fullmatch(r"\d+(\.\d+)?", g):
        return 100
    if re.fullmatch(r"[A-Fa-f][+-]?", g):
        return 50
    return 10


def parse_credits(text: str) -> int:
    raw = (text or "").strip()
    if not raw:
        return 0
    # Allow "3", "3.0", "3 credits"
    m = re.search(r"-?\d+(?:\.\d+)?", raw)
    if not m:
        return 0
    try:
        return int(float(m.group(0)))
    except ValueError:
        return 0


def header_index(headers: list[str], *needles: str) -> int | None:
    for i, h in enumerate(headers):
        if any(n in h for n in needles):
            return i
    return None


def parse_transcript(html: str) -> list[dict[str, Any]]:
    """Extract past courses using the canonical rule and deduplicate."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict[str, Any]] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = [
            cell.get_text(" ", strip=True).lower()
            for cell in rows[0].find_all(["th", "td"])
        ]
        i_status = header_index(headers, "status")
        i_course = header_index(headers, "course")
        i_grade = header_index(headers, "grade")
        i_term = header_index(headers, "term")
        i_credits = header_index(headers, "credit")

        # Must have the core columns. Title is optional.
        if None in (i_status, i_course, i_term, i_credits):
            continue

        for row in rows[1:]:
            cells = [td.get_text(" ", strip=True) for td in row.find_all(["td", "th"])]
            if not cells:
                continue

            def cell(idx: int | None) -> str:
                if idx is None or idx >= len(cells):
                    return ""
                return cells[idx]

            status = cell(i_status)
            course = cell(i_course)
            term = cell(i_term)
            grade = cell(i_grade)
            credits = cell(i_credits)

            if not course:
                continue
            # Canonical filter
            if status.strip().lower() not in VALID_STATUSES:
                continue
            if not term.strip():
                continue

            candidates.append(
                {
                    "course_code": course,  # verbatim
                    "term": term,           # verbatim
                    "credits_earned": parse_credits(credits),
                    "status": status,       # verbatim
                    "_grade": grade,
                    "_score": grade_score(grade),
                }
            )

    # Deduplicate by (course_code, term), keep most informative grade.
    # Preserve first-seen order.
    best: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for item in candidates:
        key = (item["course_code"], item["term"])
        if key not in best:
            best[key] = item
            order.append(key)
        elif item["_score"] > best[key]["_score"]:
            best[key] = item

    result = []
    for key in order:
        rec = best[key]
        result.append(
            {
                "course_code": rec["course_code"],
                "term": rec["term"],
                "credits_earned": rec["credits_earned"],
                "status": rec["status"],
            }
        )
    return result


@app.post("/api/v1/students/{student_id}/history/import", status_code=201)
async def import_history(student_id: str, file: UploadFile = File(...)):
    raw = await file.read()
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("latin-1")

    history = parse_transcript(html)
    students[student_id] = {"history": history, "plan": []}
    return {"status": "success", "past_courses_imported": len(history)}


@app.put("/api/v1/students/{student_id}/history")
def update_history(student_id: str, body: HistoryUpdate):
    rec = ensure_student(student_id)
    rec["history"] = [item.model_dump() for item in body.history]
    return {
        "status": "success",
        "message": "Academic history updated successfully",
    }


@app.delete("/api/v1/students/{student_id}/history")
def delete_history(student_id: str):
    rec = ensure_student(student_id)
    rec["history"] = []
    return {"status": "success", "message": "Academic history cleared"}


@app.post("/api/v1/students/{student_id}/plan")
def create_plan(student_id: str, body: PlanUpdate):
    rec = ensure_student(student_id)
    rec["plan"] = [item.model_dump() for item in body.planned_courses]
    return {"status": "success", "planned_courses_saved": len(rec["plan"])}


@app.put("/api/v1/students/{student_id}/plan")
def replace_plan(student_id: str, body: PlanUpdate):
    rec = ensure_student(student_id)
    rec["plan"] = [item.model_dump() for item in body.planned_courses]
    return {
        "status": "success",
        "message": "Plan updated successfully",
        "planned_courses_saved": len(rec["plan"]),
    }


@app.delete("/api/v1/students/{student_id}/plan")
def delete_plan(student_id: str):
    rec = ensure_student(student_id)
    rec["plan"] = []
    return {"status": "success", "message": "Plan cleared"}


@app.get("/api/v1/students/{student_id}/profile")
def get_profile(student_id: str):
    rec = ensure_student(student_id)
    return {
        "student_id": student_id,
        "history": rec["history"],
        "plan": rec["plan"],
    }

# Phase 3: catalog ingestion and academic audit
from audit_engine import router as audit_router
app.include_router(audit_router)
