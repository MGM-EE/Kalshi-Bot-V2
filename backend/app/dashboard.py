"""Share one bounded read projection across browser streams and HTTP backup."""

import json
import time
import uuid


class DashboardSnapshots:
    def __init__(self, state, interval=0.25, clock=time.monotonic):
        self.state = state
        self.interval = max(0.05, interval)
        self.clock = clock
        self.runtime_id = uuid.uuid4().hex
        self.sequence = 0
        self.produced_at = None
        self.value = None
        self.encoded = None

    def frame(self):
        now = self.clock()
        if self.value is None or now - self.produced_at >= self.interval:
            # Publish only after the complete read/encoding succeeds. A failed
            # provider stays visible; never relabel an old frame as fresh.
            value = self.state.snapshot()
            sequence = self.sequence + 1
            value["stream_version"] = {"runtime_id": self.runtime_id, "sequence": sequence}
            encoded = json.dumps(value, separators=(",", ":"), allow_nan=False)
            self.value, self.encoded = value, encoded
            self.produced_at, self.sequence = self.clock(), sequence
        return self.encoded
