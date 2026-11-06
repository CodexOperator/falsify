"""Render catalog/falsify.yaml for submission: python catalog/fill.py <repo name> <40-hex sha> [out]
The entry lives on the owner's GitHub account (CodexOperator); the committed file keeps {REPO_NAME} and {SHA} as
placeholders, filled at submission with the repo name and the pinned commit. This writes the filled copy (default: stdout) and
refuses a value the catalog validator would reject."""
import os
import re
import sys


def fill(text, repo_name, sha):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", repo_name):
        raise SystemExit(f"repo name must be a GitHub repository name, got {repo_name!r}")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit(f"sha must be exactly 40 lowercase hex characters, got {sha!r}")
    # the rendered copy is public: drop the template's own comments (the template note, the placeholder marks)
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#")) + "\n"
    text = re.sub(r'(sha: "\{SHA\}")\s+#.*', r"\1", text)
    out = text.replace("{REPO_NAME}", repo_name).replace("{SHA}", sha)
    left = re.findall(r"\{[A-Z_]+\}", out)
    if left:
        raise SystemExit(f"unfilled placeholders: {left}")
    return out


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit(__doc__)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "falsify.yaml")) as f:
        out = fill(f.read(), *sys.argv[1:3])
    if len(sys.argv) == 4:
        with open(sys.argv[3], "w") as f:
            f.write(out)
    else:
        sys.stdout.write(out)
