# SPDX-License-Identifier: Apache-2.0
# https://www.apache.org/licenses/LICENSE-2.0
"""The working copy under the tracker: Git or SVN.

Only three things are needed from it: what changed under the tracker root,
a history-preserving move (closing a report in a folder-per-state tracker),
and the commit-and-push that shares a session's work with the team. All of
it is scoped to the tracker root — nothing outside it is ever staged.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class VcsError(RuntimeError):
    pass


class VcsRefused(VcsError):
    """A publish that would send something other than tracker changes."""


@dataclass
class WorkingCopy:
    kind: str  # "git", "svn", or "none"
    top: Path

    def _run(self, *args: str, check: bool = True) -> str:
        try:
            proc = subprocess.run(args, cwd=self.top, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise VcsError(f"{args[0]} not found on PATH") from exc
        if check and proc.returncode != 0:
            raise VcsError(f"{' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return proc.stdout

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.top.resolve()).as_posix()

    # ------------------------------------------------------------ status

    def status(self, root: Path) -> list[dict[str, str]]:
        if self.kind == "git":
            out = self._run("git", "status", "--porcelain=v1", "--untracked-files=all", "--", self.rel(root))
            return [{"status": ln[:2].strip() or "?", "path": ln[3:]} for ln in out.splitlines() if ln]
        if self.kind == "svn":
            out = self._run("svn", "status", self.rel(root))
            return [
                {"status": ln[0], "path": ln[8:].strip()} for ln in out.splitlines() if ln and ln[0] != " "
            ]
        return []

    def push_destination(self) -> str | None:
        """The remote-tracking ref `git push` sends this branch to (`@{push}`), or None.

        Outgoing commits are measured against that ref, not against "every
        remote": a commit already on `origin/feature` but not on `origin/main`
        would still be sent when the branch pushes to `main`.
        """
        if self.kind != "git":
            return None
        try:
            ref = self._run("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{push}").strip()
            if not ref:
                return None
            self._run("git", "rev-parse", "--verify", "--quiet", ref)
        except VcsError:
            return None
        return ref

    def outgoing(self) -> list[str] | None:
        """Commits a push would send; None when the push destination is unknown."""
        if self.kind != "git":
            return []
        dest = self.push_destination()
        if dest is None:
            return None
        out = self._run("git", "rev-list", "HEAD", "--not", dest)
        return [ln for ln in out.split() if ln]

    def _check_outgoing_scoped(self, rel_root: str) -> list[str]:
        """Refuse a push that would also publish anything outside the tracker
        folder — on a shared branch that is someone else's work — or whose
        destination cannot be established. Returns the outgoing commits."""
        commits = self.outgoing()
        if commits is None:
            raise VcsRefused(
                "cannot tell where `git push` would send this branch (no upstream or push destination); "
                "set one (for example `git push -u origin <branch>` once, by hand) before publishing"
            )
        prefix = "" if rel_root in ("", ".") else rel_root.rstrip("/") + "/"
        for sha in commits:
            # -m: merge commits are compared with each parent, so a change that
            # exists only in the merge result is seen too. -z: paths unquoted.
            out = self._run(
                "git", "diff-tree", "--root", "-m", "-r", "--no-commit-id", "--name-only", "-z", sha
            )
            paths = [p for p in out.split("\0") if p]
            stray = [p for p in paths if prefix and not p.startswith(prefix)]
            if stray:
                raise VcsRefused(
                    f"the branch has unpushed commit {sha[:12]} touching files outside {rel_root}/ "
                    f"({', '.join(stray[:3])}); pushing would publish them too. Push or move that work "
                    "first, or keep the tracker on its own branch."
                )
        return commits

    def unpushed(self) -> int | None:
        """Commits not yet at the push destination; None when that is unknown.

        A publish whose commit succeeded but whose push failed leaves the tracker
        clean yet unpublished; this is how `pending` and `publish` still see it.
        """
        if self.kind != "git":
            return 0
        commits = self.outgoing()
        return None if commits is None else len(commits)

    def remotes(self) -> list[str]:
        if self.kind == "git":
            out = self._run("git", "remote", "-v", check=False)
            return sorted({ln.split()[1] for ln in out.splitlines() if len(ln.split()) >= 2})
        if self.kind == "svn":
            out = self._run("svn", "info", "--show-item", "url", check=False)
            return [out.strip()] if out.strip() else []
        return []

    # ------------------------------------------------------------ move

    def _tracked(self, path: Path) -> bool:
        if self.kind == "git":
            return bool(self._run("git", "ls-files", "--", self.rel(path), check=False).strip())
        if self.kind == "svn":
            out = self._run("svn", "status", "--depth", "empty", self.rel(path), check=False)
            return not out.startswith("?")
        return False

    def move(self, src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if self.kind == "git" and self._tracked(src):
            self._run("git", "mv", self.rel(src), self.rel(dst))
        elif self.kind == "svn" and self._tracked(src):
            self._run(
                "svn", "add", "--parents", "--force", "--depth", "empty", self.rel(dst.parent), check=False
            )
            self._run("svn", "mv", self.rel(src), self.rel(dst))
        else:
            shutil.move(str(src), str(dst))

    # ------------------------------------------------------------ publish

    def publish(self, root: Path, message: str, trailers: list[str], push: bool) -> dict[str, object]:
        rel_root = self.rel(root)
        if self.kind == "git":
            if push:
                self._check_outgoing_scoped(rel_root)
            self._run("git", "add", "-A", "--", rel_root)
            if not self._run("git", "diff", "--cached", "--name-only", "--", rel_root).strip():
                # An earlier publish may have committed without pushing; with
                # --push, push it now (an unknown destination was refused above).
                if push and self._check_outgoing_scoped(rel_root):
                    self._run("git", "push")
                    revision = self._run("git", "rev-parse", "HEAD").strip()
                    return {
                        "committed": False,
                        "pushed": True,
                        "revision": revision,
                        "note": "pushed earlier commits",
                    }
                return {"committed": False, "pushed": False, "revision": None, "note": "nothing to commit"}
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
                fh.write(message)
                msg_path = fh.name
            args = ["git", "commit", "-F", msg_path]
            for trailer in trailers:
                args += ["--trailer", trailer]
            args += ["--", rel_root]
            try:
                self._run(*args)
            finally:
                Path(msg_path).unlink(missing_ok=True)
            revision = self._run("git", "rev-parse", "HEAD").strip()
            pushed = False
            if push:
                self._check_outgoing_scoped(rel_root)
                self._run("git", "push")
                pushed = True
            return {"committed": True, "pushed": pushed, "revision": revision}
        if self.kind == "svn":
            for entry in self.status(root):
                if entry["status"] == "?":
                    self._run("svn", "add", "--parents", "--force", entry["path"])
                elif entry["status"] == "!":
                    self._run("svn", "rm", "--force", entry["path"])
            if not self.status(root):
                return {"committed": False, "pushed": False, "revision": None, "note": "nothing to commit"}
            full = message.rstrip("\n") + ("\n\n" + "\n".join(trailers) if trailers else "") + "\n"
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
                fh.write(full)
                msg_path = fh.name
            try:
                out = self._run("svn", "commit", "-F", msg_path, rel_root)
            finally:
                Path(msg_path).unlink(missing_ok=True)
            m = re.search(r"Committed revision (\d+)", out)
            # An SVN commit is the publish: there is no separate push.
            return {"committed": True, "pushed": True, "revision": m.group(1) if m else None}
        raise VcsError("the tracker root is not inside a Git or SVN working copy")


def detect(root: Path) -> WorkingCopy:
    """Find the working copy that contains ``root`` (nearest ``.git`` / ``.svn``)."""
    start = root.resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return WorkingCopy("git", candidate)
        if (candidate / ".svn").is_dir():
            return WorkingCopy("svn", candidate)
    return WorkingCopy("none", start)
