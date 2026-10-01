"""What version each lab is at: its VERSION file, the newest CHANGELOG.md entry (Keep a Changelog: `## [x.y.z] — date`),
and its git state — the commit it is on, how far past its last tag, how many files are uncommitted, and how it stands
against GitHub as of the last fetch (the hub never fetches by itself). Cached for a minute."""
import re, subprocess, time
from pathlib import Path

_cache = {}
HEAD = re.compile(r"^## \[([^\]]+)\]\s*[—-]?\s*(\S*)")


def _git(d, *args):
    try:
        r = subprocess.run(["git", "-C", d, *args], capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:                                          # noqa: BLE001
        return None


def _changelog(d):
    p = Path(d) / "CHANGELOG.md"
    if not p.exists(): return None
    lines = p.read_text().splitlines(); start = next((i for i, l in enumerate(lines) if HEAD.match(l)), None)
    if start is None: return None
    m = HEAD.match(lines[start]); body = []
    for l in lines[start + 1:]:
        if HEAD.match(l): break
        body.append(l)
    items = [re.sub(r"\*\*(.+?)\*\*.*", r"\1", l[2:]).replace("`", "").rstrip(".") for l in body if l.startswith("- **")]
    text = "\n".join(l for l in body if l.strip()).strip()
    return {"version": m[1], "date": m[2] or None, "items": items[:5], "text": text[:900] + ("…" if len(text) > 900 else "")}


def lab_version(lab):
    d = lab["dir"]; hit = _cache.get(d)
    if hit and time.time() - hit[0] < 60: return hit[1]
    vf = Path(d) / "VERSION"
    version = vf.read_text().strip() if vf.exists() else None
    head = _git(d, "log", "-1", "--format=%h%x09%cI%x09%s") or ""
    sha, when, subject = (head.split("\t") + ["", "", ""])[:3]
    describe = _git(d, "describe", "--tags", "--long", "--abbrev=7") or ""
    m = re.match(r"^(.*)-(\d+)-g[0-9a-f]+$", describe)
    tag, since = (m[1], int(m[2])) if m else (None, None)
    dirty = _git(d, "status", "--porcelain")
    ab = _git(d, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    ahead, behind = (int(x) for x in ab.split()) if ab and len(ab.split()) == 2 else (None, None)
    fetched = None
    fh = Path(d) / ".git" / "FETCH_HEAD"
    if fh.exists(): fetched = fh.stat().st_mtime
    repo = lab.get("repo", "").rstrip("/")
    cl = _changelog(d)
    out = {"version": version, "changelog": cl, "tag": tag, "since_tag": since, "branch": _git(d, "rev-parse", "--abbrev-ref", "HEAD"),
           "commit": sha, "commit_date": when, "commit_subject": subject[:120], "uncommitted": len(dirty.splitlines()) if dirty else 0,
           "ahead": ahead, "behind": behind, "fetched": fetched,
           "tagged": bool(version and tag in (version, f"v{version}")) if version else None,
           "links": {"changelog": f"{repo}/blob/main/CHANGELOG.md" if repo and cl else None,
                     "tag": f"{repo}/releases/tag/{tag}" if repo and tag else None, "commit": f"{repo}/commit/{sha}" if repo and sha else None}}
    _cache[d] = (time.time(), out)
    return out
