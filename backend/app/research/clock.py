import inspect
import time
from contextlib import contextmanager
from functools import wraps


class EventClock:
    def __init__(self, initial=None):
        self.fixed = initial

    def __call__(self):
        return time.time() if self.fixed is None else self.fixed

    @contextmanager
    def at(self, timestamp=None):
        previous = self.fixed
        self.fixed = self() if timestamp is None else timestamp
        try:
            yield self.fixed
        finally:
            self.fixed = previous


def paper_operation(kind):
    def decorate(method):
        signature = inspect.signature(method)

        @wraps(method)
        def call(self, *args, **kwargs):
            clock = self.clock
            if not isinstance(clock, EventClock):
                return method(self, *args, **kwargs)
            with clock.at():
                if not self.state.record_event or self.state.in_cycle:
                    return method(self, *args, **kwargs)
                arguments = dict(signature.bind(self, *args, **kwargs).arguments)
                arguments.pop("self")
                arguments = {
                    k: v.model_dump(mode="json") if hasattr(v, "model_dump") else v
                    for k, v in arguments.items()
                }
                error = None
                try:
                    return method(self, *args, **kwargs)
                except Exception as exc:
                    error = type(exc).__name__
                    raise
                finally:
                    if self.state.record_event and not self.state.in_cycle:
                        self.state.record_event(kind, {"arguments": arguments, "error": error})

        return call

    return decorate
