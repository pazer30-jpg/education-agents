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
import hashlib
import os
from pathlib import Path
from datetime import datetime
from config import OUTPUT_DIR

CHECKPOINTS_DIR = OUTPUT_DIR / "checkpoints"
CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Checkpoint:
    def __init__(self, run_id: str = None):
        if run_id is None:
            run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.run_id   = run_id
        self.path     = CHECKPOINTS_DIR / f"{run_id}.json"
        self._data    = self._load()

    # ── persistence ──────────────────────────

    def _load(self) -> dict:
        # Try primary path, then last-known-good backup if hash mismatch
        for candidate in (self.path, self.path.with_suffix(".json.bak")):
            if candidate.exists():
                try:
                    text = candidate.read_text(encoding="utf-8")
                    data = json.loads(text)
                    # Verify hash if present
                    expected = data.pop("_hash", None)
                    if expected:
                        actual = _content_hash(json.dumps(data, ensure_ascii=False,
                                                          sort_keys=True, default=str))
                        if expected != actual:
                            print(f"  ⚠️ checkpoint hash mismatch in {candidate.name} — trying backup")
                            continue
                    return data
                except Exception as e:
                    print(f"  ⚠️ checkpoint load failed for {candidate.name}: {e}")
                    continue
        return {
            "run_id":     self.run_id,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "steps":      {},
            "meta":       {},
        }

    def _persist(self):
        """Atomic write with hash validation + last-known-good backup.

        Steps:
          1. Backup current file → .bak (if exists, valid)
          2. Compute hash of new payload
          3. Write to .tmp
          4. fsync + atomic rename → primary path
          5. New file with hash that we can verify on next load
        """
        self._data["updated_at"] = datetime.now().isoformat()

        # Backup current file as last-known-good
        if self.path.exists():
            try:
                self.path.replace(self.path.with_suffix(".json.bak"))
            except Exception:
                pass

        # Compute hash of stable payload (sort keys for determinism)
        payload = dict(self._data)
        payload.pop("_hash", None)
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        payload["_hash"] = _content_hash(canonical)

        # Atomic write — tmp file → fsync → rename
        tmp_path = self.path.with_suffix(".json.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            tmp_path.replace(self.path)
        except Exception as e:
            print(f"  ⚠️ checkpoint persist failed: {e}")
            # Restore from backup if write failed
            bak = self.path.with_suffix(".json.bak")
            if bak.exists() and not self.path.exists():
                bak.replace(self.path)
            raise

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
