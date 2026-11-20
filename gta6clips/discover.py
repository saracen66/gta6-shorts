"""Find candidate moments on Twitch, Kick and YouTube."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import requests

from . import media
from .config import env
from .db import DB, Candidate
from .filters import language_ok, streamer_allowed
from .signals import heatmap_windows

log = logging.getLogger("gta6clips.discover")
UA = {"User-Agent": "gta6clips/1.0"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# --------------------------------------------------------------------------- Twitch
class Twitch:
    API = "https://api.twitch.tv/helix"

    def __init__(self):
        self.cid, self.secret = env("TWITCH_CLIENT_ID"), env("TWITCH_CLIENT_SECRET")
        self._token = None

    @property
    def ok(self) -> bool:
        return bool(self.cid and self.secret)

    def _headers(self) -> dict:
        if not self._token:
            r = requests.post("https://id.twitch.tv/oauth2/token", data={
                "client_id": self.cid, "client_secret": self.secret,
                "grant_type": "client_credentials"}, timeout=30)
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Client-Id": self.cid, "Authorization": f"Bearer {self._token}", **UA}

    def get(self, path: str, **params) -> dict:
        r = requests.get(f"{self.API}/{path}", headers=self._headers(), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def game_ids(self, names: list[str]) -> list[str]:
        data = self.get("games", name=names)["data"]
        return [g["id"] for g in data]

    def top_clips(self, game_id: str, hours: int, limit: int) -> list[dict]:
        now = datetime.now(timezone.utc)
        out, cursor = [], None
        while len(out) < limit:
            params = {"game_id": game_id, "started_at": _iso(now - timedelta(hours=hours)),
                      "ended_at": _iso(now), "first": 100}
            if cursor:
                params["after"] = cursor
            js = self.get("clips", **params)
            out += js["data"]
            cursor = js.get("pagination", {}).get("cursor")
            if not cursor or not js["data"]:
                break
        return out[:limit * 3]  # caller filters; Twitch returns by views desc

    def live_streams(self, game_id: str, n: int) -> list[dict]:
        return self.get("streams", game_id=game_id, first=min(n, 100), type="live")["data"]


def discover_twitch_clips(cfg: dict, db: DB) -> int:
    sc = cfg["sources"]["twitch_clips"]
    tw = Twitch()
    if not sc.get("enabled") or not tw.ok:
        return 0
    added = 0
    for gid in tw.game_ids(cfg["game"]["names"]):
        for c in tw.top_clips(gid, sc["lookback_hours"], sc["max_per_cycle"]):
            if added >= sc["max_per_cycle"]:
                break
            if c["view_count"] < sc["min_views"] or not language_ok(cfg, c.get("language")):
                continue
            login = c["broadcaster_name"]
            if not streamer_allowed(cfg, "twitch", login):
                continue
            vod = c.get("video_id")
            source_url = (f"https://www.twitch.tv/videos/{vod}" if vod
                          else f"https://www.twitch.tv/{login.lower()}")
            cand = Candidate(
                id=f"twitch_clip:{c['id']}", source="twitch_clip", platform="twitch",
                url=c["url"], source_url=source_url, streamer=login,
                streamer_url=f"https://www.twitch.tv/{login.lower()}", title=c["title"],
                moment_ts=_ts(c["created_at"]) - 30,
                signal=min(c["view_count"] / 1000.0, 10.0) + 1.0,
                meta={"views": c["view_count"], "clip_title": c["title"],
                      "duration": c.get("duration"), "clipper": c.get("creator_name")},
            )
            added += db.add_candidate(cand)
    log.info("twitch clips: %d new candidates", added)
    return added


# --------------------------------------------------------------------------- Kick
class Kick:
    API = "https://api.kick.com/public"

    def __init__(self):
        self.cid, self.secret = env("KICK_CLIENT_ID"), env("KICK_CLIENT_SECRET")
        self._token = None

    @property
    def ok(self) -> bool:
        return bool(self.cid and self.secret)

    def _headers(self) -> dict:
        if not self._token:
            r = requests.post("https://id.kick.com/oauth/token", data={
                "client_id": self.cid, "client_secret": self.secret,
                "grant_type": "client_credentials"}, timeout=30)
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json", **UA}

    def category_id(self, names: list[str]) -> int | None:
        for version, key in (("v2", "name"), ("v1", "q")):
            for name in names:
                try:
                    r = requests.get(f"{self.API}/{version}/categories", headers=self._headers(),
                                     params={key: name}, timeout=30)
                    if r.ok:
                        for c in r.json().get("data") or []:
                            if c.get("name", "").lower() == name.lower():
                                return int(c["id"])
                except requests.RequestException as e:
                    log.debug("kick category lookup %s failed: %s", version, e)
        return None

    def live_streams(self, category_id: int, languages: list[str]) -> list[dict]:
        params: dict[str, Any] = {"category_id": [category_id], "limit": 100}
        if languages:
            params["language_code"] = languages
        r = requests.get(f"{self.API}/v2/livestreams", headers=self._headers(), params=params,
                         timeout=30)
        r.raise_for_status()
        data = r.json().get("data") or []
        return sorted(data, key=lambda s: s.get("viewer_count", 0), reverse=True)

    @staticmethod
    def channel_clips(slug: str) -> list[dict]:
        """Unofficial endpoint (may be blocked by Cloudflare). Returns [] on failure."""
        try:
            r = requests.get(f"https://kick.com/api/v2/channels/{slug}/clips",
                             params={"sort": "view", "time": "day"},
                             headers={**UA, "Accept": "application/json"}, timeout=30)
            if not r.ok:
                return []
            js = r.json()
            return js.get("clips") or js.get("data") or []
        except (requests.RequestException, ValueError):
            return []


def discover_kick(cfg: dict, db: DB) -> int:
    sc = cfg["sources"]["kick"]
    k = Kick()
    if not sc.get("enabled") or not k.ok:
        return 0
    cat = sc.get("category_id") or k.category_id(cfg["game"]["names"])
    if not cat:
        log.warning("kick: GTA VI category id not found; set sources.kick.category_id")
        return 0
    added = 0
    for s in k.live_streams(cat, cfg["game"].get("languages") or [])[: sc["top_channels"]]:
        slug = (s.get("channel") or {}).get("slug") or s.get("slug")
        if not slug or not streamer_allowed(cfg, "kick", slug):
            continue
        for c in k.channel_clips(slug)[: sc["clips_per_channel"]]:
            clip_id = c.get("id")
            url = c.get("clip_url") or c.get("video_url")
            if not clip_id or not url:
                continue
            cat_name = ((c.get("category") or {}).get("name") or "").lower()
            if cat_name and "grand theft auto" not in cat_name and "gta" not in cat_name:
                continue
            created = c.get("created_at")
            cand = Candidate(
                id=f"kick_clip:{clip_id}", source="kick_clip", platform="kick",
                url=f"https://kick.com/{slug}/clips/{clip_id}" if not url.endswith(".m3u8") else url,
                source_url=f"https://kick.com/{slug}", streamer=slug,
                streamer_url=f"https://kick.com/{slug}", title=c.get("title", ""),
                moment_ts=_ts(created) - 30 if created else None,
                signal=min((c.get("views") or c.get("view_count") or 0) / 500.0, 10.0) + 1.0,
                meta={"views": c.get("views") or c.get("view_count"), "clip_title": c.get("title")},
            )
            added += db.add_candidate(cand)
    log.info("kick: %d new candidates", added)
    return added


# --------------------------------------------------------------------------- YouTube
def youtube_search(cfg: dict) -> list[dict]:
    key = env("YOUTUBE_API_KEY")
    sc = cfg["sources"]["youtube"]
    if not key:
        return []
    after = _iso(datetime.now(timezone.utc) - timedelta(hours=sc["lookback_hours"]))
    q = "|".join(f'"{k}"' for k in cfg["game"]["keywords"])
    params = {"part": "snippet", "q": q, "type": "video", "videoCategoryId": "20",
              "order": "viewCount", "publishedAfter": after, "maxResults": min(sc["max_videos"] * 2, 50),
              "eventType": "completed", "key": key}
    langs = cfg["game"].get("languages") or []
    if langs:
        params["relevanceLanguage"] = langs[0]
    r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def discover_youtube(cfg: dict, db: DB) -> int:
    sc = cfg["sources"]["youtube"]
    if not sc.get("enabled"):
        return 0
    added = scanned = 0
    for item in youtube_search(cfg):
        if scanned >= sc["max_videos"]:
            break
        vid = item["id"]["videoId"]
        sn = item["snippet"]
        channel, channel_id = sn["channelTitle"], sn["channelId"]
        if db.seen(f"yt:{vid}") or not streamer_allowed(cfg, "youtube", channel):
            continue
        scanned += 1
        try:
            meta = media.info(f"https://www.youtube.com/watch?v={vid}")
        except RuntimeError as e:
            log.warning("yt info %s failed: %s", vid, e)
            continue
        handle = meta.get("uploader_id") or ""
        if handle and not streamer_allowed(cfg, "youtube", handle):
            continue
        windows = heatmap_windows(meta.get("heatmap") or [], sc["min_heat"], sc["windows_per_video"],
                                  meta.get("duration") or 0)
        if not windows:
            log.info("yt %s: no most-replayed data yet, retry later", vid)
            continue  # not marked seen: heatmap appears once views accumulate
        db.mark_seen(f"yt:{vid}")
        ts0 = meta.get("release_timestamp") or meta.get("timestamp")
        for start, end, heat in windows:
            cand = Candidate(
                id=f"youtube_vod:{vid}:{int(start)}", source="youtube_vod", platform="youtube",
                url=f"https://www.youtube.com/watch?v={vid}",
                source_url=f"https://www.youtube.com/watch?v={vid}&t={int(start)}s",
                streamer=channel,
                streamer_url=(f"https://www.youtube.com/{handle}" if handle.startswith("@")
                              else f"https://www.youtube.com/channel/{channel_id}"),
                title=sn["title"], start=start, end=end,
                moment_ts=(ts0 + start) if ts0 else None,
                signal=heat * 10, meta={"heat": heat, "vod_title": sn["title"]},
            )
            added += db.add_candidate(cand)
    log.info("youtube: %d new candidates from %d videos", added, scanned)
    return added


# --------------------------------------------------------------------------- live targets
def live_targets(cfg: dict) -> Iterable[tuple[str, str, str]]:
    """(platform, streamer, url) of live GTA VI streams for live recording."""
    lc = cfg["sources"]["live_record"]
    for url in lc.get("channels") or []:
        plat = "twitch" if "twitch.tv" in url else "kick" if "kick.com" in url else "youtube"
        yield plat, url.rstrip("/").rsplit("/", 1)[-1], url
    if lc.get("channels"):
        return
    n = lc.get("top_n", 3)
    if "twitch" in lc["platforms"]:
        tw = Twitch()
        if tw.ok:
            for gid in tw.game_ids(cfg["game"]["names"]):
                for s in tw.live_streams(gid, n * 3):
                    if streamer_allowed(cfg, "twitch", s["user_login"]) and language_ok(cfg, s.get("language")):
                        yield "twitch", s["user_login"], f"https://www.twitch.tv/{s['user_login']}"
                        n -= 1
                        if n <= 0:
                            return
    if "kick" in lc["platforms"]:
        k = Kick()
        cat = cfg["sources"]["kick"].get("category_id") or (k.category_id(cfg["game"]["names"]) if k.ok else None)
        if cat:
            for s in k.live_streams(cat, cfg["game"].get("languages") or [])[:n]:
                slug = (s.get("channel") or {}).get("slug")
                if slug and streamer_allowed(cfg, "kick", slug):
                    yield "kick", slug, f"https://kick.com/{slug}"


def discover_all(cfg: dict, db: DB) -> int:
    total = 0
    for fn in (discover_twitch_clips, discover_kick, discover_youtube):
        try:
            total += fn(cfg, db)
        except Exception as e:  # one source failing must not stop the others
            log.exception("%s failed: %s", fn.__name__, e)
    return total

