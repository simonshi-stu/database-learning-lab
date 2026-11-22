"""本地后端学习实验室：FastAPI + SQLite，仅绑定 127.0.0.1。"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import runner
from .problems import sales, hotel

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title='后端运行实验室', docs_url=None, redoc_url=None)

MODULES = {'sales-last-unit': sales, 'hotel-two-nights': hotel}


class RunRequest(BaseModel):
    problem: str
    mode: str = Field(pattern='^(python|sql)$')
    code: str = Field(max_length=20000)
    scenario: str = ''


@app.get('/api/problems')
def problems():
    return {'problems': [m.META for m in MODULES.values()]}


@app.post('/api/run')
def run(body: RunRequest):
    if body.problem not in MODULES:
        raise HTTPException(404, 'unknown problem')
    try:
        result = runner.run_problem(body.problem, body.mode, body.code, body.scenario)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return JSONResponse(result)


@app.get('/api/runs/{run_id}')
def get_run(run_id: str):
    result = runner.load_run(run_id)
    if result is None:
        raise HTTPException(404, 'run not found')
    return JSONResponse(result)


@app.post('/api/reset')
def reset():
    return {'removed_runs': runner.reset_runs()}


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/favicon.ico')
def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)


app.mount('/static', StaticFiles(directory=str(ROOT / 'static')), name='static')