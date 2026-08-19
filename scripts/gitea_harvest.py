#!/usr/bin/env python3
"""Out-of-band Gitea doc harvester — pulls ONLY markdown/office/pdf files out
of Gitea repos over the REST API (no git clone/pull, no working tree, no
code files) and drops them flat on disk. DronaCharya never talks to Gitea:
point `[notes].directories` at the output folder and run `dc sync-notes`
(or `dc add <folder>`) to ingest whatever landed there. This script has zero
dependency on the dronacharya package — stdlib only, runs anywhere.

Usage:
    export GITEA_TOKEN=xxxx
    python3 gitea_harvest.py --url https://gitea.example.internal \
        --repos myorg/proj1,myorg/proj2 --out /srv/dc-drop/gitea
    python3 gitea_harvest.py --url https://gitea.example.internal \
        --org myorg --out /srv/dc-drop/gitea
    python3 gitea_harvest.py --url https://gitea.example.internal \
        --all --out /srv/dc-drop/gitea --dry-run

Re-running is cheap and safe: a manifest (.gitea_harvest_manifest.json) keyed
by Gitea's own blob sha skips unchanged files and removes local copies of
files deleted upstream. Keep this extension list in sync with
`[notes].extensions` in dronacharya.toml (defaults match out of the box).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_EXTENSIONS = {
    ".tdl", ".md", ".markdown", ".txt", ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm",
}
IGNORE_PREFIXES = (".git/", "node_modules/", "vendor/", ".venv/", "__pycache__/",
                   "dist/", "build/")
MANIFEST_NAME = ".gitea_harvest_manifest.json"


def api(base_url: str, token: str, path: str, params: dict | None = None) -> dict:
    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"token {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GET {path} -> HTTP {e.code}: {e.read()[:300]!r}") from e


def raw(base_url: str, token: str, owner: str, repo: str, path: str, ref: str) -> bytes:
    url = (base_url.rstrip("/") + f"/api/v1/repos/{owner}/{repo}/raw/"
           + urllib.parse.quote(path) + "?" + urllib.parse.urlencode({"ref": ref}))
    req = urllib.request.Request(url, headers={"Authorization": f"token {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def discover_repos(base_url: str, token: str, org: str | None, want_all: bool) -> list[tuple[str, str]]:
    repos, page = [], 1
    while True:
        if org:
            batch = api(base_url, token, f"/api/v1/orgs/{org}/repos", {"page": page, "limit": 50})
        else:
            batch = api(base_url, token, "/api/v1/repos/search",
                        {"page": page, "limit": 50})["data"]
        if not batch:
            break
        for r in batch:
            repos.append((r["owner"]["login"], r["name"]))
        page += 1
        if not want_all and not org:
            break
    return repos


def harvest_repo(base_url: str, token: str, owner: str, repo: str, out_root: Path,
                  exts: set[str], manifest: dict, dry_run: bool) -> dict:
    stats = {"fetched": 0, "unchanged": 0, "removed": 0, "errors": 0}
    info = api(base_url, token, f"/api/v1/repos/{owner}/{repo}")
    branch = info.get("default_branch") or "main"
    tree = api(base_url, token, f"/api/v1/repos/{owner}/{repo}/git/trees/{branch}",
               {"recursive": "true", "per_page": "1000"})
    if tree.get("truncated"):
        print(f"  [warn] {owner}/{repo}: tree truncated (>1000 entries) — "
              "some files may be missed; consider narrowing to subpaths", file=sys.stderr)

    repo_key = f"{owner}/{repo}"
    seen_paths = set()
    repo_dir = out_root / f"{owner}__{repo}"
    prev = manifest.get(repo_key, {})

    for entry in tree.get("tree", []):
        if entry.get("type") != "blob":
            continue
        path = entry["path"]
        if any(path.startswith(p) for p in IGNORE_PREFIXES):
            continue
        if Path(path).suffix.lower() not in exts:
            continue
        seen_paths.add(path)
        sha = entry["sha"]
        if prev.get(path) == sha:
            stats["unchanged"] += 1
            continue
        dest = repo_dir / path
        if dry_run:
            print(f"  would fetch: {repo_key}/{path}")
            stats["fetched"] += 1
            continue
        try:
            content = raw(base_url, token, owner, repo, path, branch)
        except Exception as e:  # noqa: BLE001 - one bad file must not abort the repo
            print(f"  [error] {repo_key}/{path}: {e}", file=sys.stderr)
            stats["errors"] += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        prev[path] = sha
        stats["fetched"] += 1

    # files that vanished upstream since the last run
    for stale_path in set(prev) - seen_paths:
        stats["removed"] += 1
        if dry_run:
            print(f"  would remove: {repo_key}/{stale_path}")
            continue
        (repo_dir / stale_path).unlink(missing_ok=True)
        del prev[stale_path]

    if not dry_run:
        manifest[repo_key] = prev
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="Gitea base URL, e.g. https://gitea.internal")
    ap.add_argument("--token-env", default="GITEA_TOKEN",
                     help="env var holding the Gitea API token (default: GITEA_TOKEN)")
    ap.add_argument("--out", required=True, help="output drop folder")
    ap.add_argument("--repos", help="comma-separated owner/repo list")
    ap.add_argument("--org", help="harvest every repo in this org")
    ap.add_argument("--all", action="store_true", help="harvest every repo the token can see")
    ap.add_argument("--ext", action="append", default=[],
                     help="extra extension to include (repeatable), e.g. --ext .rst")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get(args.token_env)
    if not token:
        ap.error(f"set {args.token_env} to a Gitea API token")
    if not (args.repos or args.org or args.all):
        ap.error("specify one of --repos, --org, --all")

    exts = DEFAULT_EXTENSIONS | {e if e.startswith(".") else f".{e}" for e in args.ext}
    out_root = Path(args.out).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    if args.repos:
        repos = [tuple(r.strip().split("/", 1)) for r in args.repos.split(",") if r.strip()]
    else:
        repos = discover_repos(args.url, token, args.org, args.all)

    totals = {"fetched": 0, "unchanged": 0, "removed": 0, "errors": 0}
    for owner, repo in repos:
        print(f"{owner}/{repo}")
        try:
            stats = harvest_repo(args.url, token, owner, repo, out_root, exts, manifest, args.dry_run)
        except Exception as e:  # noqa: BLE001 - one bad repo must not abort the run
            print(f"  [error] {owner}/{repo}: {e}", file=sys.stderr)
            totals["errors"] += 1
            continue
        print(f"  fetched={stats['fetched']} unchanged={stats['unchanged']} "
              f"removed={stats['removed']} errors={stats['errors']}")
        for k in totals:
            totals[k] += stats[k]

    if not args.dry_run:
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    print(f"\ntotal: fetched={totals['fetched']} unchanged={totals['unchanged']} "
          f"removed={totals['removed']} errors={totals['errors']}")
    return 1 if totals["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
