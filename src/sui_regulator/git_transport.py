"""Git object transport. No checkout, hooks, merge driver, or repository code execution."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from .models import DomainError


class GitTransport:
    def __init__(
        self, path: Path, remote: str, *, author_name="Sui Git Bridge", author_email="sui-bridge@localhost"
    ):
        if not remote or remote.startswith("-") or "\n" in remote or "::" in remote:
            raise ValueError("invalid Git remote")
        if remote.startswith("https://") and ("@" in remote or "?" in remote):
            raise ValueError("use an external credential helper, not credentials in the remote URL")
        if not (
            remote.startswith(("https://", "ssh://", "/", "file://"))
            or re.fullmatch(r"[\w.-]+@[\w.-]+:[\w./-]+", remote)
        ):
            raise ValueError("remote must be HTTPS, SSH or an absolute local repository")
        self.path, self.remote = path, remote
        self.author_name, self.author_email = author_name, author_email
        if not path.exists():
            path.mkdir(parents=True, mode=0o700)
            self.run("init", "--bare", str(path))

    def run(self, *args, data=None, extra_env=None, allowed=(0,), limit=4 * 1024 * 1024):
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("GIT_") or k in {"GIT_SSH_COMMAND", "GIT_SSH", "GIT_ASKPASS"}
        }
        env.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_AUTHOR_NAME=self.author_name,
            GIT_AUTHOR_EMAIL=self.author_email,
            GIT_COMMITTER_NAME=self.author_name,
            GIT_COMMITTER_EMAIL=self.author_email,
        )
        env.update(extra_env or {})
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                result = subprocess.run(
                    [
                        "git",
                        "--git-dir",
                        str(self.path),
                        "-c",
                        "core.hooksPath=/dev/null",
                        "-c",
                        "core.fsmonitor=false",
                        "-c",
                        "protocol.ext.allow=never",
                        "-c",
                        "gc.auto=0",
                        *args,
                    ],
                    input=data,
                    stdout=output,
                    stderr=errors,
                    env=env,
                    timeout=30,
                )
            except subprocess.TimeoutExpired as exc:
                raise DomainError("GIT_TIMEOUT", f"Git {args[0]} exceeded 30 seconds") from exc
            if result.returncode not in allowed:
                # Raw stderr can contain remote credentials or local config values.
                raise DomainError("GIT_FAILED", f"Git {args[0]} failed with exit code {result.returncode}")
            output.seek(0)
            value = output.read(limit + 1)
            if len(value) > limit:
                raise DomainError("GIT_LIMIT", "Git object listing exceeds the configured bound")
            return result.returncode, value

    def fetch(self, branch, name, *, optional=False):
        ref = "refs/heads/" + branch
        _, listed = self.run("ls-remote", self.remote, ref)
        if not listed.strip():
            if optional:
                return None
            raise DomainError("MISSING_BRANCH", "the enrolled command branch does not exist")
        self.run("fetch", "--quiet", "--no-tags", self.remote, f"+{ref}:refs/bridge/{name}")
        return self.run("rev-parse", f"refs/bridge/{name}")[1].decode().strip()

    def ancestor(self, old, new):
        return self.run("merge-base", "--is-ancestor", old, new, allowed=(0, 1))[0] == 0

    def tree(self, sha):
        records = self.run("ls-tree", "-rz", sha)[1].split(b"\0")
        entries = {}
        for record in records:
            if not record:
                continue
            meta, name = record.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            entries[name.decode("utf-8")] = (mode, kind, oid)
        return entries

    def blob(self, entries, path, limit=65536):
        mode, kind, oid = entries.get(path, ("", "", ""))
        if mode not in {"100644", "100755"} or kind != "blob":
            raise DomainError("INVALID_BUNDLE", "expected a regular Git blob: " + path)
        if int(self.run("cat-file", "-s", oid)[1]) > limit:
            raise DomainError("BUNDLE_TOO_LARGE", "Git file exceeds size limit: " + path)
        return self.run("cat-file", "blob", oid, limit=limit)[1]

    def commit_files(self, files, *, parent=None, remove=(), message="Publish instance observations"):
        with tempfile.TemporaryDirectory(prefix="sui-git-index-") as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            self.run("read-tree", parent if parent else "--empty", extra_env=env)
            for path in remove:
                # --force-remove still consults a worktree; index-info works in a bare cache.
                self.run(
                    "update-index",
                    "-z",
                    "--index-info",
                    data=("0 " + "0" * 40 + "\t" + path + "\0").encode(),
                    extra_env=env,
                )
            for path, content in sorted(files.items()):
                if path.startswith(("/", "-")) or any(p in {"", ".", "..", ".git"} for p in path.split("/")):
                    raise ValueError("invalid publish path")
                blob = self.run("hash-object", "-w", "--stdin", data=content)[1].decode().strip()
                self.run("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", extra_env=env)
            tree = self.run("write-tree", extra_env=env)[1].decode().strip()
        args = ["commit-tree", tree]
        if parent:
            args.extend(["-p", parent])
        return self.run(*args, data=(message + "\n").encode())[1].decode().strip()

    def push(self, sha, branch):
        self.run("push", "--quiet", self.remote, f"{sha}:refs/heads/{branch}")
