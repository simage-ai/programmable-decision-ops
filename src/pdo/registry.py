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

_cache: dict[tuple[str, str, str], tuple[Callable, str]] = {}  # (file, function, content digest)
_module_digests: dict[str, str] = {}


class EvaluatorError(ValueError):
    """An implementation reference cannot be resolved."""


def _digest_bytes(data: bytes) -> str:
    return sha512t24u(data.replace(b"\r\n", b"\n"))


def resolve(implementation: str, base_dir: Path) -> tuple[Callable, str]:
    """The callable named by `implementation` and the digest of its module's source.

    Both are keyed on the source's content, not its timestamp, so the code that runs is always the
    code the digest names, even in a long-running server after an edit.
    """
    if ":" not in implementation:
        raise EvaluatorError(f"{implementation!r}: expected 'module.py:function' or 'package.module:function'")
    module_ref, func_name = implementation.rsplit(":", 1)
    if module_ref.endswith(".py"):
        path = (base_dir / module_ref).resolve()
        if not path.exists():
            raise EvaluatorError(f"{implementation!r}: {path} does not exist")
        code_digest = _digest_bytes(path.read_bytes())
        key = (str(path), func_name, code_digest)
        if key not in _cache:
            name = f"pdo_evaluator_{sha512t24u(str(path).encode())[:12]}_{code_digest[:8]}"
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _cache[key] = (_function(module, func_name, implementation), code_digest)
        return _cache[key]
    try:
        module = importlib.import_module(module_ref)
    except ImportError as exc:
        raise EvaluatorError(f"{implementation!r}: {exc}") from exc
    source = inspect.getsourcefile(module)
    if not source:
        raise EvaluatorError(f"{implementation!r}: no source file, so its code cannot be identified")
    code_digest = _digest_bytes(Path(source).read_bytes())
    if _module_digests.setdefault(module_ref, code_digest) != code_digest:
        module = importlib.reload(module)
        _module_digests[module_ref] = code_digest
    return _function(module, func_name, implementation), code_digest


def signature_problem(func: Callable, declared: set[str]) -> str | None:
    """Why `func` cannot be called with exactly the declared inputs and parameters, if it cannot.

    A parameter that is not declared would be a hidden input (its default would silently stand in for
    a value the graph does not track), so it is refused rather than left to its default.
    """
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return None
    named = {p.name for p in params if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)}
    undeclared = sorted(named - declared)
    if undeclared:
        return f"evaluator parameters {undeclared} are not declared inputs or parameters"
    if not any(p.kind == p.VAR_KEYWORD for p in params) and declared - named:
        return f"declared inputs or parameters {sorted(declared - named)} are not evaluator parameters"
    return None


def _function(module, func_name: str, implementation: str) -> Callable:
    func = getattr(module, func_name, None)
    if not callable(func):
        raise EvaluatorError(f"{implementation!r}: no function {func_name!r}")
    return func
