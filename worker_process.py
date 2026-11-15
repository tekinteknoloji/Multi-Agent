"""Shared process and lock handling for durable local work."""
import os
import subprocess
import sys
import time
from contextlib import contextmanager


@contextmanager
def round_lock(path):
    with path.open("a+b") as lock:
        deadline = time.monotonic() + 930
        while True:
            try:
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Önceki işlem zamanında tamamlanmadı.")
                time.sleep(0.25)
        yield


def wait_for_worker(script, args, log_path, completed, refresh=None):
    result = completed()
    if result is not None:
        if refresh:
            refresh()
        return result
    options = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
               if os.name == "nt" else {"start_new_session": True})
    with log_path.open("ab") as log:
        with subprocess.Popen([sys.executable, str(script), *args],
                              stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options) as process:
            while True:
                finished = process.poll() is not None
                result = completed()
                if refresh:
                    refresh()
                if result is not None:
                    return result
                if finished:
                    raise RuntimeError(f"İşlem tamamlanamadı; {log_path.name} dosyasını incele.")
                time.sleep(0.25)
