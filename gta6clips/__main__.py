"""CLI: python -m gta6clips <command>"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

from .config import DATA, OUT, RAW, ROOT, SECRETS, SKILL_DIR, WORK, env, load_config, load_env, setup_logging
from .db import DB

LOCK = DATA / "run.lock"


def init_workspace() -> None:
    import shutil
    for src, dst in (("config.example.yaml", "config.yaml"), (".env.example", ".env")):
        target = ROOT / dst
        if not target.exists():
            shutil.copy(SKILL_DIR / src, target)
            print(f"created {target}")
        else:
            print(f"exists  {target}")
    print(f"workspace: {ROOT}\nNext: fill in {ROOT / '.env'}, then run `doctor`.")


def doctor(cfg: dict) -> int:
    """Print what is configured and what is missing. Exit code 0 = ready to run."""
    import shutil
    checks: list[tuple[str, bool, str]] = []
    add = lambda name, ok, fix="": checks.append((name, ok, fix))  # noqa: E731
    add("ffmpeg on PATH", bool(shutil.which("ffmpeg")), "install ffmpeg and add to PATH")
    add("workspace config.yaml", (ROOT / "config.yaml").exists(), "run `init`")
    if cfg["judge"].get("provider", "api") == "claude-code":
        add("Claude Code CLI (judge via subscription)", bool(shutil.which("claude")),
            "install Claude Code and run `claude` once to log in")
    else:
        add("Claude API credentials", bool(env("ANTHROPIC_API_KEY") or env("ANTHROPIC_AUTH_TOKEN")
                                           or Path.home().joinpath(".config", "anthropic").exists()),
            "set ANTHROPIC_API_KEY in .env, or judge.provider: claude-code")
    src = cfg["sources"]
    if src["twitch_clips"].get("enabled") or src["live_record"].get("enabled"):
        add("Twitch app keys", bool(env("TWITCH_CLIENT_ID") and env("TWITCH_CLIENT_SECRET")),
            "TWITCH_CLIENT_ID/SECRET from dev.twitch.tv/console/apps")
    if src["kick"].get("enabled"):
        add("Kick app keys", bool(env("KICK_CLIENT_ID") and env("KICK_CLIENT_SECRET")),
            "KICK_CLIENT_ID/SECRET from kick.com/settings/developer")
    if src["youtube"].get("enabled") and not env("YOUTUBE_API_KEY"):
        print("[--] YouTube search: no YOUTUBE_API_KEY, using yt-dlp search (fine)")
    pl = cfg["platforms"]
    if pl["youtube"].get("enabled"):
        add("YouTube upload auth", (SECRETS / "youtube_token.json").exists(), "run `auth youtube`")
    if pl["facebook"].get("enabled"):
        add("Facebook page token", bool(env("FB_PAGE_ID") and env("FB_PAGE_TOKEN")), "FB_PAGE_ID/FB_PAGE_TOKEN")
    if pl["instagram"].get("enabled"):
        add("Instagram user id", bool(env("IG_USER_ID") and env("FB_PAGE_TOKEN")), "IG_USER_ID + FB_PAGE_TOKEN")
    if pl["tiktok"].get("enabled"):
        add("TikTok auth", (SECRETS / "tiktok_token.json").exists(), "run `auth tiktok`")
    try:
        import faster_whisper  # noqa: F401
        add("faster-whisper (captions)", True)
    except ImportError:
        add("faster-whisper (captions)", False, "pip install faster-whisper, or set render.captions: false")
    bad = 0
    for name, ok, fix in checks:
        print(f"[{'ok' if ok else '!!'}] {name}" + ("" if ok else f"  ->  {fix}"))
        bad += not ok
    print(f"\nworkspace: {ROOT}")
    return 1 if bad else 0


def acquire_lock(stale: float = 3 * 3600) -> bool:
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < stale:
        return False
    LOCK.write_text(str(os.getpid()))
    return True


def cleanup(cfg: dict) -> int:
    cutoff = time.time() - cfg["schedule"].get("keep_days", 7) * 86400
    n = 0
    for d in (RAW, WORK, OUT):
        for f in d.rglob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                n += 1
    return n


def cycle(cfg: dict, db: DB, log) -> None:
    from .discover import discover_all
    from .live import record_and_detect
    from .process import process_pending
    from .publish import publish_due
    log.info("cycle start")
    log.info("discovered %d", discover_all(cfg, db))
    try:
        log.info("live candidates %d", record_and_detect(cfg, db))
    except Exception:
        log.exception("live recording failed")
    log.info("processed %s", process_pending(cfg, db))
    log.info("published %s", publish_due(cfg, db))
    log.info("cleanup removed %d old files", cleanup(cfg))
    log.info("stats %s", db.stats())


def test_url(cfg: dict, db: DB, url: str, start: float | None, end: float | None) -> int:
    """Judge + render one URL without queueing any posts. Prints verdict and output path."""
    from . import media
    from .db import Candidate
    from .process import process_one, safe_name
    info = media.info(url)
    platform = ("twitch" if "twitch.tv" in url else "kick" if "kick.com" in url else "youtube")
    streamer = info.get("channel") or info.get("uploader") or "unknown"
    streamer_url = info.get("channel_url") or info.get("uploader_url") or url
    dur = info.get("duration") or 0
    if start is None and dur > cfg["render"]["max_seconds"] + 30:
        raise SystemExit(f"video is {dur:.0f}s long: pass --start/--end (max ~75s window)")
    cid = f"test:{info.get('id', safe_name(url))}:{int(start or 0)}"
    db.conn.execute("DELETE FROM candidates WHERE id=?", (cid,))
    db.conn.commit()
    c = Candidate(id=cid, source="test", platform=platform, url=url, source_url=url,
                  streamer=streamer, streamer_url=streamer_url, title=info.get("title", ""),
                  start=start, end=end, meta={"views": info.get("view_count")})
    db.add_candidate(c)
    saved = {p: dict(v) for p, v in cfg["platforms"].items()}
    for v in cfg["platforms"].values():
        v["enabled"] = False  # never queue posts from a test
    result = process_one(cfg, db, c)
    cfg["platforms"] = saved
    row = db.conn.execute("SELECT status, error FROM candidates WHERE id=?", (cid,)).fetchone()
    print(f"\nresult: {result}  ({row['error'] or ''})")
    clip = db.conn.execute("SELECT file, verdict, description FROM clips WHERE id=?", (cid,)).fetchone()
    if clip:
        print(f"video: {clip['file']}\nverdict: {json.dumps(json.loads(clip['verdict']), indent=2)}")
        print(f"\ndescription:\n{clip['description']}")
    return 0 if result == "rendered" else 1


def block_streamer(spec: str) -> None:
    """Add platform:streamer to blocklist, cancel queued posts, list live posts to delete."""
    platform, _, streamer = spec.partition(":")
    if not streamer:
        raise SystemExit("format: platform:streamer  e.g. twitch:somestreamer")
    path = ROOT / "config.yaml"
    if not path.exists():
        raise SystemExit("create config.yaml first (copy config.example.yaml)")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    bl = cfg.setdefault("streamers", {}).setdefault("blocklist", []) or []
    if spec.lower() not in [x.lower() for x in bl]:
        bl.append(spec.lower())
        cfg["streamers"]["blocklist"] = bl
        path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    posted = DB().skip_streamer_posts(platform, streamer)
    print(f"Blocked {spec}. Queued posts cancelled.")
    if posted:
        print("Already published - delete these manually on each platform:")
        for r in posted:
            print(f"  {r['platform']}: {r['remote_id']}  (clip {r['clip_id']})")


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles choke on emoji in titles
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    load_env()
    log = setup_logging()
    ap = argparse.ArgumentParser(prog="gta6clips")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create workspace config.yaml and .env")
    sub.add_parser("doctor", help="check setup; exit 1 if something is missing")
    sub.add_parser("once", help="one full cycle (for Task Scheduler / cron)")
    r = sub.add_parser("run", help="loop forever")
    r.add_argument("--minutes", type=float)
    sub.add_parser("discover")
    sub.add_parser("process")
    sub.add_parser("publish")
    sub.add_parser("status")
    a = sub.add_parser("auth"); a.add_argument("platform", choices=["youtube", "tiktok"])
    b = sub.add_parser("block", help="takedown: block platform:streamer"); b.add_argument("spec")
    u = sub.add_parser("test-url", help="run one clip/video URL through judge + render (no posting)")
    u.add_argument("url"); u.add_argument("--start", type=float); u.add_argument("--end", type=float)
    t = sub.add_parser("test-render", help="render a local video file through the full edit")
    t.add_argument("video"); t.add_argument("--hook", default="TEST HOOK TITLE")
    t.add_argument("--start", type=float, default=0); t.add_argument("--end", type=float, default=20)
    ap_args = ap.parse_args(argv)

    cfg = load_config()
    db = DB()
    cmd = ap_args.cmd
    if cmd == "init":
        init_workspace()
    elif cmd == "doctor":
        return doctor(cfg)
    elif cmd == "auth":
        from .publish import tiktok_auth, youtube_auth
        (youtube_auth if ap_args.platform == "youtube" else tiktok_auth)()
    elif cmd == "block":
        block_streamer(ap_args.spec)
    elif cmd == "status":
        print(json.dumps(db.stats(), indent=2))
        for row in db.conn.execute(
                "SELECT p.platform, p.status, p.remote_id, p.error, c.post_title FROM posts p "
                "JOIN clips c ON c.id=p.clip_id ORDER BY coalesce(p.posted_at, c.created_at) DESC LIMIT 20"):
            print(dict(row))
    elif cmd == "discover":
        from .discover import discover_all
        print("new candidates:", discover_all(cfg, db))
    elif cmd == "process":
        from .process import process_pending
        print(process_pending(cfg, db))
    elif cmd == "publish":
        from .publish import publish_due
        print(publish_due(cfg, db))
    elif cmd == "test-url":
        return test_url(cfg, db, ap_args.url, ap_args.start, ap_args.end)
    elif cmd == "test-render":
        from .render import render
        from .transcribe import transcribe
        src = Path(ap_args.video)
        words = transcribe(src, cfg["render"].get("whisper_model", "small"))
        out = OUT / f"test_{src.stem}.mp4"
        render(src, out, ap_args.start, ap_args.end, words, ap_args.hook, "twitch.tv/example", cfg)
        print(out)
    elif cmd in ("once", "run"):
        if not acquire_lock():
            log.info("another run is active; exiting")
            return
        try:
            while True:
                try:
                    cycle(cfg, db, log)
                except Exception:
                    log.exception("cycle crashed")
                if cmd == "once":
                    break
                LOCK.touch()
                time.sleep(60 * (ap_args.minutes or cfg["schedule"]["cycle_minutes"]))
                cfg = load_config()  # pick up config edits (e.g. blocklist) without restart
        finally:
            LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
