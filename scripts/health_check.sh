#!/usr/bin/env bash
# ─────────────────────────────────────────────
# scripts/health_check.sh
# בדיקת תקינות לפני הרצת הפייפליין
# שימוש: bash scripts/health_check.sh
# ─────────────────────────────────────────────

set -e
cd "$(dirname "$0")/.."

echo "🔍 Moki Health Check"
echo "════════════════════"

PASS=0
FAIL=0

check() {
    local label="$1"
    local cmd="$2"
    if eval "$cmd" &>/dev/null; then
        echo "  ✅ $label"
        PASS=$((PASS + 1))
    else
        echo "  ❌ $label"
        FAIL=$((FAIL + 1))
    fi
}

# Python
check "Python 3.9+" "python3 -c 'import sys; assert sys.version_info >= (3,9)'"

# Requirements
check "requests"          "python3 -c 'import requests'"
check "anthropic"         "python3 -c 'import anthropic'"
check "python-dotenv"     "python3 -c 'import dotenv'"
check "python-docx"       "python3 -c 'import docx'"
check "pdfplumber"        "python3 -c 'import pdfplumber'"

# Claude CLI or API key
if command -v claude &>/dev/null; then
    echo "  ✅ Claude CLI נמצא"
    ((PASS++))
elif [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "  ✅ ANTHROPIC_API_KEY מוגדר"
    ((PASS++))
else
    echo "  ❌ אין Claude CLI ואין ANTHROPIC_API_KEY — הפייפליין לא יעבוד"
    ((FAIL++))
fi

# Output dirs
check "output/ready/blog"     "test -d output/ready/blog"
check "output/ready/linkedin" "test -d output/ready/linkedin"
check "output/ready/podcast"  "test -d output/ready/podcast"

# .env
if [ -f .env ]; then
    echo "  ✅ קובץ .env קיים"
    ((PASS++))
else
    echo "  ⚠️  אין קובץ .env (אופציונלי — cp .env.example .env)"
fi

echo ""
echo "════════════════════"
echo "תוצאה: $PASS ✅  |  $FAIL ❌"

if [ "$FAIL" -gt 0 ]; then
    echo "⚠️  יש בעיות — תקן לפני הרצת הפייפליין"
    exit 1
else
    echo "🚀 הכל תקין — אפשר להריץ"
    exit 0
fi
