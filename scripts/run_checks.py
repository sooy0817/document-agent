"""Run unittest-compatible suite and retain exact executed/skipped test evidence."""
import io
import json
import platform
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
stream = io.StringIO()
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
          "runner": "unittest", "discovered": result.testsRun,
          "executed": result.testsRun-len(result.skipped),
          "passed": result.testsRun-len(result.skipped)-len(result.failures)-len(result.errors),
          "failures": len(result.failures), "errors": len(result.errors),
          "skipped": [{"test": str(t), "reason": why} for t, why in result.skipped],
          "full_gate_passed": result.wasSuccessful() and not result.skipped}
folder = ROOT / "reports"
folder.mkdir(exist_ok=True)
(folder / "test-output.txt").write_text(stream.getvalue(), encoding="utf-8")
(folder / "test-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(stream.getvalue())
print(json.dumps(report, indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)
