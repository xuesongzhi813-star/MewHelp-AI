"""``/api/jobs`` 路由:白名单作业的启动、列表与日志回显。

客户端只提交作业名(以及 heavy 作业的 ``confirm`` 布尔值),命令由
``app.core.jobs`` 写死;异常统一映射为 404 / 403 / 409,启动成功 202。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.jobs import (
    ConfirmationRequired,
    JobAlreadyRunning,
    MakeNotFound,
    UnknownJob,
    job_status,
    list_jobs,
    start_job,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class StartJobRequest(BaseModel):
    confirm: bool = False


@router.get("")
def get_jobs() -> list[dict]:
    return list_jobs()


@router.post("/{name}", status_code=202)
def post_job(name: str, payload: StartJobRequest | None = None) -> dict:
    confirm = payload.confirm if payload is not None else False
    try:
        state = start_job(name, confirm=confirm)
    except UnknownJob as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfirmationRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except JobAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MakeNotFound as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "name": name,
        "status": state.status,
        "started_at": state.started_at,
        "finished_at": state.finished_at,
        "exit_code": state.exit_code,
    }


@router.get("/{name}")
def get_job(name: str) -> dict:
    try:
        return job_status(name)
    except UnknownJob as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
