"""
test_chat_smoke.py — Smoke test for all chat commands
Runs each command once through _chat_process and checks it doesn't crash.
"""
import sys
sys.argv = ["test"]  # prevent CLI arg parsing

from agent5_project_manager import _chat_process

COMMANDS = [
    ("עזרה",           "פקודות"),
    ("סטטוס",          None),
    ("קבצים",          "פלטפורמה"),
    ("ביצועים",        None),
    ("ממיר רשימה",     None),
    ("לוגים 5",        None),
    ("תור",            None),
    ("מה קרה",         None),
    ("checkpoints",    None),
    ("ביבליוגרפיה",    None),
    ("סדר",            None),
    ("ארכיון",         None),
]


def run_smoke():
    session = {"history": [], "topic": "test"}
    passed, failed = 0, 0

    print(f"\n{'='*50}")
    print(f"  Smoke Test — {len(COMMANDS)} commands")
    print(f"{'='*50}\n")

    for cmd, expect_substr in COMMANDS:
        try:
            result = _chat_process(cmd, session, auto=True)
            result_str = str(result) if result else ""
            if expect_substr and expect_substr not in result_str:
                print(f"  ⚠️  {cmd!r:20} — returned but missing '{expect_substr}'")
                failed += 1
            else:
                print(f"  ✅ {cmd!r:20}")
                passed += 1
        except Exception as e:
            print(f"  ❌ {cmd!r:20} — {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"  {'✅' if not failed else '⚠️'}  {passed}/{passed+failed} passed")
    print(f"{'='*50}\n")
    return failed == 0


if __name__ == "__main__":
    success = run_smoke()
    sys.exit(0 if success else 1)
