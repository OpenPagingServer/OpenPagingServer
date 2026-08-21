from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVICE_UNIT = "openpagingserver.service"
PROMPT = "Open Paging Server will stopped while cahce is being cleared. Contiune? [Y/N]:"
DEFAULT_ENDPOINT_MODULES_DIR = Path("/var/lib/openpagingserver/endpointmodules")
load_dotenv(PROJECT_ROOT / ".env")


def running_as_root() -> bool:
    get_effective_user_id = getattr(os, "geteuid", None)
    return get_effective_user_id is not None and get_effective_user_id() == 0


def systemd_unit_exists() -> bool:
    if shutil.which("systemctl") is None:
        return False

    result = subprocess.run(
        [
            "systemctl",
            "show",
            "--property=LoadState",
            "--value",
            SERVICE_UNIT,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args)
    return result.stdout.strip().lower() not in {"", "not-found"}


def service_is_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", SERVICE_UNIT],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 3:
        return False
    raise subprocess.CalledProcessError(result.returncode, result.args)


def run_systemctl(action: str) -> None:
    subprocess.run(["systemctl", action, SERVICE_UNIT], check=True)


def endpoint_module_cache_dir() -> Path:
    module_root = Path(
        os.getenv("ENDPOINT_MODULES_PATH", str(DEFAULT_ENDPOINT_MODULES_DIR))
    ).expanduser().resolve()
    cache_dir = Path(
        os.getenv("ENDPOINT_MODULE_CACHE_PATH", str(module_root / ".cache"))
    ).expanduser().resolve()

    try:
        cache_dir.relative_to(module_root)
    except ValueError as exc:
        raise ValueError(
            "ENDPOINT_MODULE_CACHE_PATH must be inside ENDPOINT_MODULES_PATH"
        ) from exc

    if cache_dir == module_root:
        raise ValueError(
            "ENDPOINT_MODULE_CACHE_PATH must not be the endpoint-modules directory itself"
        )
    return cache_dir


def python_cache_dirs() -> list[Path]:
    return sorted(
        (path for path in PROJECT_ROOT.rglob("__pycache__") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )


def remove_directory(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_symlink():
        path.unlink()
    else:
        shutil.rmtree(path)
    return True


def clear_caches() -> list[Path]:
    module_cache = endpoint_module_cache_dir()
    removed = []
    for cache_dir in python_cache_dirs():
        if remove_directory(cache_dir):
            removed.append(cache_dir)

    if remove_directory(module_cache):
        removed.append(module_cache)
    return removed


def main() -> int:
    if not running_as_root():
        print("Error: this script must be run as root.", file=sys.stderr)
        return 1

    try:
        confirmed = input(PROMPT).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCache clearing cancelled.")
        return 1

    if confirmed not in {"y", "yes"}:
        print("Cache clearing cancelled.")
        return 0

    try:
        service_exists = systemd_unit_exists()
        service_was_active = service_exists and service_is_active()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Error: unable to determine server status: {exc}", file=sys.stderr)
        return 1
    cleanup_error: Exception | None = None
    restart_error: Exception | None = None
    removed: list[Path] = []

    try:
        if service_was_active:
            print("Stopping Open Paging Server...")
            run_systemctl("stop")
        elif not service_exists:
            print("Open Paging Server systemd service was not found; continuing cleanup.")

        removed = clear_caches()
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        cleanup_error = exc
    finally:
        if service_was_active:
            try:
                print("Starting Open Paging Server...")
                run_systemctl("start")
            except (OSError, subprocess.CalledProcessError) as exc:
                restart_error = exc

    if cleanup_error is not None:
        print(f"Error: cache cleanup failed: {cleanup_error}", file=sys.stderr)
    if restart_error is not None:
        print(
            f"Error: Open Paging Server could not be restarted: {restart_error}",
            file=sys.stderr,
        )
    if cleanup_error is not None or restart_error is not None:
        return 1

    suffix = "y" if len(removed) == 1 else "ies"
    print(f"Cache clearing complete. Removed {len(removed)} cache director{suffix}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
