"""Own one SDK lifecycle; telemetry must never replace a business result."""
from contextlib import contextmanager, ExitStack
import logging
import sys
import threading


logger = logging.getLogger(__name__)
JAPAN_URL = 'https://jp.cloud.langfuse.com'


def _make_client(**kwargs):
    from langfuse import Langfuse
    client = Langfuse(**kwargs)
    # The pinned SDK keeps resource managers after shutdown. Never silently
    # attach a new application lifespan to those terminated exporters.
    if getattr(getattr(client, '_resources', None), '_shutdown', False):
        raise RuntimeError('telemetry process restart required')
    return client


def _make_handler(**kwargs):
    from langfuse.langchain import CallbackHandler
    return CallbackHandler(**kwargs)


def _bounded(call, timeout):
    result = []
    finished = threading.Event()

    def run():
        try:
            result.append((True, call()))
        except Exception as exc:
            result.append((False, type(exc).__name__))
        finally:
            finished.set()

    threading.Thread(target=run, name='telemetry-lifecycle', daemon=True).start()
    if not finished.wait(timeout):
        raise TimeoutError('telemetry lifecycle deadline')
    ok, value = result[0]
    if not ok:
        raise RuntimeError(value)
    return value


class TraceScope:
    def __init__(self, span=None, handler=None, client=None):
        self.span = span
        self.handler = handler
        self.client = client
        self._attributes = ExitStack()

    def attributes(self, **fields):
        if self.span is not None:
            try:
                from langfuse import propagate_attributes
                # Keep propagation alive through all nested graph tasks/threads.
                self._attributes.enter_context(propagate_attributes(**fields))
            except Exception as exc:
                logger.warning('telemetry attributes degraded exception=%s', type(exc).__name__)

    def close_attributes(self):
        try:
            self._attributes.close()
        except Exception as exc:
            logger.warning('telemetry attributes close degraded exception=%s', type(exc).__name__)

    def finish(self, output=None, *, error_type=None):
        fields = {'output': output} if output is not None else {}
        if error_type:
            fields.update(level='ERROR', status_message=error_type)
        self.update(**fields)

    def config(self, existing=None):
        config = dict(existing or {})
        if self.handler is None:
            return config
        existing_handlers = config.get('callbacks')
        if existing_handlers is not None and not isinstance(existing_handlers, (list, tuple)):
            # LangChain callback managers carry inherited handlers as well.
            try:
                manager = existing_handlers.copy()
                if self.handler not in manager.handlers:
                    manager.add_handler(self.handler)
                config['callbacks'] = manager
            except Exception as exc:
                logger.warning('telemetry callback configuration degraded exception=%s', type(exc).__name__)
        else:
            handlers = list(existing_handlers or [])
            if self.handler not in handlers:
                handlers.append(self.handler)
            config['callbacks'] = handlers
        return config

    def update(self, **fields):
        if self.span is not None:
            try:
                self.span.update(**fields)
            except Exception as exc:
                logger.warning('telemetry update degraded exception=%s', type(exc).__name__)


class ObservabilityRuntime:
    def __init__(self, settings, *, client_factory=None):
        self.settings = settings
        self._factory = client_factory or _make_client
        self._client = None
        self._lock = threading.RLock()
        self.status = 'disabled'
        self.shutdown_confirmed = False

    @property
    def read_api(self):
        try:return self._client.api if self.status=='ready' and self._client is not None else None
        except Exception as exc:
            logger.warning('telemetry read API unavailable exception=%s',type(exc).__name__)
            return None

    def start(self):
        with self._lock:
            if self.status in {'ready', 'closed'}:
                return self.status
            if not self.settings.langfuse_enabled:
                return self.status
            url = self.settings.langfuse_base_url.rstrip('/')
            if (url != JAPAN_URL or not self.settings.langfuse_public_key
                    or not self.settings.langfuse_secret_key):
                self.status = 'degraded'
                logger.warning('telemetry configuration incomplete or wrong region')
                return self.status
            try:
                from app.observability.projection import project_workflow_payload
                self._client = self._factory(
                    public_key=self.settings.langfuse_public_key,
                    secret_key=self.settings.langfuse_secret_key,
                    base_url=url, timeout=self.settings.langfuse_timeout,
                    mask=project_workflow_payload,
                )
                if not _bounded(self._client.auth_check, self.settings.langfuse_timeout):
                    raise RuntimeError('authentication unavailable')
                self.status = 'ready'
            except Exception as exc:
                self.status = 'degraded'
                logger.warning('telemetry startup degraded exception=%s', type(exc).__name__)
            return self.status

    def close(self):
        with self._lock:
            if self.status == 'closed':
                return
            self.status = 'closed'
            if self._client is not None:
                try:
                    # SDK shutdown itself flushes; do not separately flush twice.
                    _bounded(self._client.shutdown, self.settings.langfuse_shutdown_timeout)
                    self.shutdown_confirmed = True
                except Exception as exc:
                    logger.warning('telemetry shutdown unconfirmed exception=%s', type(exc).__name__)

    @contextmanager
    def execution(self, name, *, trace_id, metadata):
        cm = None
        span = None
        handler = None
        if self.status == 'ready':
            try:
                candidate = self._client.start_as_current_observation(
                    name=name, as_type='span', trace_context={'trace_id': trace_id},
                    metadata=metadata,
                )
                span = candidate.__enter__()
                cm = candidate
            except Exception as exc:
                logger.warning('telemetry scope degraded exception=%s', type(exc).__name__)
            if span is not None:
                try:
                    handler = _make_handler(public_key=self.settings.langfuse_public_key)
                except Exception as exc:
                    logger.warning('telemetry callback degraded exception=%s', type(exc).__name__)
        scope = TraceScope(span, handler, self._client if span is not None else None)
        try:
            yield scope
        except BaseException:
            scope.close_attributes()
            if cm is not None:
                try:
                    cm.__exit__(*sys.exc_info())
                except Exception as exc:
                    logger.warning('telemetry scope close degraded exception=%s', type(exc).__name__)
            raise
        else:
            scope.close_attributes()
            if cm is not None:
                try:
                    cm.__exit__(None, None, None)
                except Exception as exc:
                    logger.warning('telemetry scope close degraded exception=%s', type(exc).__name__)
