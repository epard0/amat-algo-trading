"""No engine commands are executed by these launcher argument checks."""

import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/start.py"


class LauncherTests(unittest.TestCase):
    def test_invalid_port_rejected_before_process_start(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--api-port", "0"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("ports must be between", result.stderr)

    def test_help_documents_launch_modes(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--production", result.stdout)
        self.assertIn("--api-only", result.stdout)


if __name__ == "__main__":
    unittest.main()
