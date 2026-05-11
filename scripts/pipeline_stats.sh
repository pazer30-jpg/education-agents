#!/usr/bin/env bash
# ─────────────────────────────────────────────
# scripts/pipeline_stats.sh
# סטטיסטיקות מהירות על הפייפליין
# שימוש: bash scripts/pipeline_stats.sh
# ─────────────────────────────────────────────

cd "$(dirname "$0")/.."

echo "📊 Moki Pipeline Stats"
echo "════════════════════════"

python3 - <<'EOF'
import json
from pathlib import Path
from datetime import datetime

analytics = Path("output/analytics.json")
if not analytics.exists():
    print("❌ אין output/analytics.json")
    exit(1)

with open(analytics) as f:
    d = json.load(f)

runs = d.get("runs", [])
total = len(runs)
if total == 0:
    print("אין ריצות עדיין.")
    exit(0)

success = sum(1 for r in runs if r.get("success"))
fail = total - success
durations = [r["duration_s"]/60 for r in runs if r.get("duration_s")]
avg_dur = sum(durations)/len(durations) if durations else 0

agent_fails = {}
for r in runs:
    for e in r.get("errors", []):
        a = e.get("agent", "unknown")
        agent_fails[a] = agent_fails.get(a, 0) + 1

last5 = runs[-5:]
last5_ok = sum(1 for r in last5 if r.get("success"))

# Output counts
ready = Path("output/ready")
blogs    = len(list((ready/"blog").glob("*.md")))    if (ready/"blog").exists()    else 0
linkedin = len(list((ready/"linkedin").glob("*.txt"))) if (ready/"linkedin").exists() else 0
podcast  = len(list((ready/"podcast").glob("*")))    if (ready/"podcast").exists()  else 0

print(f"  סה\"כ ריצות:      {total}")
print(f"  הצלחות:          {success} ({100*success//total}%)")
print(f"  כשלות:           {fail} ({100*fail//total}%)")
print(f"  ממוצע זמן ריצה:  {avg_dur:.0f} דקות")
print(f"  5 ריצות אחרונות: {last5_ok}/5 הצליחו")
print()
print("  כשלות לפי סוכן:")
for agent, count in sorted(agent_fails.items(), key=lambda x: -x[1]):
    print(f"    {agent:12s}: {count}")
print()
print("  תוצרים מוכנים:")
print(f"    📝 בלוג:     {blogs} קבצים")
print(f"    💼 LinkedIn: {linkedin} קבצים")
print(f"    🎙 פודקאסט:  {podcast} קבצים")
EOF

echo ""
echo "════════════════════════"
echo "לוגים אחרונים:"
tail -5 output/moki.log 2>/dev/null || echo "  (אין לוג)"
