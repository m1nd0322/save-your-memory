from __future__ import annotations

import hashlib
import re
from pathlib import Path


def stable_wiki_path(relative_path: str) -> str:
    stem = Path(relative_path).stem
    slug = re.sub(r"[^\w.-]+", "-", stem, flags=re.UNICODE).strip("-._")
    slug = (slug or "file")[:60]
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:12]
    return f"wiki/sources/{slug}-{digest}.md"
