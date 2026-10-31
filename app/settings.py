from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


def _default_clock() -> Callable[[], float]:
    return time.monotonic


@dataclass
class Settings:
    database_url: str = "sqlite:///./rawaj.db"
    api_tokens: Any = field(default_factory=dict)
    max_transfer_piastres: int = 1_000_000
    rate_limit_capacity: int = 10
    rate_limit_refill_per_second: float = 1.0
    clock: Any = field(default_factory=_default_clock)
