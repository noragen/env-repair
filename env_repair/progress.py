import sys
import time


class Progress:
    def __init__(self, total, label, stream=None):
        self.total = total
        self.label = label
        self.stream = stream or sys.stderr
        self.start = time.time()
        self.last_len = 0

    def update(self, current):
        if self.total <= 0:
            return
        elapsed = time.time() - self.start
        rate = current / elapsed if elapsed > 0 else 0
        remaining = (self.total - current) / rate if rate > 0 else 0
        pct = int((current / self.total) * 100)
        msg = f"{self.label} {current}/{self.total} ({pct}%) ETA {remaining:0.1f}s"
        pad = " " * max(0, self.last_len - len(msg))
        self.stream.write("\r" + msg + pad)
        self.stream.flush()
        self.last_len = len(msg)

    def finish(self):
        if self.last_len:
            self.stream.write("\n")
            self.stream.flush()
