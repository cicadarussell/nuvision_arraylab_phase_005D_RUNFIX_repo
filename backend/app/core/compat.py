from __future__ import annotations

try:
    from app.core.compat import StrEnum as _StrEnum
except ImportError:  # Python 3.9 / 3.10 compatibility
    from enum import Enum

    class _StrEnum(str, Enum):
        """Small Python 3.9-compatible replacement for enum.StrEnum."""

        def __str__(self) -> str:
            return str(self.value)

StrEnum = _StrEnum
