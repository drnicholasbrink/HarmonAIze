"""Hardening helpers for executing ``MappingRule.transform_code``.

Transform code is still executed with ``exec``/``eval`` (see
``health.tasks._compile_transform_callable``). This is NOT a security boundary;
these helpers only narrow the attack surface and bound resource usage:

* the code must pass ``health.models.validate_safe_transform_code`` (AST
  allow-list, no dunder names/attributes, no frame/generator introspection
  attributes) immediately before it is compiled, not only when it was saved;
* the parsed AST is rewritten so ``+``, ``*``, ``%`` and ``**`` (including
  augmented assignment) go through guarded helpers that refuse huge integers,
  huge exponentiation and huge string/list repetition or concatenation;
* ``range`` is bounded and only a small set of pure built-ins is exposed;
* each call is wall-clock limited with ``SIGALRM`` when running in the main
  thread (Celery prefork workers), which interrupts long Python-level loops.
"""

from __future__ import annotations

import ast
import copy
import logging
import re
import signal
import threading
from collections.abc import Callable
from contextlib import contextmanager

logger = logging.getLogger(__name__)

MAX_INT_BITS = 100_000
MAX_SEQUENCE_LENGTH = 1_000_000
MAX_FORMAT_WIDTH = 10_000
TRANSFORM_TIME_LIMIT_SECONDS = 2.0

_HELPER_PREFIX = "__hz_"


class TransformLimitError(ValueError):
    """A transform exceeded a size limit."""


class TransformTimeoutError(TransformLimitError):
    """A transform exceeded its wall-clock time limit."""


# --------------------------------------------------------------------------- guarded operators


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_int(value):
    if _is_int(value) and value.bit_length() > MAX_INT_BITS:
        msg = "Integer result is too large."
        raise TransformLimitError(msg)
    return value


def _check_len(size: int) -> None:
    if size > MAX_SEQUENCE_LENGTH:
        msg = f"Result would be too long ({size} > {MAX_SEQUENCE_LENGTH})."
        raise TransformLimitError(msg)


_SIZED = (str, bytes, list, tuple)


def guarded_pow(base, exponent):
    if _is_int(base) and _is_int(exponent) and exponent > 0 and abs(base) > 1:
        if abs(base).bit_length() * exponent > MAX_INT_BITS:
            msg = "Exponentiation result is too large."
            raise TransformLimitError(msg)
    return _check_int(base**exponent)


def guarded_mul(left, right):
    if isinstance(left, _SIZED) and _is_int(right):
        _check_len(len(left) * max(right, 0))
    elif isinstance(right, _SIZED) and _is_int(left):
        _check_len(len(right) * max(left, 0))
    elif _is_int(left) and _is_int(right):
        if left.bit_length() + right.bit_length() > MAX_INT_BITS + 1:
            msg = "Integer result is too large."
            raise TransformLimitError(msg)
    return left * right


def guarded_add(left, right):
    if isinstance(left, _SIZED) and isinstance(right, _SIZED):
        _check_len(len(left) + len(right))
    return left + right


_FORMAT_NUMBER = re.compile(r"%[^a-zA-Z%]*?(\d+)")


def guarded_mod(left, right):
    if isinstance(left, (str, bytes)):
        text = left if isinstance(left, str) else left.decode("latin-1")
        for match in _FORMAT_NUMBER.finditer(text):
            if int(match.group(1)) > MAX_FORMAT_WIDTH:
                msg = "String format width/precision is too large."
                raise TransformLimitError(msg)
    return _check_int(left % right)


def guarded_range(*args):
    result = range(*args)
    _check_len(len(result))
    return result


_OP_HELPERS = {
    ast.Pow: ("__hz_pow", guarded_pow),
    ast.Mult: ("__hz_mul", guarded_mul),
    ast.Add: ("__hz_add", guarded_add),
    ast.Mod: ("__hz_mod", guarded_mod),
}


class _GuardOperators(ast.NodeTransformer):
    """Route ``** * + %`` (also as ``op=``) through the guarded helpers."""

    def visit_BinOp(self, node: ast.BinOp):
        self.generic_visit(node)
        helper = _OP_HELPERS.get(type(node.op))
        if helper is None:
            return node
        return ast.copy_location(
            ast.Call(func=ast.Name(id=helper[0], ctx=ast.Load()), args=[node.left, node.right], keywords=[]),
            node,
        )

    def visit_AugAssign(self, node: ast.AugAssign):
        self.generic_visit(node)
        helper = _OP_HELPERS.get(type(node.op))
        if helper is None:
            return node
        load_target = copy.deepcopy(node.target)
        load_target.ctx = ast.Load()
        call = ast.Call(func=ast.Name(id=helper[0], ctx=ast.Load()), args=[load_target, node.value], keywords=[])
        return ast.copy_location(ast.Assign(targets=[node.target], value=call), node)


# --------------------------------------------------------------------------- builtins / time limit

SAFE_BUILTINS = {
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "round": round,
    "abs": abs,
    "min": min,
    "max": max,
    "len": len,
    "sum": sum,
    "any": any,
    "all": all,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "range": guarded_range,
    "zip": zip,
    "list": list,
    "tuple": tuple,
    "dict": dict,
    "set": set,
}


def _build_globals() -> dict:
    namespace = {"__builtins__": dict(SAFE_BUILTINS)}
    for name, func in _OP_HELPERS.values():
        namespace[name] = func
    return namespace


@contextmanager
def time_limit(seconds: float):
    """Raise TransformTimeoutError if the block runs longer than ``seconds``.

    Uses ``SIGALRM``/``setitimer``, so it only applies in the main thread on
    POSIX; elsewhere it is a no-op. Long single C-level operations are not
    interrupted (the operator guards cover the known ones).
    """
    if (
        seconds <= 0
        or not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def _handler(signum, frame):
        msg = f"Transform exceeded {seconds} seconds."
        raise TransformTimeoutError(msg)

    previous_handler = signal.signal(signal.SIGALRM, _handler)
    previous_delay, previous_interval = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay:
            signal.setitimer(signal.ITIMER_REAL, previous_delay, previous_interval)


class HardenedTransform:
    """Callable wrapper applying the per-call time limit."""

    def __init__(self, func: Callable, seconds: float = TRANSFORM_TIME_LIMIT_SECONDS):
        self._func = func
        self._seconds = seconds

    def __call__(self, value):
        with time_limit(self._seconds):
            return self._func(value)


def compile_transform(code: str | None, *, seconds: float = TRANSFORM_TIME_LIMIT_SECONDS):
    """Validate, harden and compile transform code.

    Returns a callable or ``None`` when ``code`` is empty or does not define a
    lambda / ``transform`` function. Raises ``django.core.exceptions.ValidationError``
    when the code fails the safety validator.
    """
    from .models import validate_safe_transform_code

    if not code or not code.strip():
        return None
    code_str = code.strip()
    validate_safe_transform_code(code_str)

    tree = _GuardOperators().visit(ast.parse(code_str, mode="exec"))
    ast.fix_missing_locations(tree)
    safe_globals = _build_globals()

    # Compiling at definition time can itself run code (e.g. module-level
    # statements in the exec form), so keep it under the time limit too.
    with time_limit(seconds):
        if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Lambda):
            expression = ast.Expression(body=tree.body[0].value)
            func = eval(compile(expression, "<transform>", "eval"), safe_globals, {})  # noqa: S307
        else:
            local_ns: dict = {}
            exec(compile(tree, "<transform>", "exec"), safe_globals, local_ns)  # noqa: S102
            func = local_ns.get("transform")
    if not callable(func):
        return None
    return HardenedTransform(func, seconds)
