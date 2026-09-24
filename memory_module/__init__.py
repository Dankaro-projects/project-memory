"""A local, standard-library memory module. See README.md for limits."""

from .core import Memory, MemoryError, Conflict, InvalidRecord, BudgetTooSmall, dumps

from .hooks import Hooks, CaptureFailure
from .schema import migrate

__version__ = "0.6.0b11"
__all__ = ["Memory", "MemoryError", "Conflict", "InvalidRecord", "BudgetTooSmall", "dumps", "Hooks", "CaptureFailure", "migrate"]
