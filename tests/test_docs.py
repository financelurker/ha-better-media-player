"""The README and the pages in docs/: image versions, links and anchors, YAML only where it belongs."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DOCS = ROOT / "docs"
PAGES = sorted(DOCS.glob("*.md"))
REPO_BLOB = "https://github.com/financelurker/ha-better-media-player/blob/main/"
REPO_RAW = "https://raw.githubusercontent.com/financelurker/ha-better-media-player/main/"
BADGES = (
    "https://img.shields.io/",
    "https://my.home-assistant.io/badges/",
    "https://github.com/financelurker/ha-better-media-player/actions/workflows/",
)
YAML_PAGES = {"automations.md", "dashboards.md"}

_spec = importlib.util.spec_from_file_location("readme_images", ROOT / "scripts" / "readme_images.py")
readme_images = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(readme_images)

FENCE_RE = re.compile(r"^```.*?^```", re.M | re.S)
LINK_RE = re.compile(r"\]\(([^)\s]+)\)|<(?:img|a) [^>]*(?:src|href)=\"([^\"]+)\"|<(https?://[^>\s]+)>|^\[[^\]]+\]: *(\S+)", re.M)
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)|<img [^>]*src=\"([^\"]+)\"")


def _prose(text: str) -> str:
    return FENCE_RE.sub("", text)


def anchors(file: Path) -> set[str]:
    """The anchors GitHub generates for the headings of a Markdown file."""
    seen: dict[str, int] = {}
    result = set()
    for line in _prose(file.read_text(encoding="utf-8")).splitlines():
        match = re.match(r"^#{1,6} +(.*?) *$", line)
        if not match:
            continue
        slug = re.sub(r"[^\w\- ]", "", match.group(1).lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug if count == 0 else f"{slug}-{count}")
    return result


def links(file: Path) -> list[str]:
    return [next(filter(None, groups)) for groups in LINK_RE.findall(_prose(file.read_text(encoding="utf-8")))]


def _check(file: Path, link: str) -> str | None:
    """Why a link of a file is broken, or None."""
    if link.startswith(REPO_RAW):
        target, anchor = ROOT / link[len(REPO_RAW):].split("?")[0], ""
    elif link.startswith(REPO_BLOB):
        path, _, anchor = link[len(REPO_BLOB):].partition("#")
        target = ROOT / path
    elif link.startswith(("http://", "https://", "mailto:")):
        return None
    else:
        path, _, anchor = link.partition("#")
        target = (file.parent / path).resolve() if path else file
    if not target.resolve().is_relative_to(ROOT):
        return f"{link}: outside the repository"
    if not target.exists():
        return f"{link}: {target.relative_to(ROOT)} does not exist"
    if anchor and anchor not in anchors(target):
        return f"{link}: no heading #{anchor} in {target.relative_to(ROOT)}"
    return None


def test_readme_image_urls_up_to_date():
    text = README.read_text(encoding="utf-8")
    updated, missing = readme_images.versioned(text)
    assert not missing
    assert updated == text, "run: python scripts/readme_images.py"


def test_readme_image_urls_are_stamped(tmp_path):
    (tmp_path / "docs" / "images").mkdir(parents=True)
    (tmp_path / "docs" / "images" / "a.png").write_bytes(b"one")
    text = f"![a]({REPO_RAW}docs/images/a.png?v=0000000000) ![b]({REPO_RAW}docs/images/b.png)"
    updated, missing = readme_images.versioned(text, tmp_path)
    assert missing == {"docs/images/b.png"}
    assert f"{REPO_RAW}docs/images/a.png?v=7692c3ad35" in updated  # sha256("one")[:10]
    (tmp_path / "docs" / "images" / "a.png").write_bytes(b"two")
    assert readme_images.versioned(updated, tmp_path)[0] != updated


def test_readme_images_versioned():
    """Every README image is a raw URL with a version marker (else a cache shows an old image)."""
    images = [a or b for a, b in IMAGE_RE.findall(README.read_text(encoding="utf-8"))]
    assert images
    unversioned = [
        url for url in images
        if not url.startswith(BADGES)
        and not re.fullmatch(re.escape(REPO_RAW) + r"docs/images/[\w.-]+\?v=[0-9a-f]{10}", url)
    ]
    assert unversioned == []


def test_readme_links_absolute():
    """HACS shows the README inside Home Assistant: relative links do not work there."""
    relative = [link for link in links(README) if not link.startswith(("https://", "http://"))]
    assert relative == []


@pytest.mark.parametrize("file", [README, *PAGES], ids=lambda f: f.name)
def test_links_resolve(file: Path):
    broken = [reason for link in links(file) if (reason := _check(file, link))]
    assert broken == []


@pytest.mark.parametrize("file", PAGES, ids=lambda f: f.name)
def test_page_structure(file: Path):
    text = file.read_text(encoding="utf-8")
    assert text.startswith("# ")
    assert "[← Back to the overview](../README.md)" in text
    assert ("```yaml" in text) == (file.name in YAML_PAGES), "YAML belongs on automations.md / dashboards.md"
    if file.name not in YAML_PAGES:
        assert "```" not in text, "code blocks belong on automations.md / dashboards.md"
    assert REPO_BLOB not in text, "pages link each other relatively"


def test_readme_without_code_blocks():
    assert "```" not in README.read_text(encoding="utf-8")


def test_every_page_in_readme():
    readme = README.read_text(encoding="utf-8")
    assert [p.name for p in PAGES if f"{REPO_BLOB}docs/{p.name})" not in readme] == []


def test_every_image_used():
    used = {Path(link.split("?")[0]).name for file in [README, *PAGES] for link in links(file)}
    assert sorted(p.name for p in (DOCS / "images").iterdir() if p.name not in used) == []


def test_anchor_slugs(tmp_path):
    page = tmp_path / "page.md"
    page.write_text("# Night mode, sleep timer and wake up\n## Is it for you?\n## A\n## A\n```yaml\n# not a heading\n```\n")
    assert anchors(page) == {"night-mode-sleep-timer-and-wake-up", "is-it-for-you", "a", "a-1"}
