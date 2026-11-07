"""Git / GitHub operations used by the updater (via git and the gh CLI)."""
from __future__ import annotations

import base64
import json
import re
import subprocess
from typing import Any

from .state_io import ROOT

META_RE = re.compile(r"<!-- auto-proposal (\{.*?\}) -->", re.S)


def run(*args: str, input: str | None = None, check: bool = True) -> str:
    p = subprocess.run(args, cwd=ROOT, input=input, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])}… failed: {p.stderr.strip()[:500]}")
    return p.stdout


def commit_and_push(paths: list[str], message: str) -> bool:
    run("git", "add", "--", *paths)
    if not run("git", "diff", "--cached", "--name-only").strip():
        return False
    run("git", "commit", "-m", message)
    run("git", "pull", "--rebase", "--autostash", "origin", "main")
    run("git", "push", "origin", "HEAD:main")
    return True


def list_auto_prs(repo: str) -> list[dict[str, Any]]:
    out = run("gh", "pr", "list", "--repo", repo, "--state", "all", "--limit", "100",
              "--json", "number,state,headRefName,body,url,mergeable")
    prs = []
    for pr in json.loads(out or "[]"):
        if not pr["headRefName"].startswith("auto/"):
            continue
        m = META_RE.search(pr.get("body") or "")
        pr["meta"] = json.loads(m.group(1)) if m else {}
        prs.append(pr)
    return prs


def create_pr(repo: str, branch: str, state_text: str, title: str, body: str) -> str:
    sha = run("gh", "api", f"repos/{repo}/git/ref/heads/main", "--jq", ".object.sha").strip()
    run("gh", "api", "-X", "POST", f"repos/{repo}/git/refs", "-f", f"ref=refs/heads/{branch}", "-f", f"sha={sha}")
    file_sha = run("gh", "api", f"repos/{repo}/contents/state.yaml?ref={branch}", "--jq", ".sha").strip()
    payload = {"message": title, "branch": branch, "sha": file_sha,
               "content": base64.b64encode(state_text.encode("utf-8")).decode("ascii")}
    run("gh", "api", "-X", "PUT", f"repos/{repo}/contents/state.yaml", "--input", "-", input=json.dumps(payload))
    return run("gh", "pr", "create", "--repo", repo, "--head", branch, "--base", "main",
               "--title", title, "--body", body).strip()


def close_pr(repo: str, number: int, comment: str) -> None:
    run("gh", "pr", "close", str(number), "--repo", repo, "--comment", comment, "--delete-branch", check=False)


def enable_workflow(repo: str, workflow: str) -> bool:
    """Re-enable the scheduled workflow (resets GitHub's 60-day inactivity timer)."""
    try:
        run("gh", "api", "-X", "PUT", f"repos/{repo}/actions/workflows/{workflow}/enable")
        return True
    except RuntimeError as e:
        print(f"::warning::keepalive (enable workflow) failed: {e}")
        return False
