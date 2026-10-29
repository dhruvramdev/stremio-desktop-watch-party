"""
Unit tests for compatibility and safety gate module.
"""

import unittest
import os
import tempfile
import plistlib
import shutil
import json

from launcher.compatibility import (
    find_stremio_app,
    get_stremio_version,
    check_entitlements,
    check_architecture,
    check_compatibility
)

class TestCompatibility(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.app_path = os.path.join(self.test_dir, "Stremio.app")
        contents_dir = os.path.join(self.app_path, "Contents")
        macos_dir = os.path.join(contents_dir, "MacOS")
        os.makedirs(macos_dir, exist_ok=True)

        # Create dummy Info.plist
        self.info_plist_path = os.path.join(contents_dir, "Info.plist")
        plist_data = {
            "CFBundleShortVersionString": "5.1.30",
            "CFBundleIdentifier": "com.stremio.stremio"
        }
        with open(self.info_plist_path, "wb") as f:
            plistlib.dump(plist_data, f)

        # Create dummy manifest.json
        self.manifest_path = os.path.join(self.test_dir, "manifest.json")
        manifest_data = {
            "schema_version": 1,
            "supported_versions": {
                "5.1.30": {
                    "architectures": ["arm64", "x86_64"],
                    "notes": "Test manifest"
                }
            }
        }
        with open(self.manifest_path, "w") as f:
            json.dump(manifest_data, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_get_stremio_version(self):
        version = get_stremio_version(self.app_path)
        self.assertEqual(version, "5.1.30")

    def test_check_compatibility_success(self):
        # With environment override for entitlements in test
        os.environ["STREMIO_WP_IGNORE_ENTITLEMENTS"] = "1"
        try:
            is_compat, details = check_compatibility(self.manifest_path, self.app_path)
            self.assertTrue(is_compat)
            self.assertEqual(details["version"], "5.1.30")
            self.assertTrue(details["version_supported"])
        finally:
            del os.environ["STREMIO_WP_IGNORE_ENTITLEMENTS"]

    def test_check_compatibility_unsupported_version(self):
        # Update Info.plist to unsupported version
        contents_dir = os.path.join(self.app_path, "Contents")
        with open(os.path.join(contents_dir, "Info.plist"), "wb") as f:
            plistlib.dump({"CFBundleShortVersionString": "9.9.9"}, f)

        is_compat, details = check_compatibility(self.manifest_path, self.app_path, force=False)
        self.assertFalse(is_compat)
        self.assertFalse(details["version_supported"])

        # Test with force=True
        is_compat_forced, details_forced = check_compatibility(self.manifest_path, self.app_path, force=True)
        self.assertTrue(is_compat_forced)
        self.assertTrue(details_forced["version_supported"])

if __name__ == "__main__":
    unittest.main()
