"""白名单作业运行器与 /api/jobs 路由测试(Task 12)。

全部用例用 Fake Popen 与假 ``shutil.which``,绝不真正启动 make:
- 注册表、heavy 确认、未知作业、全局单飞;
- argv 固定、``shell=False``、日志截断与 log_tail;
- make 不在 PATH 时的中文错误;
- API 的 202 / 404 / 403 / 409 映射。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.core.jobs as jobs
from app.api.jobs import router as jobs_router


# ---------------------------------------------------------------------------
# Fixture 与 fake
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_jobs(monkeypatch, tmp_path):
    """每个用例独立的内存状态与日志目录。"""
    jobs.reset_jobs_for_tests()
    stub = SimpleNamespace(
        make_exe="make",
        job_log_dir=str(tmp_path / "log" / "acceptance"),
    )
    monkeypatch.setattr(jobs, "get_settings", lambda: stub)
    yield
    jobs.reset_jobs_for_tests()


class RunningPopen:
    """永不结束的假进程,用于单飞断言。"""

    instances: list["RunningPopen"] = []

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.kwargs = kwargs
        self.returncode = None
        RunningPopen.instances.append(self)

    def poll(self):
        return self.returncode


class FinishedPopen:
    """构造后立即处于已完成状态的假进程。"""

    returncode = 0

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.kwargs = kwargs

    def poll(self):
        return self.returncode


def _writing_popen(lines):
    """构造会写日志的假 Popen,用于 log_tail 与截断断言。"""

    class _Popen:
        def __init__(self, argv, **kwargs):
            kwargs["stdout"].write("\n".join(lines) + "\n")
            kwargs["stdout"].flush()
            self.argv = argv
            self.kwargs = kwargs

        def poll(self):
            return 0

    return _Popen


@pytest.fixture
def api_client():
    application = FastAPI()
    application.include_router(jobs_router)
    return TestClient(application)


# ---------------------------------------------------------------------------
# 注册表与参数校验
# ---------------------------------------------------------------------------


def test_registry_has_seven_jobs():
    assert set(jobs.JOBS) == {
        "kb-preview",
        "kb-build",
        "seed-conv",
        "kb-mine",
        "kb-vectorize",
        "kb-rebuild",
        "kb-reset",
    }


def test_registry_specs_are_frozen_and_complete():
    for name, spec in jobs.JOBS.items():
        assert spec.name == name
        assert spec.target == name
        assert spec.description
        assert isinstance(spec.heavy, bool)


def test_heavy_jobs_are_only_mine_and_reset():
    heavy = {name for name, spec in jobs.JOBS.items() if spec.heavy}
    assert heavy == {"kb-mine", "kb-reset"}


def test_unknown_job_raises():
    with pytest.raises(jobs.UnknownJob):
        jobs.start_job("rm-rf")


def test_heavy_requires_confirm():
    with pytest.raises(jobs.ConfirmationRequired):
        jobs.start_job("kb-reset", confirm=False)
    with pytest.raises(jobs.ConfirmationRequired):
        jobs.start_job("kb-mine", confirm=False)


def test_light_job_needs_no_confirm(monkeypatch):
    monkeypatch.setattr(jobs.subprocess, "Popen", FinishedPopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    state = jobs.start_job("kb-preview")

    assert state.status == "running"


# ---------------------------------------------------------------------------
# 单飞
# ---------------------------------------------------------------------------


def test_single_flight_same_job(monkeypatch):
    RunningPopen.instances.clear()
    monkeypatch.setattr(jobs.subprocess, "Popen", RunningPopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    jobs.start_job("kb-preview")
    with pytest.raises(jobs.JobAlreadyRunning):
        jobs.start_job("kb-preview")


def test_single_flight_is_global(monkeypatch):
    RunningPopen.instances.clear()
    monkeypatch.setattr(jobs.subprocess, "Popen", RunningPopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    jobs.start_job("kb-preview")
    with pytest.raises(jobs.JobAlreadyRunning):
        jobs.start_job("kb-build")


def test_finished_job_allows_next_run(monkeypatch):
    monkeypatch.setattr(jobs.subprocess, "Popen", FinishedPopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    jobs.start_job("kb-preview")
    jobs.start_job("kb-build")

    assert jobs.job_status("kb-preview")["status"] == "succeeded"


# ---------------------------------------------------------------------------
# argv、shell 与 make
# ---------------------------------------------------------------------------


def test_argv_is_fixed_and_shell_false(monkeypatch):
    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

        def poll(self):
            return 0

    monkeypatch.setattr(jobs.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    jobs.start_job("kb-preview")

    assert captured["argv"] == [
        "C:/make.exe",
        "-f",
        str(jobs.ROOT / "Makefile"),
        "kb-preview",
    ]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == jobs.ROOT
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"
    assert captured["kwargs"]["stderr"] is jobs.subprocess.STDOUT


def test_make_missing_raises_chinese_error(monkeypatch):
    monkeypatch.setattr(jobs.shutil, "which", lambda name: None)

    with pytest.raises(jobs.MakeNotFound) as excinfo:
        jobs.start_job("kb-preview")

    message = str(excinfo.value)
    assert "GnuWin32.Make" in message
    assert "PATH" in message


def test_make_missing_writes_reason_to_log(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs.shutil, "which", lambda name: None)
    log_path = tmp_path / "log" / "acceptance" / "kb-preview.log"

    with pytest.raises(jobs.MakeNotFound):
        jobs.start_job("kb-preview")

    assert "GnuWin32.Make" in log_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 状态与日志
# ---------------------------------------------------------------------------


def test_job_status_reports_success_and_exit_code(monkeypatch):
    monkeypatch.setattr(jobs.subprocess, "Popen", FinishedPopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    jobs.start_job("kb-preview")
    status = jobs.job_status("kb-preview")

    assert status["status"] == "succeeded"
    assert status["exit_code"] == 0
    assert status["started_at"] is not None
    assert status["finished_at"] is not None
    assert "log_tail" in status


def test_job_status_reports_failure(monkeypatch):
    class FailingPopen(FinishedPopen):
        returncode = 2

    monkeypatch.setattr(jobs.subprocess, "Popen", FailingPopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    jobs.start_job("kb-preview")
    status = jobs.job_status("kb-preview")

    assert status["status"] == "failed"
    assert status["exit_code"] == 2


def test_job_status_unknown_raises():
    with pytest.raises(jobs.UnknownJob):
        jobs.job_status("nope")


def test_log_tail_returns_last_200_lines(monkeypatch):
    lines = [f"line-{index}" for index in range(250)]
    monkeypatch.setattr(jobs.subprocess, "Popen", _writing_popen(lines))
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    jobs.start_job("kb-preview")
    status = jobs.job_status("kb-preview")
    tail_lines = status["log_tail"].splitlines()

    assert len(tail_lines) == 200
    assert tail_lines[0] == "line-50"
    assert tail_lines[-1] == "line-249"


def test_log_is_truncated_per_run(monkeypatch):
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    monkeypatch.setattr(jobs.subprocess, "Popen", _writing_popen(["first-run"]))
    jobs.start_job("kb-preview")
    jobs.job_status("kb-preview")

    monkeypatch.setattr(jobs.subprocess, "Popen", _writing_popen(["second-run"]))
    jobs.start_job("kb-preview")
    status = jobs.job_status("kb-preview")

    assert status["log_tail"] == "second-run"


def test_list_jobs_includes_registry_and_idle_state():
    rows = jobs.list_jobs()

    assert [row["name"] for row in rows] == list(jobs.JOBS)
    assert all(row["status"] == "idle" for row in rows)
    assert {row["heavy"] for row in rows} == {False, True}


def test_reset_jobs_for_tests_clears_state(monkeypatch):
    monkeypatch.setattr(jobs.subprocess, "Popen", FinishedPopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    jobs.start_job("kb-preview")
    jobs.reset_jobs_for_tests()

    assert jobs.job_status("kb-preview")["status"] == "idle"


# ---------------------------------------------------------------------------
# /api/jobs
# ---------------------------------------------------------------------------


def test_api_lists_jobs(api_client):
    response = api_client.get("/api/jobs")

    assert response.status_code == 200
    assert {row["name"] for row in response.json()} == set(jobs.JOBS)


def test_api_start_returns_202(api_client, monkeypatch):
    RunningPopen.instances.clear()
    monkeypatch.setattr(jobs.subprocess, "Popen", RunningPopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    response = api_client.post("/api/jobs/kb-preview")

    assert response.status_code == 202
    assert response.json()["status"] == "running"


def test_api_unknown_job_returns_404(api_client):
    assert api_client.post("/api/jobs/rm-rf").status_code == 404
    assert api_client.get("/api/jobs/rm-rf").status_code == 404


def test_api_heavy_without_confirm_returns_403(api_client):
    response = api_client.post("/api/jobs/kb-reset", json={})

    assert response.status_code == 403


def test_api_heavy_with_confirm_starts(api_client, monkeypatch):
    RunningPopen.instances.clear()
    monkeypatch.setattr(jobs.subprocess, "Popen", RunningPopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    response = api_client.post("/api/jobs/kb-reset", json={"confirm": True})

    assert response.status_code == 202


def test_api_conflict_returns_409(api_client, monkeypatch):
    RunningPopen.instances.clear()
    monkeypatch.setattr(jobs.subprocess, "Popen", RunningPopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    assert api_client.post("/api/jobs/kb-preview").status_code == 202
    assert api_client.post("/api/jobs/kb-build").status_code == 409


def test_api_status_returns_log_tail(api_client, monkeypatch):
    monkeypatch.setattr(jobs.subprocess, "Popen", _writing_popen(["hello"]))
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")

    api_client.post("/api/jobs/kb-preview")
    response = api_client.get("/api/jobs/kb-preview")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["log_tail"] == "hello"
