import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def cli(tmp_path):
    def invoke(source, *args, env=None):
        config = tmp_path / "pipeforge.yml"
        config.write_text(source, encoding="utf-8")
        environment = os.environ.copy()
        environment.pop("DEMO_TOKEN", None)
        environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment["PATH"]
        if env:
            environment.update(env)
        return subprocess.run(
            [sys.executable, "-m", "pipeforge", *args, "--file", str(config)],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )

    return invoke
