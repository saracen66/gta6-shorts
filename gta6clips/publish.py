"""Upload rendered shorts via each platform's official API."""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

from .config import SECRETS, env
from .db import DB

log = logging.getLogger("gta6clips.publish")


class PublishError(RuntimeError):
    pass


def _title(row, limit: int) -> str:
    t = (row["post_title"] or "GTA 6 moment").strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------- YouTube
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
YT_CLIENT = SECRETS / "youtube_client_secret.json"
YT_TOKEN = SECRETS / "youtube_token.json"


def youtube_auth() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not YT_CLIENT.exists():
        raise SystemExit(f"Put your OAuth desktop client JSON at {YT_CLIENT}")
    flow = InstalledAppFlow.from_client_secrets_file(str(YT_CLIENT), YT_SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    YT_TOKEN.write_text(creds.to_json(), encoding="utf-8")
    print(f"YouTube token saved to {YT_TOKEN}")


def _yt_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    if not YT_TOKEN.exists():
        raise PublishError("YouTube not authorized: run `python -m gta6clips auth youtube`")
    creds = Credentials.from_authorized_user_file(str(YT_TOKEN), YT_SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        YT_TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def post_youtube(row, cfg: dict) -> str:
    from googleapiclient.http import MediaFileUpload
    yt = _yt_service()
    title = _title(row, 90)
    if "#shorts" not in title.lower():
        title = f"{title} #Shorts"[:100]
    tags = [t.lstrip("#") for t in (row["hashtags"] or "").split()] + ["GTA 6", "GTA VI", "Shorts"]
    body = {
        "snippet": {"title": title, "description": row["description"][:4900], "tags": tags[:15],
                    "categoryId": "20"},
        "status": {"privacyStatus": cfg["platforms"]["youtube"].get("privacy", "public"),
                   "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(row["file"], mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=3)
    return resp["id"]


# --------------------------------------------------------------------------- Meta (FB + IG)
def _graph(cfg: dict, platform: str) -> str:
    return f"https://graph.facebook.com/{cfg['platforms'][platform].get('graph_version', 'v24.0')}"


def _check(r: requests.Response) -> dict:
    try:
        js = r.json()
    except ValueError:
        js = {}
    if not r.ok or "error" in js:
        raise PublishError(f"{r.status_code}: {js.get('error') or r.text[:300]}")
    return js


def post_facebook(row, cfg: dict) -> str:
    page, token = env("FB_PAGE_ID"), env("FB_PAGE_TOKEN")
    if not page or not token:
        raise PublishError("FB_PAGE_ID / FB_PAGE_TOKEN missing")
    g = _graph(cfg, "facebook")
    js = _check(requests.post(f"{g}/{page}/video_reels", data={
        "upload_phase": "start", "access_token": token}, timeout=60))
    vid, upload_url = js["video_id"], js["upload_url"]
    data = Path(row["file"]).read_bytes()
    _check(requests.post(upload_url, data=data, timeout=600, headers={
        "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))}))
    desc = f"{_title(row, 90)}\n\n{row['description']}"
    _check(requests.post(f"{g}/{page}/video_reels", data={
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
        "description": desc[:2200], "access_token": token}, timeout=60))
    return vid


def post_instagram(row, cfg: dict) -> str:
    ig, token = env("IG_USER_ID"), env("FB_PAGE_TOKEN")
    if not ig or not token:
        raise PublishError("IG_USER_ID / FB_PAGE_TOKEN missing")
    g = _graph(cfg, "instagram")
    caption = f"{_title(row, 90)}\n\n{row['description']}"[:2200]
    js = _check(requests.post(f"{g}/{ig}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true", "access_token": token}, timeout=60))
    container = js["id"]
    upload_uri = js.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{cfg['platforms']['instagram'].get('graph_version', 'v24.0')}/{container}"
    data = Path(row["file"]).read_bytes()
    _check(requests.post(upload_uri, data=data, timeout=600, headers={
        "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))}))
    for _ in range(60):  # wait for IG to process (up to ~10 min)
        st = _check(requests.get(f"{g}/{container}", params={
            "fields": "status_code,status", "access_token": token}, timeout=30))
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise PublishError(f"IG processing failed: {st}")
        time.sleep(10)
    else:
        raise PublishError("IG processing timed out")
    pub = _check(requests.post(f"{g}/{ig}/media_publish", data={
        "creation_id": container, "access_token": token}, timeout=60))
    return pub["id"]


# --------------------------------------------------------------------------- TikTok
TT_TOKEN = SECRETS / "tiktok_token.json"
TT_API = "https://open.tiktokapis.com/v2"


def tiktok_auth() -> None:
    """Desktop OAuth with PKCE on a localhost redirect registered in your TikTok app."""
    key, redirect = env("TIKTOK_CLIENT_KEY"), env("TIKTOK_REDIRECT_URI", "http://localhost:8765/callback")
    verifier = secrets.token_urlsafe(64)[:96]
    # TikTok desktop docs specify hex SHA256; set TIKTOK_PKCE=base64url if your app rejects it.
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = (base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
                 if os.environ.get("TIKTOK_PKCE") == "base64url" else digest.hex())
    state = secrets.token_urlsafe(16)
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": key, "scope": "user.info.basic,video.publish,video.upload",
        "response_type": "code", "redirect_uri": redirect, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200); self.end_headers()
            self.wfile.write(b"TikTok authorized. You can close this tab.")

        def log_message(self, *a):
            pass

    port = urllib.parse.urlparse(redirect).port or 8765
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    print("Opening browser for TikTok login:\n", url)
    webbrowser.open(url)
    for _ in range(300):
        if got:
            break
        time.sleep(1)
    srv.server_close()
    if got.get("state") != state or "code" not in got:
        raise SystemExit(f"TikTok auth failed: {got}")
    r = requests.post(f"{TT_API}/oauth/token/", data={
        "client_key": key, "client_secret": env("TIKTOK_CLIENT_SECRET"), "code": got["code"],
        "grant_type": "authorization_code", "redirect_uri": redirect, "code_verifier": verifier},
        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    js = r.json()
    if "access_token" not in js:
        raise SystemExit(f"TikTok token exchange failed: {js}")
    js["obtained_at"] = time.time()
    TT_TOKEN.write_text(json.dumps(js), encoding="utf-8")
    print(f"TikTok token saved to {TT_TOKEN}")


def _tt_token() -> str:
    if not TT_TOKEN.exists():
        raise PublishError("TikTok not authorized: run `python -m gta6clips auth tiktok`")
    tok = json.loads(TT_TOKEN.read_text(encoding="utf-8"))
    if time.time() > tok["obtained_at"] + tok.get("expires_in", 86400) - 300:
        r = requests.post(f"{TT_API}/oauth/token/", data={
            "client_key": env("TIKTOK_CLIENT_KEY"), "client_secret": env("TIKTOK_CLIENT_SECRET"),
            "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
            headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        new = r.json()
        if "access_token" not in new:
            raise PublishError(f"TikTok refresh failed (re-run auth tiktok): {new}")
        new["obtained_at"] = time.time()
        TT_TOKEN.write_text(json.dumps(new), encoding="utf-8")
        tok = new
    return tok["access_token"]


def _tt(path: str, token: str, body: dict) -> dict:
    r = requests.post(f"{TT_API}{path}", json=body, timeout=60, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    js = r.json()
    if js.get("error", {}).get("code", "ok") != "ok":
        raise PublishError(f"TikTok {path}: {js['error']}")
    return js.get("data", {})


def post_tiktok(row, cfg: dict) -> str:
    tc = cfg["platforms"]["tiktok"]
    token = _tt_token()
    size = Path(row["file"]).stat().st_size
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}
    if tc.get("mode") == "inbox":
        data = _tt("/post/publish/inbox/video/init/", token, {"source_info": source})
    else:
        info = _tt("/post/publish/creator_info/query/", token, {})
        options = info.get("privacy_level_options") or ["SELF_ONLY"]
        privacy = tc.get("privacy", "PUBLIC_TO_EVERYONE")
        if privacy not in options:
            log.warning("TikTok privacy %s not allowed (options %s); using %s", privacy, options, options[-1])
            privacy = "SELF_ONLY" if "SELF_ONLY" in options else options[0]
        title = f"{_title(row, 90)}\n\n{row['description']}"[:2200]
        data = _tt("/post/publish/video/init/", token, {
            "post_info": {"title": title, "privacy_level": privacy, "disable_duet": False,
                          "disable_comment": False, "disable_stitch": False,
                          "video_cover_timestamp_ms": 1000},
            "source_info": source})
    with open(row["file"], "rb") as f:
        r = requests.put(data["upload_url"], data=f, timeout=600, headers={
            "Content-Type": "video/mp4", "Content-Length": str(size),
            "Content-Range": f"bytes 0-{size - 1}/{size}"})
    if r.status_code not in (200, 201, 206):
        raise PublishError(f"TikTok upload HTTP {r.status_code}: {r.text[:300]}")
    pid = data["publish_id"]
    for _ in range(40):
        st = _tt("/post/publish/status/fetch/", token, {"publish_id": pid})
        status = st.get("status")
        if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
            return str(ids[0]) if ids else pid
        if status == "FAILED":
            raise PublishError(f"TikTok publish failed: {st.get('fail_reason')}")
        time.sleep(8)
    return pid  # still processing; TikTok finishes it server-side


# --------------------------------------------------------------------------- scheduler
POSTERS = {"youtube": post_youtube, "facebook": post_facebook, "instagram": post_instagram,
           "tiktok": post_tiktok}


def due(db: DB, platform: str, pc: dict, now: float | None = None) -> bool:
    now = now or time.time()
    if db.posts_since(platform, now - 86400) >= pc.get("max_per_day", 5):
        return False
    return now - db.last_post_time(platform) >= pc.get("min_gap_minutes", 60) * 60


def publish_due(cfg: dict, db: DB) -> dict[str, str]:
    """Post at most one clip per platform per call, respecting daily caps and spacing."""
    results = {}
    for platform, pc in cfg["platforms"].items():
        if not pc.get("enabled") or not due(db, platform, pc):
            continue
        for row in db.queued_posts(platform):
            if not Path(row["file"]).exists():
                db.mark_post(row["clip_id"], platform, "failed", error="file missing")
                continue
            try:
                rid = POSTERS[platform](row, cfg)
                db.mark_post(row["clip_id"], platform, "posted", remote_id=rid)
                results[platform] = f"posted {row['clip_id']} -> {rid}"
                log.info("%s: posted %s -> %s", platform, row["clip_id"], rid)
            except Exception as e:
                db.requeue_failed_attempt(row["clip_id"], platform, str(e)[:500])
                results[platform] = f"error: {e}"
                log.error("%s: post %s failed: %s", platform, row["clip_id"], e)
            break  # one per platform per call
    return results
