#!/usr/bin/env python3
"""Install an unmodified official native Claude CLI without npm or root."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import platform
import tarfile
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="2.1.316")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local/share/sui-regulator/tools")
    args = parser.parse_args()
    arch = {"x86_64": "x64", "aarch64": "arm64"}.get(platform.machine())
    if platform.system() != "Linux" or arch is None:
        parser.error("this installer currently supports Linux x86-64/aarch64 only")
    url = f"https://registry.npmjs.org/@anthropic-ai%2Fclaude-code-linux-{arch}/{args.version}"
    with urllib.request.urlopen(url, timeout=60) as response:
        metadata = json.load(response)
    with urllib.request.urlopen(metadata["dist"]["tarball"], timeout=180) as response:
        archive = response.read()
    algorithm, expected = metadata["dist"]["integrity"].split("-", 1)
    if algorithm != "sha512" or base64.b64encode(hashlib.sha512(archive).digest()).decode() != expected:
        raise RuntimeError("official package integrity mismatch")
    directory = args.prefix.expanduser().resolve() / "claude-code" / args.version
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        candidates = [
            member for member in bundle.getmembers() if member.isfile() and member.name.endswith("/claude")
        ]
        if len(candidates) != 1:
            raise RuntimeError("unexpected package layout")
        source = bundle.extractfile(candidates[0])
        assert source is not None
        target = directory / "claude"
        temporary = directory / "claude.download"
        with temporary.open("xb") as output:
            output.write(source.read())
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o700)
        temporary.replace(target)
    receipt = {
        "version": args.version,
        "source": url,
        "integrity": metadata["dist"]["integrity"],
        "binary": str(target),
    }
    (directory / "install.json").write_text(json.dumps(receipt, indent=2) + "\n")
    bindir = Path.home() / ".local/bin"
    bindir.mkdir(parents=True, exist_ok=True)
    link = bindir / "claude"
    if not link.exists() and not link.is_symlink():
        link.symlink_to(target)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
