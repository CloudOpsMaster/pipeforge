"""Install the built wheel in a fresh venv and run it outside the checkout."""

import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def main() -> None:
    wheels = list(Path(sys.argv[1]).resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one wheel in the distribution directory.")
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="pipeforge-smoke-") as directory:
        temporary = Path(directory)
        venv.EnvBuilder(with_pip=True).create(temporary / "venv")
        binaries = temporary / "venv" / ("Scripts" if os.name == "nt" else "bin")
        python = binaries / "python"
        cli = binaries / "pipeforge"
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PATH"] = str(binaries) + os.pathsep + env.get("PATH", "")
        subprocess.run(
            [str(python), "-m", "pip", "install", str(wheels[0])],
            cwd=temporary,
            env=env,
            check=True,
            timeout=120,
        )
        (temporary / "pipeforge.yml").write_text(
            (root / "examples/hello-world/pipeforge.yml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        for args in (["--version"], ["validate"], ["run", "--dry-run"], ["run"]):
            subprocess.run([str(cli), *args], cwd=temporary, env=env, check=True, timeout=15)
    print("Wheel installation and CLI smoke checks passed outside the checkout.")


if __name__ == "__main__":
    main()
