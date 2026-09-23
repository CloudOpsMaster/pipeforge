"""Run a checksum-pinned Gitleaks binary against all available Git history."""

import hashlib
import io
import platform
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSION = "8.30.1"
CHECKSUMS = {
    "darwin_arm64": "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5",
    "darwin_x64": "dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709",
    "linux_arm64": "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080",
    "linux_x64": "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
}


def main():
    architecture = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x64"}[platform.machine()]
    target = f"{platform.system().lower()}_{architecture}"
    url = f"https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{target}.tar.gz"
    with urllib.request.urlopen(url, timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != CHECKSUMS[target]:
        raise RuntimeError("Gitleaks download checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="pipeforge-gitleaks-") as temporary:
        executable = Path(temporary) / "gitleaks"
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            member = bundle.getmember("gitleaks")
            if not member.isfile():
                raise RuntimeError("Expected a regular executable")
            stream = bundle.extractfile(member)
            assert stream is not None
            executable.write_bytes(stream.read())
        executable.chmod(0o700)
        subprocess.run(
            [str(executable), "git", "--log-opts=--all", "--redact=100", "--no-banner", "."],
            check=True,
        )


if __name__ == "__main__":
    main()
