"""Resolve evaluator implementations and their code identity.

"evaluators.py:range_model" loads a file relative to the decision's directory; "pkg.module:function"
imports a module. The code digest is the sha512t24u of the defining module's source (line endings
normalized), so editing the module, including a helper it calls, is a changed input. Evaluators must
not read files, globals or the network: anything they depend on has to be a declared input.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from collections.abc import Callable
from pathlib import Path

from pdo.canonical import sha512t24u

_cache: dict[tuple[str, str, float], tuple[Callable, str]] = {}


class EvaluatorError(ValueError):
    """An implementation reference cannot be resolved."""


def _digest_file(path: Path) -> str:
    return sha512t24u(path.read_bytes().replace(b"\r\n", b"\n"))


def resolve(implementation: str, base_dir: Path) -> tuple[Callable, str]:
    """The callable named by `implementation` and the digest of its module's source."""
    if ":" not in implementation:
        raise EvaluatorError(f"{implementation!r}: expected 'module.py:function' or 'package.module:function'")
    module_ref, func_name = implementation.rsplit(":", 1)
    if module_ref.endswith(".py"):
        path = (base_dir / module_ref).resolve()
        if not path.exists():
            raise EvaluatorError(f"{implementation!r}: {path} does not exist")
        key = (str(path), func_name, path.stat().st_mtime)
        if key not in _cache:
            spec = importlib.util.spec_from_file_location(f"pdo_evaluator_{sha512t24u(str(path).encode())[:12]}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _cache[key] = (_function(module, func_name, implementation), _digest_file(path))
        return _cache[key]
    try:
        module = importlib.import_module(module_ref)
    except ImportError as exc:
        raise EvaluatorError(f"{implementation!r}: {exc}") from exc
    source = inspect.getsourcefile(module)
    code_digest = _digest_file(Path(source)) if source else sha512t24u(module_ref.encode())
    return _function(module, func_name, implementation), code_digest


def _function(module, func_name: str, implementation: str) -> Callable:
    func = getattr(module, func_name, None)
    if not callable(func):
        raise EvaluatorError(f"{implementation!r}: no function {func_name!r}")
    return func
