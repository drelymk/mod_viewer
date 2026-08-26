"""ZZMI hash.json metadata parsing.

ZZMI currently shares the structural hash.json fields needed by the index.
Keeping this module separate leaves room for game-specific validation without
making the registry or API infer a type from weak metadata signals.
"""

from .gimi import MetadataError, parse_hash_file

__all__ = ["MetadataError", "parse_hash_file"]
