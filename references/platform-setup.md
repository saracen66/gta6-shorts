# Platform credentials

All values go in the workspace `.env` (`$GTA6_SHORTS_HOME/.env`, default `~/gta6-shorts/.env`).
OAuth tokens are written to `$GTA6_SHORTS_HOME/secrets/` by the `auth` commands.

## Claude (judge)
`ANTHROPIC_API_KEY` from https://platform.claude.com, or run `ant auth login` once.
Cost: roughly a few cents per judged candidate (6 frames + transcript). Capped by `judge.max_per_cycle`.

## Sources (read-only)

### Twitch
1. https://dev.twitch.tv/console/apps -> Register Your Application (OAuth redirect `http://localhost`, category "Application Integration").
2. Copy Client ID -> `TWITCH_CLIENT_ID`, New Secret -> `TWITCH_CLIENT_SECRET`.
No user login needed (app access token).

### Kick
1. https://kick.com/settings/developer -> create app.
2. `KICK_CLIENT_ID`, `KICK_CLIENT_SECRET`.
If category lookup fails, set `sources.kick.category_id` in config.yaml.
Kick clip listing uses an unofficial endpoint that may be blocked; live recording still works.

### YouTube search
Google Cloud console -> enable "YouTube Data API v3" -> Credentials -> API key -> `YOUTUBE_API_KEY`.
Each search costs 100 of the 10,000 daily quota units.

## Destinations (posting)

### YouTube Shorts
1. Same Google Cloud project -> OAuth consent screen (External; add your channel's Google account as a test user,
   or publish the app).
2. Credentials -> Create OAuth client ID -> **Desktop app** -> download JSON ->
   save as `$GTA6_SHORTS_HOME/secrets/youtube_client_secret.json`.
3. Run `auth youtube`.
Quota: an upload costs 1,600 units -> about 6 uploads/day on the default quota. Request more via Google's quota form.
Note: videos uploaded by unverified API projects may be locked private until the project passes Google's audit.

### Facebook Page Reels + Instagram Reels
Requirements: a Facebook Page, and an Instagram **Business or Creator** account linked to that Page.
1. https://developers.facebook.com -> Create app (type Business).
2. Add products: Facebook Login for Business, Instagram Graph API.
3. Graph API Explorer: select app, get a **User** token with
   `pages_show_list, pages_read_engagement, pages_manage_posts, instagram_basic, instagram_content_publish, business_management`.
4. Exchange for a long-lived user token:
   `GET https://graph.facebook.com/v24.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_TOKEN`
5. `GET /me/accounts?access_token=LONG_USER_TOKEN` -> the Page's `access_token` (does not expire) -> `FB_PAGE_TOKEN`, and its `id` -> `FB_PAGE_ID`.
6. `GET /{FB_PAGE_ID}?fields=instagram_business_account&access_token=FB_PAGE_TOKEN` -> `IG_USER_ID`.
While the Meta app is in development mode it can post only to accounts that have a role on the app. That is
fine for your own Page/IG. Instagram limit: 50 API posts per 24h.

### TikTok
1. https://developers.tiktok.com -> create app -> add **Login Kit** (Desktop) and **Content Posting API**
   (enable Direct Post if you want auto-publish).
2. Scopes: `user.info.basic`, `video.upload`, `video.publish`.
3. Redirect URI: `http://localhost:8765/callback` (must match `TIKTOK_REDIRECT_URI`).
4. `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, then run `auth tiktok`.
   If TikTok rejects the PKCE challenge, set `TIKTOK_PKCE=base64url` in `.env` and retry.
5. **Audit**: until TikTok audits your app, direct posts are forced to `SELF_ONLY` (private). Either submit the app
   for audit, or set `platforms.tiktok.mode: inbox`. Clips then land in your TikTok drafts/inbox and you tap Post.
