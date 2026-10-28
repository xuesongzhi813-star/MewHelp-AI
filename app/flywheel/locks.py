"""Connection-scoped MySQL advisory locks; commit does not release them."""
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
import logging
import threading

from sqlalchemy import text
from app.db import get_engine


logger = logging.getLogger(__name__)


class LockUnavailable(RuntimeError):
    pass


@dataclass
class LockLease:
    connection: object
    name: str
    _mutex: object = field(default_factory=threading.RLock,repr=False,compare=False)

    def assert_owned(self):
        with self._mutex:
            try:
                owned = self.connection.scalar(text('SELECT IS_USED_LOCK(:name) = CONNECTION_ID()'), {'name':self.name})
            except Exception:
                raise LockUnavailable('database lock connection lost') from None
            if owned != 1:
                raise LockUnavailable('database lock ownership lost')


@contextmanager
def mysql_lock(name: str, *, timeout: int = 5):
    if not name or len(name) > 64 or timeout < 0:
        raise ValueError('invalid lock name or timeout')
    connection = get_engine().connect()
    acquired = False
    lease = None
    try:
        result = connection.scalar(text('SELECT GET_LOCK(:name, :timeout)'), {'name':name,'timeout':timeout})
        if result != 1:
            raise LockUnavailable('database lock unavailable')
        acquired = True
        lease = LockLease(connection, name)
        yield lease
    finally:
        with lease._mutex if lease is not None else nullcontext():
            if acquired:
                try:
                    released = connection.scalar(text('SELECT RELEASE_LOCK(:name)'), {'name':name})
                    if released != 1:
                        connection.invalidate()
                except Exception as exc:
                    logger.warning('lock release failed exception=%s', type(exc).__name__)
                    connection.invalidate()
            connection.close()
