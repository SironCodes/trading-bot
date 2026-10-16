"""Structured logging: JSON lines to a file plus plain console output, with every Alpaca key and secret
value replaced by [REDACTED] before anything is written."""
import json
import logging
from pathlib import Path


class Redacting(logging.Formatter):
    def __init__(self, inner: logging.Formatter, secrets):
        super().__init__()
        self.inner, self.secrets = inner, [s for s in secrets if s]

    def format(self, record):
        text = self.inner.format(record)
        for s in self.secrets:
            text = text.replace(s, "[REDACTED]")
        return text


class Json(logging.Formatter):
    def format(self, r):
        return json.dumps({"ts": self.formatTime(r, "%Y-%m-%dT%H:%M:%S%z"), "level": r.levelname, "logger": r.name,
                           "msg": r.getMessage(), **getattr(r, "fields", {})}, default=str)


def setup(path, level: str, environ) -> None:
    secrets = [v for k, v in environ.items() if k.startswith("ALPACA_") and k.endswith(("_KEY", "_SECRET"))]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    file, console = logging.FileHandler(path), logging.StreamHandler()
    file.setFormatter(Redacting(Json(), secrets))
    console.setFormatter(Redacting(logging.Formatter("%(levelname)s %(name)s: %(message)s"), secrets))
    root = logging.getLogger()
    root.handlers[:] = [file, console]
    root.setLevel(level)
