import json
import os
import urllib.request

from lab.problems import sales


BASE = os.environ.get('COURSE_BASE', 'http://127.0.0.1:8022')
ORIG = os.environ.get('ORIG_BASE', 'http://127.0.0.1:8021')


def post(base, path, payload):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.status, json.loads(r.read().decode('utf-8'))


s1, d1 = post(BASE, '/api/course/run', {'lesson_id': 'pg-iso', 'code': 'SELECT qty FROM t WHERE id = 1;', 'scenario': 'rr'})
print('course pg-iso rr HTTP', s1, 'passed', d1['passed'])
s2, d2 = post(ORIG, '/api/run', {'problem': 'sales-last-unit', 'mode': 'python', 'scenario': 'race', 'code': sales.REFERENCE})
print('orig sales race HTTP', s2, 'judge passed', d2['judge']['passed'])
s3, d3 = post(BASE, '/api/course/run', {'lesson_id': 'cpp-page', 'code': 'insert(3, "abcdefghijklmnopqrstuvwx");', 'scenario': 'exact'})
print('course cpp-page exact HTTP', s3, 'passed', d3['passed'])
