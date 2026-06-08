"""
linkedin_publisher.py — LinkedIn API wrapper for auto-publishing posts.

Closes the manual gap: when you click ✅ פרסם in Telegram, the post is
pushed to LinkedIn automatically instead of being copy-pasted by hand.

Auth model: OAuth 2.0 (3-legged). One-time setup, then access_token
expires every 60 days and refresh_token expires every 365.

WHEN THIS RUNS
──────────────
  - Only if LINKEDIN_ACCESS_TOKEN is in .env (graceful no-op otherwise)
  - Called from telegram_approval._handle_approve (post to LinkedIn → move
    file to output/published/ → reply ✅ in Telegram with the post URL)
  - Token refresh attempted automatically when LINKEDIN_REFRESH_TOKEN
    is present and access_token returns 401

ONE-TIME SETUP (see launchd/LINKEDIN_SETUP.md for screenshots)
──────────────────────────────────────────────────────────────
  1. https://www.linkedin.com/developers/apps/new — register an app
  2. Products → request "Share on LinkedIn" + "Sign In with LinkedIn"
     (approval: 1-3 days for the first scope)
  3. Auth → add http://localhost:8765/callback as a redirect URL
  4. Auth → grab Client ID + Client Secret
  5. Add to .env:
        LINKEDIN_CLIENT_ID=...
        LINKEDIN_CLIENT_SECRET=...
        LINKEDIN_REDIRECT_URI=http://localhost:8765/callback
  6. Run: python3 linkedin_publisher.py --auth
     → opens browser → log in → grants permission
     → script captures callback, writes LINKEDIN_ACCESS_TOKEN +
       LINKEDIN_REFRESH_TOKEN + LINKEDIN_USER_URN to .env
  7. Test: python3 linkedin_publisher.py --test
     → posts a tiny test message to your LinkedIn

USAGE FROM CODE
────────────────
  from linkedin_publisher import publish, is_configured
  if is_configured():
      result = publish("Hello world from Moki")
      if result.get("ok"):
          print(f"Posted: {result['url']}")

CLI
────
  python3 linkedin_publisher.py --auth        # one-time OAuth setup
  python3 linkedin_publisher.py --test        # send a test post
  python3 linkedin_publisher.py --refresh     # force token refresh
  python3 linkedin_publisher.py --status      # show token health
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

# Only import requests on actual use so import errors don't crash
# autonomy.py when LinkedIn isn't configured

ENV_FILE = Path(__file__).parent / ".env"

CLIENT_ID     = os.environ.get("LINKEDIN_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
REDIRECT_URI  = os.environ.get("LINKEDIN_REDIRECT_URI", "http://localhost:8765/callback")
ACCESS_TOKEN  = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
REFRESH_TOKEN = os.environ.get("LINKEDIN_REFRESH_TOKEN", "")
USER_URN      = os.environ.get("LINKEDIN_USER_URN", "")  # "urn:li:person:abc123"

AUTH_URL    = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL   = "https://www.linkedin.com/oauth/v2/accessToken"
ME_URL      = "https://api.linkedin.com/v2/userinfo"
UGC_URL     = "https://api.linkedin.com/v2/ugcPosts"

SCOPES = "w_member_social openid profile email"


# ─────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────

def is_configured() -> bool:
    return bool(ACCESS_TOKEN and USER_URN)


def _read_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    env = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def _write_env_kv(key: str, value: str) -> None:
    """Set or replace a KEY=value line in .env. Preserves comments + order."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text(f"{key}={value}\n", encoding="utf-8")
        return
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _set_runtime(key: str, value: str) -> None:
    """Update both os.environ AND .env so next import sees the change."""
    os.environ[key] = value
    _write_env_kv(key, value)
    globals()[key] = value


# ─────────────────────────────────────────────
# OAuth flow
# ─────────────────────────────────────────────

def _build_auth_url(state: str = "moki") -> str:
    qs = urllib.parse.urlencode({
        "response_type": "code",
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "scope":         SCOPES,
        "state":         state,
    })
    return f"{AUTH_URL}?{qs}"


