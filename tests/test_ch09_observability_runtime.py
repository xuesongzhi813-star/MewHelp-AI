"""Lifecycle failures must never replace the caller's result or exception."""
from contextlib import contextmanager
from types import SimpleNamespace
import subprocess
import sys
import threading
import os

import pytest


def settings(**updates):
    return SimpleNamespace(**{
        'langfuse_enabled': True, 'langfuse_public_key': 'pk-test',
        'langfuse_secret_key': 'sk-test',
        'langfuse_base_url': 'https://jp.cloud.langfuse.com',
        'langfuse_timeout': 0.1, 'langfuse_shutdown_timeout': 0.1,
        **updates,
    })


class Client:
    def __init__(self, **kwargs):
        self.closed = 0
        self.outputs = []

    def auth_check(self):
        return True

    def shutdown(self):
        self.closed += 1

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        yield self

    def update(self, **kwargs):
        self.outputs.append(kwargs)


def runtime(monkeypatch, client=None, **updates):
    from app.observability import runtime as module
    monkeypatch.setattr(module, '_make_handler', lambda **kwargs: object())
    return module.ObservabilityRuntime(settings(**updates), client_factory=lambda **kw: client or Client())


def test_disabled_creates_no_client():
    from app.observability.runtime import ObservabilityRuntime
    def forbidden(**kwargs):
        pytest.fail('disabled tracing must not initialize SDK')
    observed = ObservabilityRuntime(settings(langfuse_enabled=False), client_factory=forbidden)
    assert observed.start() == 'disabled'
    with observed.execution('chat', trace_id='a' * 32, metadata={}):
        answer = '正常回答'
    assert answer == '正常回答'


def test_auth_failure_is_degraded_not_startup_failure(monkeypatch):
    class Broken(Client):
        def auth_check(self):
            raise ConnectionError('unreachable')
    observed = runtime(monkeypatch, Broken())
    assert observed.start() == 'degraded'
    with observed.execution('chat', trace_id='a' * 32, metadata={}):
        answer = '正常回答'
    assert answer == '正常回答'


def test_scope_failure_does_not_replace_business_exception(monkeypatch):
    class Broken(Client):
        @contextmanager
        def start_as_current_observation(self, **kwargs):
            raise RuntimeError('export unavailable')
            yield
    observed = runtime(monkeypatch, Broken())
    assert observed.start() == 'ready'
    original = ValueError('business failure')
    with pytest.raises(ValueError) as error:
        with observed.execution('chat', trace_id='a' * 32, metadata={}):
            raise original
    assert error.value is original


def test_config_adds_handler_once(monkeypatch):
    observed = runtime(monkeypatch)
    observed.start()
    sentinel = object()
    config = {'callbacks': [sentinel], 'configurable': {'thread_id': 'session'}}
    with observed.execution('chat', trace_id='a' * 32, metadata={}) as scope:
        first = scope.config(config)
        second = scope.config(first)
    assert second['callbacks'] == first['callbacks']
    assert len(second['callbacks']) == 2
    assert second['configurable'] == {'thread_id': 'session'}
    assert config['callbacks'] == [sentinel]


def test_close_flushes_once(monkeypatch):
    client = Client()
    observed = runtime(monkeypatch, client)
    observed.start()
    observed.close()
    observed.close()
    assert client.closed == 1
    assert observed.status == 'closed'


def test_two_app_lifespans_do_not_reuse_shutdown_client(monkeypatch):
    from app.observability import runtime as module
    monkeypatch.setattr(module, '_make_handler', lambda **kw: object())
    clients = []
    def fresh(**kwargs):
        client = Client()
        clients.append(client)
        return client
    for _ in range(2):
        observed = module.ObservabilityRuntime(settings(), client_factory=fresh)
        assert observed.start() == 'ready'
        observed.close()
    assert len(clients) == 2 and clients[0] is not clients[1]
    assert [client.closed for client in clients] == [1, 1]


@pytest.mark.parametrize('url', ['', 'https://cloud.langfuse.com', 'https://jp.cloud.langfuse.com.attacker.test'])
def test_wrong_region_degrades_without_constructing_client(url):
    from app.observability.runtime import ObservabilityRuntime
    def forbidden(**kwargs):
        pytest.fail('incorrect region must not initialize SDK')
    observed = ObservabilityRuntime(settings(langfuse_base_url=url), client_factory=forbidden)
    assert observed.start() == 'degraded'


def test_runner_redacts_child_output_before_printing_and_writing(tmp_path):
    from scripts.test_ch09 import run_command
    private = 'synthetic-ch09-private-value'
    log = tmp_path / 'result.log'
    result = run_command([sys.executable, '-c', f'print({private!r}); raise SystemExit(3)'],
                         secrets=[private], log_path=log)
    assert result.returncode == 3
    assert private not in result.stdout
    assert private not in log.read_text(encoding='utf-8')
    assert '[REDACTED]' in result.stdout


def test_telemetry_update_failure_does_not_affect_answer(monkeypatch):
    class Broken(Client):
        def update(self, **kwargs):
            raise RuntimeError('update failed')
    observed = runtime(monkeypatch, Broken())
    observed.start()
    with observed.execution('chat', trace_id='a' * 32, metadata={}) as scope:
        scope.update(output='answer')
        answer = 'answer'
    assert answer == 'answer'


def test_broken_callback_manager_keeps_original_business_config(monkeypatch):
    class Manager:
        def copy(self):
            raise RuntimeError('callback manager unavailable')
    observed = runtime(monkeypatch)
    observed.start()
    manager = Manager()
    with observed.execution('chat', trace_id='a' * 32, metadata={}) as scope:
        config = scope.config({'callbacks': manager, 'run_name': 'business'})
    assert config['callbacks'] is manager
    assert config['run_name'] == 'business'


def test_auth_timeout_does_not_wait_for_remote_completion(monkeypatch):
    release = threading.Event()
    class Slow(Client):
        def auth_check(self):
            release.wait(2)
            return True
    observed = runtime(monkeypatch, Slow(), langfuse_timeout=0.01)
    try:
        assert observed.start() == 'degraded'
        assert not release.is_set()
    finally:
        release.set()


def test_exporter_exit_failure_preserves_business_error(monkeypatch):
    class Broken(Client):
        @contextmanager
        def start_as_current_observation(self, **kwargs):
            try:
                yield self
            finally:
                raise RuntimeError('exporter close failed')
    observed = runtime(monkeypatch, Broken())
    observed.start()
    original = ValueError('business failure')
    with pytest.raises(ValueError) as error:
        with observed.execution('chat', trace_id='a' * 32, metadata={}):
            raise original
    assert error.value is original


def test_runner_cli_handles_chinese_failure_under_gbk(tmp_path):
    case = tmp_path / 'encoding_case.py'
    case.write_text("def test_encoding():\n    assert False, '中文故障'\n", encoding='utf-8')
    result = subprocess.run([sys.executable, 'scripts/test_ch09.py', str(case), '-q', '--tb=short'],
        capture_output=True, env={**os.environ, 'PYTHONIOENCODING':'gbk'})
    assert result.returncode == 1
    assert b'UnicodeEncodeError' not in result.stderr
    assert b'1 failed' in result.stdout
