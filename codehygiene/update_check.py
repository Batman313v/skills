"""codehygiene v0.7.0 — Periodic 12-hour release check against pyproject.toml metadata."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from urllib.request import urlopen

__all__ = [
    "CHECK_INTERVAL_SEC",
    "FETCH_TIMEOUT_SEC",
    "JOIN_TIMEOUT_SEC",
    "PYPROJECT_URL",
    "UpdateHandle",
    "emit_update_notice",
    "extract_project_version",
    "parse_version_tuple",
    "start_update_check",
]

PYPROJECT_URL = "https://raw.githubusercontent.com/brianbell-x/codehygiene/main/pyproject.toml"
CHECK_INTERVAL_SEC = 12 * 3600
FETCH_TIMEOUT_SEC = 0.6
JOIN_TIMEOUT_SEC = 0.35

_OPT_OUT_FALSE = frozenset({"", "0", "false", "no", "off"})
_DISABLE_ENV_VARS = ("CODEHYGIENE_NO_UPDATE_CHECK", "CI", "PYTEST_CURRENT_TEST")
_PLATFORM_CACHE_ROOTS = {
    "win32": ("LOCALAPPDATA", ("AppData", "Local")),
    "darwin": ("", ("Library", "Caches")),
}
_DEFAULT_CACHE_SPEC = ("XDG_CACHE_HOME", (".cache",))


@dataclass(frozen=True)
class UpdateHandle:
    installed: str
    cached_latest: str
    result_box: list[str]
    worker: threading.Thread | None


def extract_project_version(toml_text: str) -> str:
    """Return `[project] version` from a `pyproject.toml` string, or `""` if absent."""
    in_project = False
    for raw_line in toml_text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
        elif in_project and line.startswith("version") and "=" in line:
            rhs = line.split("=", 1)[1].split("#", 1)[0]
            return rhs.strip().strip("\"'")
    return ""


def parse_version_tuple(raw_ver: str) -> tuple[int, ...]:
    """Parse `vX.Y.Z` into an integer tuple, or `()` when malformed."""
    clean_ver = raw_ver.strip().lstrip("v").split("-", 1)[0]
    tokens = clean_ver.split(".")
    if not all(tok.isdigit() for tok in tokens):
        return ()
    return tuple(int(tok) for tok in tokens)


def _is_disabled_by_env() -> bool:
    return any(
        os.environ.get(name, "").strip().lower() not in _OPT_OUT_FALSE
        for name in _DISABLE_ENV_VARS
    )


def _cache_file_path() -> Path:
    env_key, rel_parts = _PLATFORM_CACHE_ROOTS.get(sys.platform, _DEFAULT_CACHE_SPEC)
    override = os.environ.get(env_key, "").strip() if env_key else ""
    root = Path(override) if override else Path.home().joinpath(*rel_parts)
    return root / "codehygiene" / "update.json"


def _read_cache(cache_path: Path) -> tuple[float, str]:
    if not cache_path.is_file():
        return 0.0, ""
    try:
        payload = json.loads(cache_path.read_text("utf-8"))
        return float(payload["checked_at"]), str(payload["latest"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0, ""


def _write_cache(cache_path: Path, checked_at: float, latest: str) -> bool:
    record = json.dumps({"checked_at": round(checked_at, 1), "latest": latest})
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(f"{record}\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _refresh_worker(cache_path: Path, fallback_latest: str, result_box: list[str]) -> None:
    try:
        with urlopen(PYPROJECT_URL, timeout=FETCH_TIMEOUT_SEC) as resp:
            fetched = extract_project_version(resp.read(16_384).decode("utf-8", "replace"))
    except OSError:
        fetched = ""
    latest = fetched or fallback_latest
    _write_cache(cache_path, time.time(), latest)
    if latest:
        result_box.append(latest)


def start_update_check(installed: str, *, disabled: bool = False) -> UpdateHandle | None:
    """Return a cached or background-refreshing release check handle."""
    if disabled or _is_disabled_by_env():
        return None
    cache_path = _cache_file_path()
    checked_at, cached_latest = _read_cache(cache_path)
    if 0.0 <= (time.time() - checked_at) < CHECK_INTERVAL_SEC:
        return UpdateHandle(installed, cached_latest, [], None)

    result_box: list[str] = []
    worker = threading.Thread(
        target=_refresh_worker,
        args=(cache_path, cached_latest, result_box),
        daemon=True,
    )
    worker.start()
    return UpdateHandle(installed, cached_latest, result_box, worker)


def emit_update_notice(handle: UpdateHandle | None, stream: TextIO | None = None) -> bool:
    """Write a single-line upgrade notice to `stream` (default `stderr`) when newer."""
    if handle is None:
        return False
    if handle.worker is not None:
        handle.worker.join(timeout=JOIN_TIMEOUT_SEC)

    latest = handle.result_box[0] if handle.result_box else handle.cached_latest
    installed_ver = parse_version_tuple(handle.installed)
    latest_ver = parse_version_tuple(latest)
    if not installed_ver or not latest_ver or latest_ver <= installed_ver:
        return False

    target = stream if stream is not None else sys.stderr
    target.write(
        f"note: codehygiene {latest} available (installed {handle.installed}) "
        "— run `npx skills update codehygiene -y` (add `-g` if global)\n"
    )
    return True
