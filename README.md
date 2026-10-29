# OA Instagram Manager

Posts to Omar's OA Instagram account through the official Instagram API, and **only posts Omar has approved in chat**.

## How a post goes out

1. Posts are drafted in the OA content calendar. Nothing there posts by itself.
2. Omar approves specific posts in chat with Claude.
3. Claude adds each approved post to `queue/` (caption, JPEG images in `media/`, time, and a fingerprint of the content).
4. Every 15 minutes, GitHub Actions runs `scripts/publish.py`, which posts whatever is due and writes the result back into the queue file.

## Safety

| Lock | What it does |
|---|---|
| Approval only | Only queue files with `"status": "approved"` can post. |
| Fingerprint | If the caption or any image changes after approval, the post is blocked as `blocked_changed`. |
| Lateness | A post more than 6 hours late is marked `missed` instead of going out at a random time. |
| No double posts | A post is locked as `publishing` before the API call. If a run stops halfway, it becomes `needs_review` and is never retried on its own. |
| Pause | Add a file named `PAUSE` to the repo root and nothing posts until it is removed. |

## Queue file statuses

`approved` → `publishing` → `posted` (with `ig_media_id`). Other outcomes: `failed`, `missed`, `blocked_changed`, `needs_review`.

## Secrets (Settings → Secrets and variables → Actions)

- `IG_ACCESS_TOKEN`: long-lived access token. Either an Instagram Login token (starts with `IG`) or a Facebook Login token (starts with `EAA`, needs `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `business_management`). The script picks the right API host from the token.
- `IG_USER_ID` (optional): the Instagram professional account ID. If empty, it is looked up from the token (for Facebook Login, from the one Facebook Page linked to Instagram).

The **Token health check** workflow runs every Monday and fails, with a GitHub email, if the token stops working. Long-lived tokens expire, so renew it when that happens.

## Test without posting

Actions → **Publish approved posts** → Run workflow → keep **Dry run** ticked. It checks the token, the publishing quota and every queued image link, and posts nothing.
