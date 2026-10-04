"""
Scanning service package: a reusable library layer (`service`) plus a
long-running HTTP layer (`api`) and a thin CLI shell (`cli`). Layers are
split by responsibility: parsing, policy, cache, routing.
"""

# Local
from .service import scan_repository

__all__ = ["scan_repository"]
