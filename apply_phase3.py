"""Run once from repository root to register the Phase 3 router without replacing Phase 2 code."""
from pathlib import Path
p = Path('main.py')
if not p.is_file():
    raise SystemExit('Run this from your course-registration-api repository root.')
s = p.read_text(encoding='utf-8-sig')
line = '\n\n# Phase 3: catalog ingestion and academic audit\nfrom audit_engine import router as audit_router\napp.include_router(audit_router)\n'
if 'app.include_router(audit_router)' not in s:
    p.write_text(s.rstrip() + line, encoding='utf-8')
    print('Registered Phase 3 router in main.py')
else:
    print('Phase 3 router already registered')
