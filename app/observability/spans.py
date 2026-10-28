"""Request-local trace context; copied by asyncio.to_thread, never global IDs."""
from contextlib import aclosing, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from functools import wraps
import logging
import uuid
import inspect

from app.observability.runtime import TraceScope


logger = logging.getLogger(__name__)


@dataclass
class Execution:
    request_id: str
    scope: TraceScope
    metadata: dict = field(default_factory=dict)


_execution: ContextVar[Execution | None] = ContextVar('mewhelp_trace', default=None)


def current_request_id():
    active = _execution.get()
    return active.request_id if active is not None else None


def tracing_config(existing=None):
    active = _execution.get()
    if active is None:
        return dict(existing or {})
    config = active.scope.config(existing)
    metadata = {**config.get('metadata', {}), **active.metadata}
    if active.metadata.get('session_id'):
        metadata['langfuse_session_id'] = active.metadata['session_id']
    config['metadata'] = metadata
    return config


def bind_turn(identity):
    active = _execution.get()
    if active is not None:
        active.metadata.update({k: v for k, v in asdict(identity).items() if v is not None})
        active.scope.update(metadata=dict(active.metadata))
        active.scope.attributes(session_id=identity.session_id, metadata=dict(active.metadata))


def update_trace(state):
    active = _execution.get()
    if active is not None:
        fields = ('session_id', 'conversation_id', 'request_id', 'intent', 'route', 'continuation_of')
        active.metadata.update({k: state[k] for k in fields if state.get(k) is not None})
        active.scope.update(metadata=dict(active.metadata))
        if state.get('final_text'):
            active.scope.update(output=state['final_text'])


@contextmanager
def execution_scope(name, observed=None, *, purpose='chat', metadata=None):
    request_id = uuid.uuid4().hex
    fields = {'request_id': request_id, 'purpose': purpose, **(metadata or {})}
    manager = (observed.execution(name, trace_id=request_id, metadata=fields)
               if observed is not None else nullcontext(TraceScope()))
    with manager as scope:
        token = _execution.set(Execution(request_id, scope, fields))
        try:
            if fields.get('session_id'):
                scope.attributes(session_id=fields['session_id'], metadata=fields)
            yield scope
        finally:
            _execution.reset(token)


def model_config_kwargs():
    from langgraph.config import get_config
    try: inherited=get_config()
    except RuntimeError: inherited=None
    config = tracing_config(inherited)
    return {'config': config} if config.get('callbacks') else {}


def traced_operation(name, *, purpose):
    def decorate(function):
        @wraps(function)
        async def run(owner, *args, **kwargs):
            observed = getattr(owner, 'observability', None)
            if observed is None:
                observed = getattr(getattr(owner, 'summarizer', None), 'observability', None)
            source = _execution.get()
            metadata = {}
            if source is not None:
                metadata = {k: source.metadata[k] for k in ('session_id','conversation_id')
                            if k in source.metadata}
                metadata['source_trace_id'] = source.request_id
            with execution_scope(name, observed, purpose=purpose, metadata=metadata):
                return await function(owner, *args, **kwargs)
        return run
    return decorate


def traced_stream(name):
    def decorate(function):
        @wraps(function)
        async def run(owner, *args, **kwargs):
            if kwargs.get('workflow_runtime') is not None:
                async with aclosing(function(owner, *args, **kwargs)) as stream:
                    async for event in stream:
                        yield event
                return
            runtime = getattr(owner, 'runtime', owner)
            observed = kwargs.get('observability') or getattr(runtime, 'observability', None)
            metadata = {'session_id': owner} if isinstance(owner, str) else {}
            with execution_scope(name, observed, purpose=getattr(runtime,'trace_purpose','chat'),metadata=metadata) as scope:
                async with aclosing(function(owner, *args, **kwargs)) as stream:
                    async for event in stream:
                        if isinstance(event, dict) and event.get('type') == 'error':
                            scope.update(level='ERROR', status_message='business execution failed')
                        yield event
        return run
    return decorate


@contextmanager
def trace_span(name, *, input=None, as_type='span'):
    active = _execution.get()
    client = active.scope.client if active is not None else None
    cm = None
    span = None
    if client is not None:
        try:
            candidate = client.start_as_current_observation(name=name, as_type=as_type, input=input)
            span = candidate.__enter__()
            cm = candidate
        except Exception as exc:
            logger.warning('telemetry custom span degraded exception=%s', type(exc).__name__)
    scope = TraceScope(span, client=client)
    try:
        yield scope
    except BaseException as exc:
        scope.finish(error_type=type(exc).__name__)
        raise
    finally:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception as exc:
                logger.warning('telemetry custom span close degraded exception=%s', type(exc).__name__)


def observed_call(name, *, input_fields=(), input_selector=None, output_selector=None):
    """Instrument custom functions; explicit projections never expose settings."""
    def decorate(function):
        signature = inspect.signature(function)

        def inputs(args, kwargs):
            try:
                if input_selector:
                    return input_selector(args, kwargs)
                values = signature.bind_partial(*args, **kwargs).arguments
                return {key: values[key] for key in input_fields if key in values}
            except Exception:
                return None

        def finish(scope, value):
            if output_selector:
                try:
                    output = output_selector(value)
                    scope.finish(output=output)
                    if isinstance(output, dict) and output.get('status') == 'error':
                        scope.finish(error_type=str(output.get('outcome', 'tool_error')))
                except Exception as exc:
                    logger.warning('telemetry projection degraded exception=%s', type(exc).__name__)

        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def arun(*args, **kwargs):
                with trace_span(name, input=inputs(args, kwargs)) as scope:
                    result = await function(*args, **kwargs)
                    finish(scope, result)
                    return result
            return arun

        @wraps(function)
        def run(*args, **kwargs):
            with trace_span(name, input=inputs(args, kwargs)) as scope:
                result = function(*args, **kwargs)
                finish(scope, result)
                return result
        return run
    return decorate
