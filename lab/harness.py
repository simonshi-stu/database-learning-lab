"""共享调用层：输入→校验→执行用户解→异常映射→HTTP 响应→前端显示。

异常映射与原仓库一致：BusinessError 按状态码；IntegrityError→409 CONSTRAINT_CONFLICT；
锁忙→503 DATABASE_BUSY；酒店注入异常→HTTP 200 + ROLLED_BACK（原演示即如此）。
"""
import os
import sqlite3

from .sandbox import BusinessError, DatabaseBusy, InjectedFailure


def call_user(rec, actor, request, fn, db, validate, display, crash_after_commit=False):
    rec.emit('input', actor, '收到请求', request=request)
    try:
        validate(request)
    except BusinessError as exc:
        body = {'error': {'code': exc.code, 'message': exc.message}}
        rec.emit('validation', actor, f'后端校验失败 · {exc.code}', passed=False)
        rec.emit('http_response', actor, f'HTTP {exc.status}', status=exc.status, body=body)
        rec.emit('frontend', actor, display(body, exc.status), status=exc.status, body=body)
        return {'actor': actor, 'status': exc.status, 'body': body}
    rec.emit('validation', actor, '后端校验通过（请求键/字段类型/取值范围）', passed=True)
    rec.emit('unobserved', actor, 'SQLite 内部锁获取与升级、操作系统调度细节：未观测（只观测到语句与结果）')
    try:
        body = fn(db, request)
        if body is None:
            raise RuntimeError('解法没有 return 结果')
        status = 200
    except BusinessError as exc:
        status = exc.status
        body = {'error': {'code': exc.code, 'message': exc.message}}
    except DatabaseBusy:
        status = 503
        body = {'error': {'code': 'DATABASE_BUSY', 'message': '数据库繁忙，请使用原请求键重试'}}
    except InjectedFailure as exc:
        status = 200
        body = {'status': 'ROLLED_BACK', 'reason': f'InjectedFailure: {exc}'}
    except sqlite3.IntegrityError as exc:
        status = 409
        body = {'error': {'code': 'CONSTRAINT_CONFLICT',
                          'message': '数据违反唯一性或完整性约束', 'detail': str(exc)}}
    except Exception as exc:
        status = 500
        body = {'error': {'code': 'INTERNAL_ERROR', 'message': f'{type(exc).__name__}: {exc}'}}
    if crash_after_commit and status == 200:
        rec.emit('note', actor, '事务已提交，但响应尚未发送给客户端')
        rec.emit('process_exit', actor,
                 '子进程在提交后退出 os._exit(73)（复用原仓库 audit_sqlite_crashes 思路）',
                 exit_code=73)
        rec.close()
        os._exit(73)
    rec.emit('http_response', actor, f'HTTP {status}', status=status, body=body)
    rec.emit('frontend', actor, display(body, status), status=status, body=body)
    return {'actor': actor, 'status': status, 'body': body}


def validate_sql(statement, allowed_table, required_params):
    import re
    text = statement.strip().rstrip(';').strip()
    if not text or ';' in text:
        raise BusinessError(422, 'SQL_NOT_ALLOWED', '只允许一条 SQL 语句')
    if not re.match(r'(?is)^UPDATE\s+' + allowed_table + r'\b', text):
        raise BusinessError(422, 'SQL_NOT_ALLOWED', f'只允许 UPDATE {allowed_table} 语句')
    if re.search(r'(?i)\b(DROP|DELETE|INSERT|ATTACH|PRAGMA|CREATE|ALTER|SELECT)\b|--|/\*', text):
        raise BusinessError(422, 'SQL_NOT_ALLOWED', '语句包含不允许的关键字')
    for name in required_params:
        if ':' + name not in text:
            raise BusinessError(422, 'SQL_PARAM_MISSING', f'语句需要使用命名参数 :{name}')
    return text