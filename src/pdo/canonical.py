"""Canonical JSON and content digests.

Digests use sha512t24u (the GA4GH/VRS function DAPPER-ID-1 also uses: the first 24 bytes of SHA-512,
base64url) over a canonical JSON form. Canonicalization makes digests independent of how a value was
written: keys are sorted, numbers are compared as floats (so 300 and 300.0 hash alike, and -0.0 as
0.0), dates are ISO strings, and NaN or infinity is rejected rather than hashed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from pdo.identity_slots import HASHABLE


def sha512t24u(blob: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha512(blob).digest()[:24]).decode("ascii")


def normalize(value: Any) -> Any:
    """A JSON-ready value with numbers as floats, dates as strings and keys as strings."""
    if isinstance(value, BaseModel):
        return normalize(value.model_dump(exclude_none=True))
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"cannot hash non-finite number {value!r}")
        return 0.0 if number == 0 else number
    if isinstance(value, Enum):
        return normalize(value.value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [normalize(v) for v in value]
    if isinstance(value, set | frozenset):
        return sorted(normalize(v) for v in value)
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def canonical(value: Any) -> bytes:
    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(*parts: Any) -> str:
    return sha512t24u(canonical(list(parts)))


def hashable_dump(obj: Any) -> Any:
    """An object's identity: only its hashable slots (recursively), with unset slots left out."""
    if isinstance(obj, BaseModel):
        slots = HASHABLE.get(type(obj).__name__)
        if slots is None:
            raise KeyError(f"no identity table for {type(obj).__name__}; run make gen-project")
        out = {}
        for name in slots:
            value = getattr(obj, name, None)
            if value is None or value == []:
                continue
            out[name] = hashable_dump(value)
        return out
    if isinstance(obj, list | tuple):
        return [hashable_dump(v) for v in obj]
    return normalize(obj)


def object_digest(obj: BaseModel) -> str:
    """Content digest of one object: its class, logical id and hashable slots."""
    return digest(type(obj).__name__, getattr(obj, "id", None), hashable_dump(obj))
