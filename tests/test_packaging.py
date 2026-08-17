import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackagingManifestTests(unittest.TestCase):
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
