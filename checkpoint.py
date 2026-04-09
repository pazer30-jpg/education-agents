"""
checkpoint.py — שמירת מצב + המשכה
שומר את מצב ה-pipeline בכל שלב.
אם הריצה נקטעת — אפשר להמשיך מאיפה שעצרה.

Usage:
  from checkpoint import Checkpoint
  ckpt = Checkpoint("run_20240101_1200")
  ckpt.save("researcher_1", {"file": str(papers_file)})
  ...
  ckpt.save("writer", {"md": str(md), "docx": str(docx)})

  # Resume:
  ckpt = Checkpoint.latest()  # טוען את הריצה האחרונה
  if ckpt.done("researcher_1"):
      papers_file = Path(ckpt.get("researcher_1")["file"])
"""

import json
from pathlib import Path
from datetime import datetime
from config import OUTPUT_DIR

CHECKPOINTS_DIR = OUTPUT_DIR / "checkpoints"
CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)


class Checkpoint:
    def __init__(self, run_id: str = None):
        if run_id is None:
            run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.run_id   = run_id
        self.path     = CHECKPOINTS_DIR / f"{run_id}.json"
        self._data    = self._load()

    # ── persistence ──────────────────────────

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "run_id":     self.run_id,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "steps":      {},
            "meta":       {},
        }

    def _persist(self):
        self._data["updated_at"] = datetime.now().isoformat()
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    # ── public API ───────────────────────────

    def save(self, step: str, value: dict):
        """שמור תוצאה של שלב."""
        self._data["steps"][step] = {
            "value":    value,
            "saved_at": datetime.now().isoformat(),
        }
        self._persist()
        print(f"  💾 checkpoint: {step}")

    def get(self, step: str) -> dict | None:
        """קבל תוצאה שמורה של שלב."""
        entry = self._data["steps"].get(step)
        return entry["value"] if entry else None

    def done(self, step: str) -> bool:
        """האם השלב כבר הסתיים?"""
        return step in self._data["steps"]

    def set_meta(self, key: str, value):
        self._data["meta"][key] = value
        self._persist()

    def get_meta(self, key: str):
        return self._data["meta"].get(key)

    def summary(self) -> str:
        steps = list(self._data["steps"].keys())
        return (
            f"Checkpoint {self.run_id} | "
            f"{len(steps)} steps done: {', '.join(steps)}"
        )

    def delete(self):
        if self.path.exists():
            self.path.unlink()

    # ── class methods ────────────────────────

    @classmethod
    def latest(cls) -> "Checkpoint | None":
        """טוען את הריצה האחרונה (לא מושלמת)."""
        files = sorted(
            CHECKPOINTS_DIR.glob("run_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return None
        run_id = files[0].stem
        ckpt   = cls(run_id)
        print(f"  ♻️  ממשיך: {ckpt.summary()}")
        return ckpt

    @classmethod
    def list_all(cls) -> list["Checkpoint"]:
        files = sorted(
            CHECKPOINTS_DIR.glob("run_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return [cls(f.stem) for f in files[:10]]

    @classmethod
    def print_status(cls):
        checkpoints = cls.list_all()
        if not checkpoints:
            print("  אין checkpoints.")
            return
        print(f"\n  📋 Checkpoints אחרונים ({len(checkpoints)}):")
        for c in checkpoints:
            steps = list(c._data["steps"].keys())
            updated = c._data.get("updated_at", "")[:16].replace("T", " ")
            print(f"    {c.run_id}  [{updated}]  steps: {', '.join(steps)}")
