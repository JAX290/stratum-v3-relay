import json
import re
import unittest
from pathlib import Path

import stratum_admin_v3 as admin


ROOT = Path(__file__).resolve().parent.parent


class VersionConsistencyTest(unittest.TestCase):
    def test_all_runtime_versions_come_from_manifest(self):
        versions = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
        self.assertEqual(admin.PANEL_VERSION, versions["panel"])
        self.assertEqual(admin.CURRENT_CLIENT_VERSION, versions["windows_client"])
        self.assertEqual(admin.CURRENT_RELAY_VERSION, versions["secure_relay"])
        for value in versions.values():
            self.assertRegex(value, r"^\d+\.\d+\.\d+$")

    def test_client_source_has_no_second_assembly_version(self):
        source = (ROOT / "secure-relay" / "client" / "StratumSecureRelay.cs").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"Assembly(File)?Version", source))
        build = (ROOT / "secure-relay" / "client" / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("version.json", build)
        self.assertIn("AssemblyFileVersion", build)


if __name__ == "__main__":
    unittest.main()
