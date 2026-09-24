"""Standard-library-only, cached launcher for a pinned framework checkout."""

import argparse
import fcntl
import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path


def config_path(arguments: list[str], directory: Path) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-f", "--file", type=Path)
    parsed, _ = parser.parse_known_args(arguments)
    if parsed.file:
        path = parsed.file.expanduser().resolve()
        if not path.is_file():
            raise ValueError("Configuration file does not exist.")
        return path
    for parent in (directory, *directory.parents):
        candidate = parent / "pipeforge.yml"
        if candidate.is_file():
            return candidate
    raise ValueError("Cannot find pipeforge.yml; create one or pass -f PATH.")


def fingerprint(framework: Path) -> str:
    digest = hashlib.sha256()
    digest.update(f"{sys.executable}:{sys.version}:{framework}".encode())
    files = [framework / "pyproject.toml", framework / "requirements-runtime.txt"]
    files.extend(sorted((framework / "src/pipeforge").rglob("*.py")))
    files.extend(sorted((framework / "schema").glob("*.json")))
    for path in files:
        digest.update(str(path.relative_to(framework)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:24]


def ensure_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Runtime/cache directories must not be symlinks.")
    path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise ValueError("Runtime/cache path is not a directory.")


def prepare_runtime(framework: Path, application: Path) -> Path:
    root = application / ".pipeforge"
    ensure_directory(root)
    for name in ("runtime", "cache"):
        ensure_directory(root / name)
    ensure_directory(root / "cache/pip")
    identity = fingerprint(framework)
    runtime = root / "runtime" / identity
    if runtime.is_symlink():
        raise ValueError("Runtime must not be a symlink.")
    lock_path = root / "runtime" / f"{identity}.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        python = runtime / "bin/python"
        marker = runtime / ".ready"
        if not marker.exists() or not python.exists():
            print(
                "PipeForge: preparing isolated runtime (first run or framework update)…", flush=True
            )
            ensure_directory(runtime)
            venv.EnvBuilder(with_pip=True).create(runtime)
            env = os.environ.copy()
            env["PIP_CACHE_DIR"] = str(root / "cache/pip")
            env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
            try:
                subprocess.run(
                    [
                        str(python),
                        "-m",
                        "pip",
                        "install",
                        "--quiet",
                        "--no-input",
                        "--no-deps",
                        "-r",
                        str(framework / "requirements-runtime.txt"),
                        str(framework),
                    ],
                    env=env,
                    cwd=application,
                    check=True,
                    capture_output=True,
                    timeout=180,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                # Installer output may contain authenticated index URLs; never dump it unredacted.
                raise ValueError(
                    "Runtime installation failed. Check network/index access "
                    "and Python venv support; retry ./run."
                ) from None
            marker.write_text(identity, encoding="utf-8")
        return python


def main() -> int:
    framework = Path(__file__).resolve().parents[1]
    arguments = sys.argv[1:]
    try:
        if sys.version_info < (3, 11) or os.name != "posix":
            raise ValueError("PipeForge launcher requires Python 3.11+ on Linux or macOS.")
        help_only = any(arg in ("--help", "-h", "--version") for arg in arguments)
        config = None if help_only else config_path(arguments, Path.cwd())
        python = prepare_runtime(framework, config.parent if config else Path.cwd())
        if arguments and arguments[0] in (
            "validate",
            "jobs",
            "run",
            "plan",
            "graph",
            "exec-job",
            "render",
            "--version",
        ):
            cli = arguments
        else:
            cli = ["run", *arguments]
        if config:
            # An absolute -f ensures upward discovery is unambiguous in the package CLI.
            cli.extend(["--file", str(config)])
        env = os.environ.copy()
        env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
        env.pop("PYTHONPATH", None)
        os.execve(python, [str(python), "-m", "pipeforge", *cli], env)
    except (OSError, ValueError) as error:
        message = (
            str(error)
            if isinstance(error, ValueError)
            else "Cannot prepare runtime; check .pipeforge permissions and disk space."
        )
        print(f"PipeForge: {message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
