# Moki — launchd jobs

Background services that keep the system alive between pipeline runs:

| Job                     | Frequency           | Purpose                                                            |
|-------------------------|---------------------|--------------------------------------------------------------------|
| `com.paz.moki.autonomy` | hourly, on the hour | Run `autonomy.py` — each agent's daily routine, dispatched by hour |

## Install

```bash
cp /Users/ASUS/education-agents/launchd/com.paz.moki.autonomy.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.paz.moki.autonomy.plist
```

Verify:

```bash
launchctl list | grep moki
```

## Configure autonomy tier

In `.env` (default = 1, no-LLM):

```bash
MOKI_AUTONOMY_TIER=1   # deterministic only ($0/day extra)
MOKI_AUTONOMY_TIER=2   # + cheap LLM routines (~$1.50/day)
MOKI_AUTONOMY_TIER=3   # + creative LLM routines (~$3.50/day)
```

## Monitor

```bash
tail -f /Users/ASUS/education-agents/output/autonomy.log
cat    /Users/ASUS/education-agents/output/_memory/active_alerts.md
python3 /Users/ASUS/education-agents/autonomy.py --list   # show schedule
```

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.paz.moki.autonomy.plist
rm ~/Library/LaunchAgents/com.paz.moki.autonomy.plist
```

## Optional: LinkedIn auto-publish

See `LINKEDIN_SETUP.md` for the one-time OAuth setup. Once configured,
`python3 mark_published.py <token>` will publish the post automatically
instead of just marking it.
