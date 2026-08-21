import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackagingManifestTests(unittest.TestCase):
    def test_online_server_is_in_patch_and_full_installer(self):
        required_files = (
            'Source: "..\\online.py"; DestDir: "{app}"; Flags: ignoreversion',
            'Source: "..\\Start Vector Radio Online.cmd"; DestDir: "{app}"; Flags: ignoreversion',
            'Source: "..\\docs\\ONLINE.md"; DestDir: "{app}"; DestName: "ONLINE.md"; Flags: ignoreversion',
        )
        for name in ("patch.iss", "installer.iss"):
            with self.subTest(manifest=name):
                manifest = (ROOT / "packaging" / name).read_text(encoding="utf-8-sig")
                for required_file in required_files:
                    self.assertIn(required_file, manifest)

    def test_pronunciation_engine_is_in_patch_and_full_installer(self):
        required_module = (
            'Source: "..\\radio_pronunciation.py"; '
            'DestDir: "{app}"; Flags: ignoreversion'
        )
        for name in ("patch.iss", "installer.iss"):
            with self.subTest(manifest=name):
                manifest = (ROOT / "packaging" / name).read_text(encoding="utf-8-sig")
                self.assertIn(required_module, manifest)

    def test_pronunciation_dictionary_is_in_patch_and_full_installer(self):
        required_dictionary = (
            'Source: "..\\data\\tts_pronunciations.json"; '
            'DestDir: "{app}\\data"; Flags: ignoreversion'
        )
        for name in ("patch.iss", "installer.iss"):
            with self.subTest(manifest=name):
                manifest = (ROOT / "packaging" / name).read_text(encoding="utf-8-sig")
                self.assertIn(required_dictionary, manifest)


if __name__ == "__main__":
    unittest.main()