def _exchange_code(code: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    import requests
    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  REDIRECT_URI,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"token exchange failed: HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def _fetch_user_urn(access_token: str) -> str:
    """Get the authenticated user's URN — needed in ugcPosts author field."""
    import requests
    r = requests.get(ME_URL,
                     headers={"Authorization": f"Bearer {access_token}"},
                     timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"userinfo failed: HTTP {r.status_code}: {r.text[:200]}")
    sub = r.json().get("sub")
    if not sub:
        raise RuntimeError("userinfo returned no sub field")
    return f"urn:li:person:{sub}"


def authenticate() -> dict:
    """Interactive one-time OAuth: opens browser, captures callback, saves tokens."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return {"ok": False, "error": "set LINKEDIN_CLIENT_ID + LINKEDIN_CLIENT_SECRET in .env first"}

    import http.server
    import socketserver
    import threading
    import webbrowser

    parsed = urllib.parse.urlparse(REDIRECT_URI)
    port = parsed.port or 8765
    callback_path = parsed.path or "/callback"

    captured: dict[str, str] = {}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.urlparse(self.path).query
            params = dict(urllib.parse.parse_qsl(qs))
            captured.update(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if "code" in params:
                self.wfile.write(b"<h2>OK</h2><p>You can close this tab.</p>")
            else:
                self.wfile.write(b"<h2>Error</h2><pre>" +
                                 json.dumps(params).encode("utf-8") + b"</pre>")
        def log_message(self, *a, **kw):
            pass  # silence access log

    server = socketserver.TCPServer(("localhost", port), CallbackHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        url = _build_auth_url()
        print(f"\n🌐 Opening browser for LinkedIn authentication...")
        print(f"   If it doesn't open: {url}\n")
        webbrowser.open(url)
        # Wait up to 5 minutes for callback
        for _ in range(300):
            if "code" in captured or "error" in captured:
                break
            time.sleep(1)
    finally:
        server.shutdown()

    if "error" in captured:
        return {"ok": False, "error": captured.get("error_description") or captured["error"]}
    code = captured.get("code")
    if not code:
        return {"ok": False, "error": "no code in callback (timeout?)"}

    try:
        tokens = _exchange_code(code)
        access  = tokens.get("access_token")
        refresh = tokens.get("refresh_token", "")
        if not access:
            return {"ok": False, "error": f"no access_token in response: {tokens}"}
        urn = _fetch_user_urn(access)
        _set_runtime("LINKEDIN_ACCESS_TOKEN", access)
        if refresh:
            _set_runtime("LINKEDIN_REFRESH_TOKEN", refresh)
        _set_runtime("LINKEDIN_USER_URN", urn)
        return {"ok": True, "user_urn": urn,
                "expires_in": tokens.get("expires_in")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def refresh_access_token() -> dict:
    if not REFRESH_TOKEN:
        return {"ok": False, "error": "no LINKEDIN_REFRESH_TOKEN — run --auth again"}
    if not CLIENT_ID or not CLIENT_SECRET:
        return {"ok": False, "error": "missing LINKEDIN_CLIENT_ID / SECRET"}
    import requests
    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type":    "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=15,
    )
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    data = r.json()
    access = data.get("access_token")
    if not access:
        return {"ok": False, "error": f"no access_token: {data}"}
    _set_runtime("LINKEDIN_ACCESS_TOKEN", access)
    if data.get("refresh_token"):
        _set_runtime("LINKEDIN_REFRESH_TOKEN", data["refresh_token"])
    return {"ok": True, "expires_in": data.get("expires_in")}


# ─────────────────────────────────────────────
# Publish
# ─────────────────────────────────────────────

def publish(text: str, dry_run: bool = False) -> dict:
    """
    Publish a text post to LinkedIn. Returns:
      {"ok": True, "url": "https://www.linkedin.com/feed/update/...",
                   "id": "urn:li:share:..."}
      {"ok": False, "error": "..."}
    """
    if not is_configured():
        return {"ok": False, "error": "linkedin not configured (missing token or URN)"}
    if not text or not text.strip():
        return {"ok": False, "error": "empty post text"}

    body = {
        "author":         USER_URN,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary":    {"text": text[:3000]},
                "shareMediaCategory": "NONE",
            },
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
        },
    }

    if dry_run:
        return {"ok": True, "dry_run": True, "payload": body}

    import requests
    headers = {
        "Authorization":              f"Bearer {ACCESS_TOKEN}",
        "X-Restli-Protocol-Version":  "2.0.0",
        "Content-Type":               "application/json",
    }
    for attempt in (1, 2):  # one retry after refresh on 401
        r = requests.post(UGC_URL, headers=headers, json=body, timeout=15)
        if r.status_code == 201:
            post_id = r.headers.get("x-restli-id") or r.json().get("id", "")
            return {
                "ok":  True,
                "id":  post_id,
                "url": f"https://www.linkedin.com/feed/update/{post_id}/",
            }
        if r.status_code == 401 and attempt == 1:
            # Try refresh, then retry once
            refresh_result = refresh_access_token()
            if refresh_result.get("ok"):
                headers["Authorization"] = f"Bearer {ACCESS_TOKEN}"
                continue
            return {"ok": False, "error": f"401, refresh also failed: {refresh_result.get('error')}"}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    return {"ok": False, "error": "unreachable"}


def status() -> dict:
    """Diagnostic — what's configured and roughly how healthy."""
    out = {
        "client_id_set":     bool(CLIENT_ID),
        "client_secret_set": bool(CLIENT_SECRET),
        "access_token_set":  bool(ACCESS_TOKEN),
        "refresh_token_set": bool(REFRESH_TOKEN),
        "user_urn":          USER_URN or None,
    }
    if ACCESS_TOKEN:
        # No expiry timestamp without an explicit call to /v2/userinfo —
        # we attempt the call cheaply
        import requests
        try:
            r = requests.get(ME_URL,
                             headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
                             timeout=8)
            out["token_alive"] = r.status_code == 200
            if r.status_code != 200:
                out["token_error"] = f"HTTP {r.status_code}"
        except Exception as e:
            out["token_alive"] = False
            out["token_error"] = str(e)[:120]
    return out


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="LinkedIn publisher")
    ap.add_argument("--auth",    action="store_true",
                    help="interactive one-time OAuth setup")
    ap.add_argument("--refresh", action="store_true",
                    help="manually refresh access token")
    ap.add_argument("--test",    action="store_true",
                    help="post a small test message")
    ap.add_argument("--status",  action="store_true",
                    help="show config + token alive check")
    args = ap.parse_args()

    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return
    if args.auth:
        res = authenticate()
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if args.refresh:
        res = refresh_access_token()
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if args.test:
        msg = f"🤖 Moki LinkedIn integration test — {datetime.now().strftime('%d/%m %H:%M')}"
        res = publish(msg)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
