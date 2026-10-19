"""白名单作业运行器。

网页只传作业名,``argv`` 由本模块按注册表拼死;不接受任何来自客户端的
命令字符串,也不经过 shell。所有目标统一调用仓库根的 ``Makefile``,
与终端演示命令共用同一套配方。

状态只存内存(应用重启后回到 idle,日志文件保留),同一时刻全进程
只允许一个作业运行:``kb-build`` 与 ``kb-vectorize`` 并发会同时写
MySQL / Milvus,必须互斥。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
LOG_TAIL_LINES = 200


class UnknownJob(LookupError):
    """作业名不在白名单内。"""


class ConfirmationRequired(PermissionError):
    """heavy 作业缺少 ``confirm=True``。"""


class JobAlreadyRunning(RuntimeError):
    """已有作业在运行,拒绝并发启动。"""


class MakeNotFound(RuntimeError):
    """PATH 中找不到 make。"""


@dataclass(frozen=True)
class JobSpec:
    name: str
    target: str
    heavy: bool
    description: str


@dataclass
class JobState:
    status: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    process: object | None
    log_path: Path | None


JOBS: dict[str, JobSpec] = {
    "kb-preview": JobSpec(
        name="kb-preview",
        target="kb-preview",
        heavy=False,
        description="只读预览建库材料的切块结果,不写库",
    ),
    "kb-build": JobSpec(
        name="kb-build",
        target="kb-build",
        heavy=False,
        description="录入文档与 FAQ,并完成向量化",
    ),
    "seed-conv": JobSpec(
        name="seed-conv",
        target="seed-conv",
        heavy=False,
        description="灌入演示历史会话(幂等)",
    ),
    "kb-mine": JobSpec(
        name="kb-mine",
        target="kb-mine",
        heavy=True,
        description="调用 LLM 从历史会话挖掘问答知识",
    ),
    "kb-vectorize": JobSpec(
        name="kb-vectorize",
        target="kb-vectorize",
        heavy=False,
        description="向量化所有 pending 切片",
    ),
    "kb-rebuild": JobSpec(
        name="kb-rebuild",
        target="kb-rebuild",
        heavy=False,
        description="保留原文,重建 Milvus 向量索引",
    ),
    "kb-reset": JobSpec(
        name="kb-reset",
        target="kb-reset",
        heavy=True,
        description="清空知识库(破坏性操作,需确认)",
    ),
}

_states: dict[str, JobState] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_missing_message(make_exe: str) -> str:
    return (
        f"未找到 make(当前 make_exe={make_exe!r}),"
        "请安装 GnuWin32.Make 并把它加入 PATH。"
    )


def _log_path(name: str) -> Path:
    return Path(get_settings().job_log_dir) / f"{name}.log"


def _read_log_tail(log_path: Path | None) -> str:
    if log_path is None:
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-LOG_TAIL_LINES:])


def _refresh_locked(state: JobState) -> None:
    """poll 进程并落定 succeeded / failed;调用方必须持有 ``_lock``。"""
    if state.status != "running" or state.process is None:
        return
    exit_code = state.process.poll()
    if exit_code is None:
        return
    state.status = "succeeded" if exit_code == 0 else "failed"
    state.exit_code = exit_code
    state.finished_at = _now()


def _reap_locked() -> None:
    for state in _states.values():
        _refresh_locked(state)


def _running_locked() -> bool:
    return any(state.status == "running" for state in _states.values())


def start_job(name: str, *, confirm: bool = False) -> JobState:
    """启动白名单作业;返回刚创建的内存状态。

    校验顺序:作业名 → heavy 确认 → 全局单飞 → make 可执行文件。
    heavy 作业(kb-mine / kb-reset)必须显式 ``confirm=True``。
    """
    spec = JOBS.get(name)
    if spec is None:
        raise UnknownJob(f"未知作业:{name}")

    if spec.heavy and not confirm:
        raise ConfirmationRequired(f"作业 {name} 需要二次确认(confirm=true)")

    settings = get_settings()
    make_path: str | None = None

    with _lock:
        _reap_locked()
        if _running_locked():
            raise JobAlreadyRunning("已有作业在运行,请等待其结束")

        make_path = shutil.which(settings.make_exe)
        log_path = _log_path(name)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        if make_path is None:
            message = _make_missing_message(settings.make_exe)
            # 让页面 GET 作业详情时也能看到失败原因,而不是只有空日志。
            log_path.write_text(message + "\n", encoding="utf-8")
            raise MakeNotFound(message)

        argv = [make_path, "-f", str(ROOT / "Makefile"), spec.target]
        # 子进程强制 UTF-8 + 无缓冲:中文日志可读,运行中 log_tail 不空。
        env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
        with log_path.open("w", encoding="utf-8") as log_file:
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=ROOT,
                    shell=False,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )
            except OSError as exc:
                log_file.write(f"启动失败:{exc}\n")
                raise

        state = JobState(
            status="running",
            started_at=_now(),
            finished_at=None,
            exit_code=None,
            process=process,
            log_path=log_path,
        )
        _states[name] = state
        return state


def job_status(name: str) -> dict:
    """返回单个作业的状态与日志尾部(最后 200 行)。"""
    if name not in JOBS:
        raise UnknownJob(f"未知作业:{name}")

    with _lock:
        state = _states.get(name)
        if state is None:
            return {
                "name": name,
                "status": "idle",
                "started_at": None,
                "finished_at": None,
                "exit_code": None,
                "log_tail": _read_log_tail(_log_path(name)),
            }
        _refresh_locked(state)
        return {
            "name": name,
            "status": state.status,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "exit_code": state.exit_code,
            "log_tail": _read_log_tail(state.log_path),
        }


def list_jobs() -> list[dict]:
    """注册表 + 各自状态,供 ``GET /api/jobs`` 与后台总览使用。"""
    with _lock:
        _reap_locked()
        rows: list[dict] = []
        for spec in JOBS.values():
            state = _states.get(spec.name)
            rows.append(
                {
                    "name": spec.name,
                    "target": spec.target,
                    "heavy": spec.heavy,
                    "description": spec.description,
                    "status": state.status if state is not None else "idle",
                    "started_at": state.started_at if state is not None else None,
                    "finished_at": state.finished_at if state is not None else None,
                    "exit_code": state.exit_code if state is not None else None,
                    "log_path": (
                        str(state.log_path)
                        if state is not None and state.log_path is not None
                        else None
                    ),
                }
            )
        return rows


def reset_jobs_for_tests() -> None:
    """清空内存状态;测试隔离专用。"""
    with _lock:
        _states.clear()
