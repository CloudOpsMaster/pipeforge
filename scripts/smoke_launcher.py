"""Exercise a copied .pf checkout: cold bootstrap, job selection, and offline warm reuse."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="pipeforge-launcher-") as directory:
        application = Path(directory) / "application with spaces"
        shutil.copytree(
            root / "examples/pipelines/basic",
            application,
            ignore=shutil.ignore_patterns(".pipeforge", "__pycache__", "greeting.txt"),
        )
        framework = application / ".pf"
        framework.mkdir()
        for name in ("src", "scripts", "schema"):
            shutil.copytree(
                root / name, framework / name, ignore=shutil.ignore_patterns("__pycache__")
            )
        for name in ("run", "pyproject.toml", "requirements-runtime.txt", "README.md", "LICENSE"):
            shutil.copy2(root / name, framework / name)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        launcher = str(framework / "run")
        subprocess.run(
            [launcher, "build", "--color", "always"],
            cwd=application,
            env=env,
            check=True,
            timeout=240,
        )
        if (application / "greeting.txt").read_text() != "Hello PipeForge":
            raise SystemExit("Launcher did not execute the application's pipeline.")
        first_runtimes = list((application / ".pipeforge/runtime").glob("*/.ready"))
        if len(first_runtimes) != 1:
            raise SystemExit("Expected one initialized runtime.")
        stamp = first_runtimes[0].stat().st_mtime_ns
        # Cached operation must need neither a package index nor a reinstall.
        env["PIP_NO_INDEX"] = "1"
        result = subprocess.run(
            [launcher, "build", "--no-needs", "-vv"],
            cwd=application,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        if (
            "preparing isolated runtime" in result.stdout
            or first_runtimes[0].stat().st_mtime_ns != stamp
        ):
            raise SystemExit("Warm launch unexpectedly rebuilt the runtime.")
        data = json.loads((application / ".pipeforge/latest/report.json").read_text())
        outcomes = {job["name"]: job["status"] for job in data["jobs"]}
        if outcomes != {"test": "skipped", "build": "success"}:
            raise SystemExit("Launcher selection was not forwarded correctly.")
        (application / "src").mkdir()
        subprocess.run(
            [launcher, "--list"], cwd=application / "src", env=env, check=True, timeout=15
        )
        if (framework / ".pipeforge").exists():
            raise SystemExit("Runtime leaked into the framework checkout.")
    print("Launcher cold boot, offline cache reuse, discovery, and reports passed.")


if __name__ == "__main__":
    main()
