# LinkedIn auto-publish — one-time setup

Once configured, clicking **✅ פרסם** in the Telegram approval card will
push the post directly to your LinkedIn feed. No copy-paste.

**Approval timeline:** the "Share on LinkedIn" product is auto-approved
in most cases, but a manual review can take 1-3 business days.

## Step 1 — register your app

1. Open https://www.linkedin.com/developers/apps/new
2. Fields:
   - **App name:** Moki (or whatever you want — only you see it)
   - **LinkedIn Page:** any page you admin, or your personal page
   - **App logo:** any 100×100 PNG
3. Click **Create app**.

## Step 2 — request the publish scope

In the new app's dashboard:

1. **Products** tab → find **"Share on LinkedIn"** → click **Request access**
2. **Products** tab → also request **"Sign In with LinkedIn using OpenID Connect"**
   (needed to fetch your user URN for the `author` field)

Approval is usually automatic but can take 1-3 days for new apps.
You'll see ✅ next to each product when ready.

## Step 3 — add the redirect URL

In the same dashboard:

1. **Auth** tab → **Authorized redirect URLs for your app**
2. Add: `http://localhost:8765/callback`
3. **Update**

## Step 4 — grab credentials

In **Auth** tab, copy:

- **Client ID**
- **Client Secret** (click 👁 to reveal)

## Step 5 — write them to `.env`

```bash
cd /Users/ASUS/education-agents
cat <<EOF >> .env
LINKEDIN_CLIENT_ID=your_client_id_here
LINKEDIN_CLIENT_SECRET=your_client_secret_here
LINKEDIN_REDIRECT_URI=http://localhost:8765/callback
EOF
```

## Step 6 — run the auth flow

```bash
python3 linkedin_publisher.py --auth
```

This will:
1. Open your browser to LinkedIn's login page
2. After you log in + grant permission, browser redirects to
   `http://localhost:8765/callback` (a tiny local server we spin up
   for 5 minutes)
3. The script captures the code, exchanges it for tokens, and writes:
   - `LINKEDIN_ACCESS_TOKEN` (60-day expiry)
   - `LINKEDIN_REFRESH_TOKEN` (365-day expiry)
   - `LINKEDIN_USER_URN` (your account's permanent ID)
4. You can close the browser tab.

## Step 7 — test it

```bash
python3 linkedin_publisher.py --test
```

This posts a small "Moki LinkedIn integration test" message to your
feed. If you see it on LinkedIn, you're done.

You can delete the test post from LinkedIn — it won't break anything.

## Health check

```bash
python3 linkedin_publisher.py --status
```

Shows whether all env vars are set, whether the token is still alive,
and your user URN.

## When tokens expire

- `access_token` expires after 60 days. The publisher auto-refreshes
  on 401 using your `refresh_token`, so you usually won't notice.
- `refresh_token` expires after 365 days. Re-run `python3 linkedin_publisher.py --auth`
  before then. You'll see a 401 in the logs if it slipped.

## Privacy

- All tokens stay local in `.env` (gitignored).
- No data leaves your mac except posts you've explicitly approved
  via the ✅ button.
