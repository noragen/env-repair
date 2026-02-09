import unittest
from unittest.mock import patch


class _Progress:
    def __init__(self):
        self.finish_called = 0

    def update(self, _current):
        return None

    def finish(self):
        self.finish_called += 1


class _Future:
    def __init__(self):
        self.cancel_called = 0

    def cancel(self):
        self.cancel_called += 1
        return True

    def result(self):
        return True, None


class _Executor:
    def __init__(self, max_workers=None):
        self.max_workers = max_workers
        self.futures = []
        self.shutdown_calls = []

    def submit(self, _fn, *_a, **_kw):
        fut = _Future()
        self.futures.append(fut)
        return fut

    def shutdown(self, wait=True, cancel_futures=False):
        self.shutdown_calls.append((wait, cancel_futures))


class TestVerifyImportsInterrupt(unittest.TestCase):
    def test_parallel_checks_shutdown_cleanly_on_keyboardinterrupt(self):
        import env_repair.verify_imports as vi

        progress = _Progress()
        executor = _Executor(max_workers=2)

        def fake_as_completed(_futures):
            raise KeyboardInterrupt

        with patch.object(vi.concurrent.futures, "ThreadPoolExecutor", return_value=executor):
            with patch.object(vi.concurrent.futures, "as_completed", side_effect=fake_as_completed):
                with self.assertRaises(KeyboardInterrupt):
                    vi._run_import_checks_parallel(
                        to_check=[("x.dist-info", "x", vi.Path("."))],
                        python_exe="python",
                        max_workers=2,
                        progress=progress,
                    )

        self.assertGreaterEqual(progress.finish_called, 1)
        self.assertTrue(any(cancel_futures for _wait, cancel_futures in executor.shutdown_calls))
        self.assertTrue(all(f.cancel_called >= 1 for f in executor.futures))

