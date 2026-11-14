"""Fail if docs/index.html or README.md reference a local file that does not exist."""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("check_site")

ROOT = Path(__file__).resolve().parent.parent


def refs(text: str) -> set[str]:
    found = set(re.findall(r'(?:src|href|poster)="([^"#?]+)"', text))
    found |= set(re.findall(r"\]\(([^)#?\s]+)\)", text))
    found |= set(re.findall(r'"(media/[^"]+)"', text))  # gallery list in the page script
    return {r for r in found if not re.match(r"^(https?:|mailto:|data:)", r) and "$" not in r}


def main() -> int:
    bad = []
    for doc, base in (("docs/index.html", ROOT / "docs"), ("README.md", ROOT)):
        for r in refs((ROOT / doc).read_text(encoding="utf-8")):
            if not (base / r).exists():
                bad.append(f"{doc}: {r}")
    for b in sorted(bad):
        log.error("missing %s", b)
    log.info("%s", "OK" if not bad else f"{len(bad)} missing")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
