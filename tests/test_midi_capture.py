from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_devtools.midi_capture import make_template, source_identity
from codex_devtools.midi_monitor import parse_document


class CaptureConfigurationTests(unittest.TestCase):
    def test_identity_ignores_display_name_and_empty_streams(self):
        self.assertEqual(source_identity({"portInputStream": [{"uniqueID": 1, "name": "old"}]}),
                         source_identity({"portInputStream": [{"uniqueID": 1, "name": "new"}], "spyingInputStream": []}))
        with self.assertRaises(ValueError):
            source_identity({"portInputStream": [{"name": "no identity"}]})
        with self.assertRaisesRegex(ValueError, "at least one valid portInputStream"):
            source_identity({"portOutputStream": [{"uniqueID": 2}]})
        with self.assertRaisesRegex(ValueError, "endpoint objects"):
            source_identity({"portInputStream": "bogus"})
        with self.assertRaisesRegex(ValueError, "integer uniqueID"):
            source_identity({"portInputStream": [{"uniqueID": True}]})
        with self.assertRaisesRegex(ValueError, "duplicate endpoint"):
            source_identity({"portInputStream": [{"uniqueID": 1}, {"uniqueID": 1}]})

    def test_template_resolves_only_unique_online_source(self):
        inventory = [{"direction": "source", "name": "Capture", "displayName": "IAC Capture", "uniqueID": 123, "offline": False}]
        with tempfile.TemporaryDirectory() as folder, patch("codex_devtools.midi_capture.endpoints", return_value=inventory):
            out = Path(folder) / "template.mmon"
            make_template(out, "IAC Capture")
            document = parse_document(out)
            self.assertEqual(document["events"], [])
            self.assertEqual(document["settings"]["streamSettings"]["portInputStream"][0]["uniqueID"], 123)
            with self.assertRaises(ValueError):
                make_template(out, "Capture")
            inventory.append(dict(inventory[0]))
            with self.assertRaises(ValueError):
                make_template(Path(folder) / "duplicate.mmon", "Capture")


if __name__ == "__main__":
    unittest.main()
