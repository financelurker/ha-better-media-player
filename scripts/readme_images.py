"""Keep the image URLs in README.md cache-proof.

README images are linked with absolute raw.githubusercontent.com URLs (HACS renders
the README inside Home Assistant, where relative paths do not work). Those URLs are
cached by browsers, GitHub and HACS, so a changed image would keep showing the old
version. Each URL therefore carries `?v=<hash of the file>`: when an image changes,
its URL changes too.

    python scripts/readme_images.py          update README.md
    python scripts/readme_images.py --check  fail if README.md is not up to date
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
URL_RE = re.compile(
    r"(https://raw\.githubusercontent\.com/financelurker/ha-better-media-player/main/)"
    r"(docs/images/[\w.-]+\.(?:png|jpe?g|gif|svg|webp))(\?v=[0-9a-f]+)?"
)


def versioned(text: str, root: Path = ROOT) -> tuple[str, set[str]]:
    """The text with every image URL carrying the hash of its file, and missing files."""
    missing: set[str] = set()

    def stamp(match: re.Match) -> str:
        base, path = match.group(1), match.group(2)
        file = root / path
        if not file.is_file():
            missing.add(path)
            return base + path
        digest = hashlib.sha256(file.read_bytes()).hexdigest()[:10]
        return f"{base}{path}?v={digest}"

    return URL_RE.sub(stamp, text), missing


def main(argv: list[str]) -> int:
    text = README.read_text(encoding="utf-8")
    updated, missing = versioned(text)
    if missing:
        print(f"README.md links images that do not exist: {', '.join(sorted(missing))}", file=sys.stderr)
        return 1
    if "--check" in argv:
        if updated != text:
            print("README.md image URLs are out of date - run: python scripts/readme_images.py", file=sys.stderr)
            return 1
        print("README.md image URLs are up to date.")
        return 0
    README.write_text(updated, encoding="utf-8")
    print("README.md image URLs updated." if updated != text else "README.md image URLs were up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
