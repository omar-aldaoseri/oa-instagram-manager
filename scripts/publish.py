#!/usr/bin/env python3
"""OA Instagram manager: publishes only posts Omar approved in chat.

Runs inside GitHub Actions. For every file in queue/*.json it checks:
  1. status is "approved" (Claude sets this only after Omar says yes in chat)
  2. publish_at has arrived, and is less than MAX_LATE_HOURS old
  3. the caption and images still match the fingerprint taken at approval
Then it publishes through the official Instagram API (Instagram Login,
graph.instagram.com) and records the result back into the queue file.

Safety rules built in:
  - A file named PAUSE in the repo root stops every post.
  - A post is locked as "publishing" (and that lock is pushed) BEFORE the API
    call, so a crash can never cause the same post to go out twice. A post found
    stuck in "publishing" is marked "needs_review" and never retried on its own.
  - Posts that are too late are marked "missed", not posted at a random time.

Only the Python standard library is used.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://graph.instagram.com/v26.0"
ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "queue"
MAX_LATE_HOURS = 6
POLL_SECONDS = 10
POLL_TRIES = 30  # up to 5 minutes, per Meta's guidance


# ---------------------------------------------------------------- helpers
def log(msg):
    print(msg, flush=True)


def now_utc():
    return datetime.now(timezone.utc)


def fingerprint(item, root=ROOT):
    h = hashlib.sha256()
    h.update(item["caption"].encode("utf-8"))
    for rel in item["images"]:
        h.update(b"\x00" + rel.encode("utf-8") + b"\x00")
        h.update((root / rel).read_bytes())
    return h.hexdigest()


def is_due(item, now):
    at = datetime.fromisoformat(item["publish_at"])
    if at.tzinfo is None:
        raise ValueError("publish_at must include a timezone offset, e.g. +03:00")
    if now < at:
        return "wait"
    if now - at > timedelta(hours=MAX_LATE_HOURS):
        return "missed"
    return "due"


def image_url(rel):
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{urllib.parse.quote(rel)}"


class Api:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, params=None):
        params = dict(params or {})
        url = f"{API}/{path}"
        data = None
        if method == "GET":
            params["access_token"] = self.token
            url += "?" + urllib.parse.urlencode(params)
        else:
            params["access_token"] = self.token
            data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"Instagram API {e.code} on {path}: {body}") from None

    def wait_ready(self, container_id):
        for _ in range(POLL_TRIES):
            st = self.call("GET", container_id, {"fields": "status_code"}).get("status_code")
            if st == "FINISHED":
                return
            if st in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"container {container_id} status {st}")
            time.sleep(POLL_SECONDS)
        raise RuntimeError(f"container {container_id} not ready after waiting")


# ---------------------------------------------------------------- git
def git(*args):
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def save_and_push(path, item, message):
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    git("add", str(path.relative_to(ROOT)))
    git("commit", "-m", message)
    for attempt in range(3):
        try:
            git("push")
            return
        except subprocess.CalledProcessError:
            git("pull", "--rebase")
    raise RuntimeError("could not push queue update")


# ---------------------------------------------------------------- publish
def publish(api, ig_id, item):
    urls = [image_url(r) for r in item["images"]]
    if item["type"] == "IMAGE":
        cid = api.call("POST", f"{ig_id}/media", {"image_url": urls[0], "caption": item["caption"]})["id"]
    elif item["type"] == "CAROUSEL":
        if not 2 <= len(urls) <= 10:
            raise ValueError("a carousel needs 2 to 10 images")
        kids = []
        for u in urls:
            kid = api.call("POST", f"{ig_id}/media", {"image_url": u, "is_carousel_item": "true"})["id"]
            api.wait_ready(kid)
            kids.append(kid)
        cid = api.call("POST", f"{ig_id}/media", {
            "media_type": "CAROUSEL", "children": ",".join(kids), "caption": item["caption"]})["id"]
    else:
        raise ValueError(f"unknown type {item['type']}")
    api.wait_ready(cid)
    return api.call("POST", f"{ig_id}/media_publish", {"creation_id": cid})["id"]


def main():
    dry = os.environ.get("DRY_RUN", "false").lower() == "true"
    if (ROOT / "PAUSE").exists():
        log("PAUSE file present: nothing will be posted.")
        return 0
    token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    if not token:
        log("IG_ACCESS_TOKEN secret is missing.")
        return 1
    api = Api(token)
    ig_id = os.environ.get("IG_USER_ID", "").strip() or str(api.call("GET", "me", {"fields": "user_id"})["user_id"])

    if dry:
        me = api.call("GET", "me", {"fields": "user_id,username"})
        lim = api.call("GET", f"{ig_id}/content_publishing_limit", {"fields": "quota_usage,config"})
        log(f"Token OK for @{me.get('username')}. Publishing limit: {json.dumps(lim)}")

    now = now_utc()
    failures = 0
    for path in sorted(QUEUE.glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        name = item.get("id", path.stem)
        st = item.get("status")
        if st == "publishing":
            item["status"] = "needs_review"
            item["note"] = "Found locked mid-publish. Check Instagram before retrying."
            save_and_push(path, item, f"needs review: {name}")
            log(f"{name}: was mid-publish, marked needs_review (not retried).")
            continue
        if st != "approved":
            continue
        when = is_due(item, now)
        if when == "wait":
            continue
        if when == "missed":
            item["status"] = "missed"
            save_and_push(path, item, f"missed: {name}")
            log(f"{name}: more than {MAX_LATE_HOURS}h late, marked missed (not posted).")
            continue
        if fingerprint(item) != item.get("fingerprint"):
            item["status"] = "blocked_changed"
            item["note"] = "Caption or images changed after approval. Re-approve in chat."
            save_and_push(path, item, f"blocked, changed after approval: {name}")
            log(f"{name}: content changed after approval, blocked.")
            continue
        if dry:
            for u in [image_url(r) for r in item["images"]]:
                with urllib.request.urlopen(urllib.request.Request(u, method="HEAD"), timeout=30) as r:
                    log(f"  {u} -> {r.status} {r.headers.get('Content-Type')}")
            log(f"{name}: due now, DRY RUN so not posted.")
            continue
        item["status"] = "publishing"
        item["publishing_started"] = now.isoformat()
        save_and_push(path, item, f"publishing: {name}")
        try:
            media_id = publish(api, ig_id, item)
            item["status"] = "posted"
            item["ig_media_id"] = media_id
            item["posted_at"] = now_utc().isoformat()
            save_and_push(path, item, f"posted: {name}")
            log(f"{name}: posted, media id {media_id}.")
        except Exception as e:  # keep going with the rest of the queue
            item["status"] = "failed"
            item["note"] = str(e)[:500]
            save_and_push(path, item, f"failed: {name}")
            log(f"{name}: FAILED: {e}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
