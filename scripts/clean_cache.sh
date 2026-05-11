#!/usr/bin/env bash
# ─────────────────────────────────────────────
# scripts/clean_cache.sh
# ניקוי קבצי cache ו-pycache
# שימוש: bash scripts/clean_cache.sh
# ─────────────────────────────────────────────

cd "$(dirname "$0")/.."

echo "🧹 מנקה cache..."

# __pycache__
PYCACHE=$(find . -type d -name "__pycache__" -not -path "./.git/*" | wc -l | tr -d ' ')
find . -type d -name "__pycache__" -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null || true
echo "  ✅ הוסרו $PYCACHE תיקיות __pycache__"

# .pyc / .pyo
PYC=$(find . -name "*.pyc" -o -name "*.pyo" | wc -l | tr -d ' ')
find . \( -name "*.pyc" -o -name "*.pyo" \) -delete 2>/dev/null || true
echo "  ✅ הוסרו $PYC קבצי .pyc/.pyo"

# .DS_Store
DS=$(find . -name ".DS_Store" | wc -l | tr -d ' ')
find . -name ".DS_Store" -delete 2>/dev/null || true
echo "  ✅ הוסרו $DS קבצי .DS_Store"

# Temp drive downloads
if [ -d ".tmp.drivedownload" ]; then
    rm -rf .tmp.drivedownload
    echo "  ✅ הוסרה תיקיית .tmp.drivedownload"
fi

echo ""
echo "🎉 ניקוי הושלם"
