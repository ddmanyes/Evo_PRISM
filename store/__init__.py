"""ER store package — backend-agnostic metadata registry."""
from .factory import get_store, reset_store

__all__ = ["get_store", "reset_store"]
