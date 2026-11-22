"""课程用 PostgreSQL 实验库：连接走环境配置，专用测试库，每次运行独立 schema。

默认 DSN 指向本项目初始化的一次性集群（127.0.0.1:55433/course_lab，trust 仅本机）。
可用 COURSE_PG_DSN 覆盖。不硬编码凭据、不删建固定表。
"""
import os
import uuid

try:
    import psycopg
except ModuleNotFoundError as exc:
    if exc.name != 'psycopg':
        raise
    psycopg = None

DEFAULT_DSN = 'host=127.0.0.1 port=55433 dbname=course_lab user=course connect_timeout=3'


def dsn():
    return os.environ.get('COURSE_PG_DSN', DEFAULT_DSN)


def available():
    if os.environ.get('COURSE_SKIP_PG'):
        return False, '未配置 PostgreSQL（COURSE_SKIP_PG=1）'
    if psycopg is None:
        return False, '未安装 PostgreSQL Python 驱动（psycopg）'
    try:
        c = psycopg.connect(dsn(), autocommit=True)
        c.close()
        return True, 'PostgreSQL 可达'
    except Exception as exc:
        return False, f'未配置 PostgreSQL（{type(exc).__name__}: {str(exc).splitlines()[0]}）'


def connect(schema=None, autocommit=False, **kw):
    if psycopg is None:
        raise RuntimeError('未安装 PostgreSQL Python 驱动（psycopg）')
    c = psycopg.connect(dsn(), autocommit=autocommit, **kw)
    if schema:
        c.execute(f'SET search_path TO {schema}')
    return c


def new_schema():
    name = 'run_' + uuid.uuid4().hex[:10]
    c = connect(autocommit=True)
    c.execute(f'CREATE SCHEMA {name}')
    c.close()
    return name


def drop_schema(name):
    if not name.startswith('run_'):
        raise ValueError('only run_* schemas may be dropped')
    c = connect(autocommit=True)
    c.execute(f'DROP SCHEMA IF EXISTS {name} CASCADE')
    c.close()