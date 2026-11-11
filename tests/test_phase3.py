import io
from fastapi.testclient import TestClient
import main
from audit_engine import catalog, term_key, normalize_course

client = TestClient(main.app)

CATALOG = '''<html><table><tr><th>Course Code</th><th>Title</th><th>Credits</th><th>Prerequisites</th><th>Cross-listed</th></tr>
<tr><td>COSC 2006</td><td>Intro</td><td>3</td><td>None</td><td></td></tr>
<tr><td>COSC 2007</td><td>Second</td><td>3</td><td>COSC 2006</td><td></td></tr>
<tr><td>COSC 3506</td><td>Senior</td><td>3</td><td>COSC 2007</td><td>ITEC 3506</td></tr>
<tr><td>ITEC 3506</td><td>Senior (cross-list)</td><td>3</td><td>COSC 2007</td><td>COSC 3506</td></tr>
</table></html>'''


def setup_function():
    main.students.clear()
    catalog.clear()
    r = client.post('/api/v1/admin/catalog/import', files={'file': ('c.html', CATALOG, 'text/html')})
    assert r.status_code == 201


def create_student(student='1', history=None, planned=None):
    r = client.post(f'/api/v1/students/{student}/history/import', files={'file': ('t.html', '<html/>', 'text/html')})
    assert r.status_code == 201
    r = client.put(f'/api/v1/students/{student}/history', json={'history': history or []})
    assert r.status_code == 200
    r = client.post(f'/api/v1/students/{student}/plan', json={'planned_courses': planned or []})
    assert r.status_code == 200


def hist(code, term, credits=3, status='Completed'):
    return {'course_code': code, 'term': term, 'credits_earned': credits, 'status': status}


def plan(code, term):
    return {'course_code': code, 'term': term}


def test_catalog_import_and_normalization():
    assert len(catalog) == 4
    assert normalize_course('cosc - 3506') == 'COSC3506'
    r = client.get('/api/v1/catalog/courses/COSC3506')
    assert r.status_code == 200
    assert r.json()['credits'] == 3
    assert r.json()['prerequisites'] == ['COSC-2007']
    assert client.get('/api/v1/catalog/courses/MISSING').status_code == 404


def test_no_issues_and_chronology():
    assert term_key('23F') < term_key('24W') < term_key('24SP') < term_key('24S') < term_key('24F')
    create_student(history=[hist('COSC 2007', '23F')], planned=[plan('COSC 3506', '24W')])
    r = client.get('/api/v1/students/1/audit-report')
    assert r.status_code == 200
    data = r.json()
    assert data['status'] == 'ok'
    assert data['timeline_validation'] == []
    assert data['credit_summary'] == {'total_earned': 3, 'total_planned': 3, 'total_remaining_for_graduation': 114}


def test_missing_prerequisite_strict_and_non_strict():
    create_student(history=[hist('COSC-2007', '24W')], planned=[plan('COSC3506', '24W')])
    data = client.get('/api/v1/students/1/audit-report').json()
    assert data['status'] == 'warning'
    assert data['timeline_validation'][0]['term'] == '24W'
    assert data['timeline_validation'][0]['errors'][0]['type'] == 'MISSING_PREREQUISITE'
    assert client.get('/api/v1/students/1/audit-report?strict=true').json()['status'] == 'failed'


def test_cross_listing_and_credit_retakes():
    create_student(history=[hist('ITEC3506','23F'), hist('ITEC-3506','24W'), hist('COSC 2007','23F'), hist('COSC2006','23W',0,'Attempted')], planned=[plan('COSC-3506','25W')])
    data = client.get('/api/v1/students/1/audit-report').json()
    assert data['cross_list_violations'][0]['type'] == 'CROSS_LIST_CONFLICT'
    assert data['credit_summary']['total_earned'] == 6
    assert data['credit_summary']['total_planned'] == 3
    assert data['credit_summary']['total_remaining_for_graduation'] == 111


def test_multiple_terms_and_isolation():
    create_student(history=[], planned=[plan('COSC3506','24F'), plan('COSC2007','24W')])
    create_student(student='2', history=[hist('COSC2007','23F')], planned=[plan('COSC3506','24W')])
    d1 = client.get('/api/v1/students/1/audit-report').json()
    d2 = client.get('/api/v1/students/2/audit-report').json()
    assert [x['term'] for x in d1['timeline_validation']] == ['24W','24F']
    assert d2['timeline_validation'] == []
    assert client.get('/api/v1/students/does-not-exist/audit-report').status_code == 404


def test_catalog_reimport_replaces_and_missing_credits():
    r = client.post('/api/v1/admin/catalog/import', files={'file': ('c.html', '<table><tr><th>Course Code</th><th>Credits</th></tr><tr><td>TEST 1234</td><td>4</td></tr></table>', 'text/html')})
    assert r.status_code == 201
    assert len(catalog) == 1
    create_student(planned=[plan('TEST1234', '25W'), plan('UNKNOWN9999','25F')])
    data = client.get('/api/v1/students/1/audit-report').json()
    assert data['status'] == 'ok'
    assert data['credit_summary']['total_planned'] == 4
