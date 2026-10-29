#!/usr/bin/env python3
"""Add one approved post to the queue.

Used by Claude only after Omar approves the post in chat.

  python3 scripts/approve.py --id 2026-10-30 --at 2026-10-30T19:00:00+03:00 \
      --caption-file caption.txt --images media/2026-10-30/01.jpg [more.jpg ...]

Type is IMAGE for one image and CAROUSEL for two or more.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from publish import ROOT, QUEUE, fingerprint


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--at", required=True, help="ISO time with offset, e.g. 2026-10-30T19:00:00+03:00")
    ap.add_argument("--caption-file", required=True)
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--approved-by", default="Omar, in chat")
    a = ap.parse_args()

    at = datetime.fromisoformat(a.at)
    if at.tzinfo is None:
        raise SystemExit("--at needs a timezone offset")
    for rel in a.images:
        p = ROOT / rel
        if not p.exists() or p.suffix.lower() not in (".jpg", ".jpeg"):
            raise SystemExit(f"image missing or not JPEG: {rel}")
    if len(a.images) > 10:
        raise SystemExit("max 10 images per carousel")

    item = {
        "id": a.id,
        "publish_at": at.isoformat(),
        "type": "IMAGE" if len(a.images) == 1 else "CAROUSEL",
        "images": a.images,
        "caption": Path(a.caption_file).read_text(encoding="utf-8").strip(),
        "status": "approved",
        "approved_by": a.approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    item["fingerprint"] = fingerprint(item)
    QUEUE.mkdir(exist_ok=True)
    out = QUEUE / f"{a.id}.json"
    out.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"queued {out.relative_to(ROOT)} for {item['publish_at']} ({item['type']}, {len(a.images)} image(s))")


if __name__ == "__main__":
    main()
