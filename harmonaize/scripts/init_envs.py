#!/usr/bin/env python3
"""Create the env files and fill in placeholder secrets with random values.

Local development needs no manual step: the ``setup`` service in
docker-compose.local.yml runs this script inside the container before the app
starts. For a docker-compose production deployment, run it once on the host
from the ``harmonaize/`` directory:

    python3 scripts/init_envs.py --production  # also .envs/.production/*

What it does, and never more than once per value:

- Creates ``.envs/.local/.django`` if it is missing (from
  ``.envs/.local/.django.example`` when present, otherwise a built-in default).
- Replaces empty or ``replace-with-...`` values of secret settings (Django
  secret key, admin URL, Flower credentials, analysis salt, and with
  ``--production`` the PostgreSQL credentials) with strong random values.
  Local PostgreSQL credentials are left as they are: the local database only
  listens on 127.0.0.1, and PostgreSQL keeps the user it was first created with.
- Leaves every value you have already set untouched.
- Marks tracked template files it changed as ``skip-worktree`` so the generated
  secrets are not committed by accident.
- Lists the settings it cannot generate for you, such as third-party API keys.

It prints the names of the settings it changed, never their values.
Standard library only, so it runs before any container is built.
"""

from __future__ import annotations

import argparse
import secrets
import string
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DJANGO = ROOT / ".envs/.local/.django"
LOCAL_DJANGO_EXAMPLE = ROOT / ".envs/.local/.django.example"
PRODUCTION_DJANGO = ROOT / ".envs/.production/.django"
PRODUCTION_POSTGRES = ROOT / ".envs/.production/.postgres"

DEFAULT_LOCAL_DJANGO = """\
# General
USE_DOCKER=yes
IPYTHONDIR=/app/.ipython
DJANGO_SECRET_KEY=
# Redis
REDIS_URL=redis://redis:6379/0
# Flower
CELERY_FLOWER_USER=
CELERY_FLOWER_PASSWORD=
# Analysis module (see README)
ANALYSIS_ENABLED=false
# Optional integrations: add the keys you need
OPENAI_API_KEY=
MAPBOX_ACCESS_TOKEN=
"""


def _token(length: int = 50) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _username() -> str:
    # PostgreSQL role names must start with a letter.
    return secrets.choice(string.ascii_lowercase) + _token(15).lower()


GENERATORS = {
    "DJANGO_SECRET_KEY": lambda: _token(64),
    "DJANGO_ADMIN_URL": lambda: _token(24) + "/",
    "CELERY_FLOWER_USER": _username,
    "CELERY_FLOWER_PASSWORD": lambda: _token(40),
    "POSTGRES_USER": _username,
    "POSTGRES_PASSWORD": lambda: _token(40),
    "ANALYSIS_DEIDENTIFICATION_SALT": lambda: _token(64),
}

# Settings added to a file when they are missing entirely.
ADD_IF_MISSING = {
    PRODUCTION_DJANGO: ["ANALYSIS_DEIDENTIFICATION_SALT"],
}

# Settings the script cannot invent. Reported if still empty or a placeholder.
MANUAL = {
    LOCAL_DJANGO: ["OPENAI_API_KEY", "MAPBOX_ACCESS_TOKEN"],
    PRODUCTION_DJANGO: [
        "DJANGO_ALLOWED_HOSTS",
        "OPENAI_API_KEY",
        "SENDGRID_API_KEY",
        "DJANGO_AZURE_ACCOUNT_NAME",
        "DJANGO_AZURE_ACCOUNT_KEY",
        "DJANGO_AZURE_CONTAINER_NAME",
    ],
}


def is_placeholder(value: str) -> bool:
    value = value.strip().strip("\"'")
    return value == "" or value.lower().startswith(("replace-with-", "!!!set", "changeme"))


def read_env(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def parse(lines: list[str]) -> dict[str, str]:
    values = {}
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value
    return values


def postgres_volume_exists(prefix: str) -> bool:
    """True when Docker already has a database volume for this stack.

    PostgreSQL only creates its user on first start, so changing the
    credentials of an existing volume would lock the app out.
    """
    try:
        result = subprocess.run(
            ["docker", "volume", "ls", "-q", "--filter", f"name={prefix}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(result.stdout.strip())


def _git(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=False)
    except OSError:  # git is not installed, e.g. inside the container
        return None


def protect_tracked_file(path: Path) -> None:
    """Stop git from offering generated secrets in tracked templates for commit."""
    rel = path.relative_to(ROOT)
    tracked = _git("ls-files", "--error-unmatch", str(rel))
    if tracked is not None and tracked.returncode == 0:
        _git("update-index", "--skip-worktree", str(rel))
        print(f"  {rel}: marked skip-worktree so these secrets stay out of commits")


def fill(path: Path, *, skip: set[str] = frozenset()) -> None:
    lines = read_env(path)
    changed = []
    for i, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in GENERATORS and key not in skip and is_placeholder(value):
            lines[i] = f"{key}={GENERATORS[key]()}"
            changed.append(key)
    present = parse(lines)
    for key in ADD_IF_MISSING.get(path, []):
        if key not in present:
            lines.append(f"{key}={GENERATORS[key]()}")
            changed.append(key)
    rel = path.relative_to(ROOT)
    if changed:
        path.write_text("\n".join(lines) + "\n")
        print(f"  {rel}: generated {', '.join(changed)}")
        protect_tracked_file(path)
    else:
        print(f"  {rel}: nothing to generate")


def report_manual(paths: list[Path]) -> None:
    todo = []
    for path in paths:
        values = parse(read_env(path))
        todo += [
            f"{path.relative_to(ROOT)}: {key}"
            for key in MANUAL.get(path, [])
            if is_placeholder(values.get(key, ""))
        ]
    if todo:
        print("\nSet these yourself if you need them (the app starts without them):")
        for item in todo:
            print(f"  - {item}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--production", action="store_true", help="also fill .envs/.production/*")
    args = parser.parse_args()

    print("Preparing env files:")
    if not LOCAL_DJANGO.exists():
        LOCAL_DJANGO.parent.mkdir(parents=True, exist_ok=True)
        source = LOCAL_DJANGO_EXAMPLE.read_text() if LOCAL_DJANGO_EXAMPLE.exists() else DEFAULT_LOCAL_DJANGO
        LOCAL_DJANGO.write_text(source)
        print(f"  {LOCAL_DJANGO.relative_to(ROOT)}: created")
    fill(LOCAL_DJANGO)
    manual = [LOCAL_DJANGO]

    if args.production:
        fill(PRODUCTION_DJANGO)
        production_skip = set()
        if postgres_volume_exists("harmonaize_production_postgres_data"):
            production_skip = {"POSTGRES_USER", "POSTGRES_PASSWORD"}
        fill(PRODUCTION_POSTGRES, skip=production_skip)
        manual.append(PRODUCTION_DJANGO)

    report_manual(manual)
    return 0


if __name__ == "__main__":
    sys.exit(main())
