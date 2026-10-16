"""Tests for the hardened transform-code validator and executor."""

import pytest
from django.core.exceptions import ValidationError

from health.models import validate_safe_transform_code
from health.safe_transform import TransformLimitError, TransformTimeoutError, compile_transform
from health.tasks import _compile_transform_callable


# ------------------------------------------------------------- existing patterns keep working

@pytest.mark.parametrize(
    ("code", "value", "expected"),
    [
        ("lambda value: float(value) * 2.54", 10, 25.4),
        ("lambda value: round(float(value) / 2.2046, 1)", 22.046, 10.0),
        ("lambda value: value ** 2", 3, 9),
        ("lambda value: {'1': 'Male', '2': 'Female'}.get(str(value))", 2, "Female"),
        ("lambda value: int(value) if value else None", "7", 7),
        ("lambda value: value.strip().upper().replace(' ', '_') if value else None", " a b ", "A_B"),
        ("lambda value: [x.strip() for x in value.split(',') if x.strip()] if value else []", "a, ,b", ["a", "b"]),
        ("lambda value: {k: v for k, v in zip(['a'], [value])}", 1, {"a": 1}),
        ("lambda value: sum(i for i in range(value))", 4, 6),
        ("lambda value: '%.1f kg' % value", 2.25, "2.2 kg"),
        ("lambda value: value * 3", "ab", "ababab"),
        (
            "def transform(value):\n"
            "    if not value:\n"
            "        return None\n"
            "    parts = value.strip().split()\n"
            "    return ' '.join(part.capitalize() for part in parts)",
            " hello world ",
            "Hello World",
        ),
        (
            "def transform(value):\n"
            "    cleaned = []\n"
            "    for item in value.split(','):\n"
            "        item = item.strip()\n"
            "        if item:\n"
            "            cleaned.append(item)\n"
            "    total = 1\n"
            "    total *= 2\n"
            "    total **= 3\n"
            "    total += len(cleaned)\n"
            "    return total",
            "a,,b",
            10,
        ),
    ],
)
def test_existing_transform_patterns_still_work(code, value, expected):
    validate_safe_transform_code(code)
    func = _compile_transform_callable(code)
    assert func is not None
    assert func(value) == expected


def test_empty_code_compiles_to_none():
    assert _compile_transform_callable("") is None
    assert _compile_transform_callable("   ") is None


# ------------------------------------------------------------- escape attempts rejected

@pytest.mark.parametrize(
    "code",
    [
        "lambda value: ().__class__.__bases__",
        "lambda value: ().__class__.__bases__[0].__subclasses__()",
        "lambda value: value.__class__.mro",
        "lambda value: __import__('os').system('id')",
        "lambda value: __builtins__",
        "lambda value: getattr(value, '__class__')",
        "lambda value: getattr",
        "lambda value: open('/etc/passwd').read()",
        "lambda value: eval('1')",
        "lambda value: type(value)",
        "lambda value: [g for g in [(x for x in [1])]][0].gi_frame.f_back.f_globals",
        "lambda value: (x for x in [1]).gi_code",
        "lambda value: value._private",
        "def transform(value):\n    import os\n    return os.name",
        "def transform(value):\n    value.attr = 1\n    return value",
        "lambda value: '{0.__class__}'.format(value)",
        "lambda value: __hz_pow(2, 3)",
        "lambda value: 9**9**9",
        "lambda value: 2 ** 100000",
        "lambda value: 'a' * 10**10 if False else 'a' * 10000000000",
    ],
)
def test_escape_and_dos_attempts_rejected(code):
    with pytest.raises(ValidationError):
        validate_safe_transform_code(code)
    assert _compile_transform_callable(code) is None


# ------------------------------------------------------------- runtime guards

@pytest.mark.parametrize(
    "code",
    [
        "lambda value: value ** value",
        "lambda value: (value + 1) ** (value * 2)",
        "lambda value: 'a' * (10 ** 10)",
        "lambda value: [0] * (value ** 9)",
        "lambda value: '%99999999s' % 'x'",
        "lambda value: list(range(10 ** 12))",
        "def transform(value):\n    s = 'x' * 1000\n    for i in range(100):\n        s += s\n    return s",
        "def transform(value):\n    n = 7\n    n **= 999999\n    return n",
    ],
)
def test_runtime_size_guards(code):
    func = compile_transform(code)
    with pytest.raises(TransformLimitError):
        func(10 ** 6)


def test_runtime_time_limit():
    func = compile_transform(
        "def transform(value):\n"
        "    total = 0\n"
        "    for i in range(1000000):\n"
        "        for j in range(1000000):\n"
        "            total = total + 1\n"
        "    return total",
        seconds=0.2,
    )
    with pytest.raises(TransformTimeoutError):
        func(1)


def test_executor_skips_rule_with_rejected_code():
    # Rejected code must not compile into a callable (executor then skips the rule).
    assert _compile_transform_callable("lambda value: value.__class__") is None
