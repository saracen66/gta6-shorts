from __future__ import annotations

from typing import Any


def streamer_key(platform: str, streamer: str) -> str:
    return f"{platform}:{streamer}".lower()


def streamer_allowed(cfg: dict[str, Any], platform: str, streamer: str) -> bool:
    s = cfg.get("streamers", {}) or {}
    key = streamer_key(platform, streamer)
    block = {x.lower() for x in s.get("blocklist", []) or []}
    if key in block:
        return False
    if s.get("mode", "blocklist") == "allowlist":
        allow = {x.lower() for x in s.get("allowlist", []) or []}
        return key in allow
    return True


def language_ok(cfg: dict[str, Any], lang: str | None) -> bool:
    langs = [x.lower() for x in (cfg.get("game", {}).get("languages") or [])]
    if not langs or not lang:
        return True
    return lang.lower().split("-")[0] in langs
