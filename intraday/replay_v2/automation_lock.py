"""Shared CLI/worker gate lock; never used by read-only projections."""
from contextlib import contextmanager
import fcntl
import os


@contextmanager
def gate_lock(database):
    fd = os.open(str(database)+'.gate-automation.lock',os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,0o600)
    try:
        try:
            fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('gate job already running; retry later') from None
        yield
    finally:
        os.close(fd)
