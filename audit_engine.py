"""Phase 3 catalog ingestion and student academic audit.

The router uses the Phase 2 ``main.students`` in-memory per-student store.
"""
import re
from collections import defaultdict
from typing import Any
from bs4 import BeautifulSoup
from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter()
catalog: dict[str, dict[str, Any]] = {}
COURSE_RE = re.compile(r'\b([A-Za-z]{2,10})\s*[- ]?\s*(\d{4}[A-Za-z]?)\b')
SEASONS = {'W': 0, 'SP': 1, 'S': 2, 'F': 3}


def normalize_course(code: str) -> str:
    """Normalize both hyphens and whitespace (COSC3506 == COSC-3506)."""
    return re.sub(r'[\s-]+', '', str(code)).upper()


def display_course(code: str) -> str:
    normalized = normalize_course(code)
    m = re.fullmatch(r'([A-Z]+)(\d+[A-Z]?)', normalized)
    return f'{m.group(1)}-{m.group(2)}' if m else str(code).strip().upper()


def extract_codes(text: str) -> list[str]:
    result = []
    for m in COURSE_RE.finditer(text or ''):
        code = normalize_course(m.group())
        if code not in result:
            result.append(code)
    return result


def catalog_credits(raw: str) -> int:
    match = re.search(r'\d+(?:\.\d+)?', str(raw))
    return int(float(match.group())) if match else 0


def import_catalog_html(html: str) -> int:
    """Parse tables by header names, replacing previous imported catalog atomically."""
    soup = BeautifulSoup(html, 'html.parser')
    parsed = {}
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if not rows:
            continue
        headers = [c.get_text(' ', strip=True).lower() for c in rows[0].find_all(['td', 'th'])]
        def index(*terms):
            return next((i for i, h in enumerate(headers) if any(t in h for t in terms)), None)
        code_idx = index('course code', 'code')
        credit_idx = index('credit')
        title_idx = index('title', 'name')
        prereq_idx = index('prerequisite', 'pre-requisite', 'prereq')
        cross_idx = index('cross-list', 'cross list')
        if code_idx is None or credit_idx is None:
            continue
        for tr in rows[1:]:
            values = [c.get_text(' ', strip=True) for c in tr.find_all(['td', 'th'])]
            def field(i):
                return values[i] if i is not None and i < len(values) else ''
            raw_code = field(code_idx)
            if not extract_codes(raw_code):
                continue
            code = normalize_course(raw_code)
            parsed[code] = {
                'course_code': display_course(raw_code),
                'title': field(title_idx),
                'credits': catalog_credits(field(credit_idx)),
                'prerequisites': extract_codes(field(prereq_idx)),
                'cross_listed': extract_codes(field(cross_idx)),
            }
    catalog.clear()
    catalog.update(parsed)
    return len(parsed)


@router.post('/api/v1/admin/catalog/import', status_code=201)
async def catalog_import(file: UploadFile = File(...)):
    raw = await file.read()
    html = raw.decode('utf-8-sig', errors='replace')
    count = import_catalog_html(html)
    return {'status': 'success', 'courses_imported': count}


@router.get('/api/v1/catalog/courses/{course_code}')
def get_catalog_course(course_code: str):
    item = catalog.get(normalize_course(course_code))
    if item is None:
        raise HTTPException(status_code=404, detail='Course not found')
    return {
        **item,
        'prerequisites': [display_course(c) for c in item['prerequisites']],
        'cross_listed': [display_course(c) for c in item['cross_listed']],
    }


def term_key(term: str) -> tuple[int, int]:
    """A chronological key, e.g. 23F < 24W < 24SP < 24S < 24F."""
    match = re.fullmatch(r'\s*(\d{2})\s*(W|SP|S|F)\s*', str(term).upper())
    if not match:
        return (-1, -1)
    return (int(match.group(1)), SEASONS[match.group(2)])


def calculate_audit(student_id: str, history: list[dict], plan: list[dict], strict: bool = False) -> dict:
    completed: dict[str, list[tuple[int, int]]] = defaultdict(list)
    earned: dict[str, int] = {}
    for entry in history:
        if str(entry.get('status', '')).strip().lower() != 'completed':
            continue
        code = normalize_course(entry.get('course_code', ''))
        if not code:
            continue
        completed[code].append(term_key(entry.get('term', '')))
        # Credits counted once per normalized course, including retakes.
        earned[code] = max(earned.get(code, 0), max(0, int(entry.get('credits_earned', 0) or 0)))

    by_term = defaultdict(list)
    cross_list_violations = []
    total_planned = 0
    for entry in plan:
        code = normalize_course(entry.get('course_code', ''))
        term = str(entry.get('term', '')).strip().upper()
        item = catalog.get(code)
        total_planned += item['credits'] if item else 0
        if not item:
            continue
        for req in item['prerequisites']:
            if not any(past < term_key(term) for past in completed.get(req, [])):
                by_term[term].append({
                    'course_code': display_course(code),
                    'type': 'MISSING_PREREQUISITE',
                    'message': f'Missing prerequisite: {display_course(req)}',
                })
        for other in item['cross_listed']:
            if other in completed:
                cross_list_violations.append({
                    'course_code': display_course(code),
                    'type': 'CROSS_LIST_CONFLICT',
                    'message': f'Cross-listed with completed course {display_course(other)}',
                })

    timeline = [{'term': term, 'errors': by_term[term]} for term in sorted(by_term, key=lambda t: (term_key(t), t))]
    issues = bool(timeline or cross_list_violations)
    return {
        'student_id': student_id,
        'status': 'failed' if issues and strict else 'warning' if issues else 'ok',
        'timeline_validation': timeline,
        'cross_list_violations': cross_list_violations,
        'credit_summary': {
            'total_earned': sum(earned.values()),
            'total_planned': total_planned,
            'total_remaining_for_graduation': max(0, 120 - sum(earned.values()) - total_planned),
        },
    }


@router.get('/api/v1/students/{student_id}/audit-report')
def audit_report(student_id: str, strict: bool = False):
    # Imported lazily to avoid a circular import during startup.
    from main import ensure_student
    record = ensure_student(student_id)
    return calculate_audit(student_id, record['history'], record['plan'], strict)
