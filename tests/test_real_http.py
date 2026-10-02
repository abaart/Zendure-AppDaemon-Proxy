# SPDX-License-Identifier: GPL-3.0-only
"""Use a separate process to keep genuine HTTP libraries out of fake fixtures."""
from pathlib import Path
import subprocess
import unittest
import sys


class RealHttpRuntimeTests(unittest.TestCase):
    def test_runtime_with_real_aiohttp_and_mocked_appdaemon_methods(self):
        root = Path(__file__).resolve().parents[1]
        interpreter = root / ".venv" / "bin" / "python"
        if not interpreter.exists():
            interpreter = Path(sys.executable)
        result = subprocess.run([str(interpreter), str(root / "scripts" / "verify_http_runtime.py")],
            cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"status": "passed"', result.stdout)
        self.assertIn('"max_device_requests_in_flight": 1', result.stdout)
