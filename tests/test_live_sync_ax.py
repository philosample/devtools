import unittest

from codex_devtools.live_sync_ax import compare_sync_observations, parse_sync_rows


def snapshot(rows, prefix=""):
    return "\n".join(
        f"{prefix}\t{i} checkbox Description: {name}: MIDI Output: Sync, "
        f"Value: {value}, Help: MIDI Output: Sync"
        for i, (name, value) in enumerate(rows, 1)
    )


class LiveSyncAXTests(unittest.TestCase):
    def test_full_and_diff_observations_exact_restore(self):
        before = snapshot([("Synth: USB", 1), ("Bus", 0)])
        disabled = snapshot([("Bus", 0), ("Synth: USB", 0)], "+")
        restored = snapshot([("Synth: USB", 1), ("Bus", 0)], "~")
        result = compare_sync_observations(before, disabled, restored, 2)
        self.assertTrue(result["valid"])
        self.assertEqual(result["originally_enabled"], ["Synth: USB"])
        self.assertEqual(len(set(result["snapshot_sha256"])), 3)

    def test_remaining_sync_and_unrestored_settings_fail(self):
        before = snapshot([("Synth", 1), ("Bus", 0)])
        off = snapshot([("Synth", 0), ("Bus", 0)])
        self.assertFalse(compare_sync_observations(before, before, before, 2)["valid"])
        self.assertFalse(compare_sync_observations(before, off, off, 2)["valid"])
        wrong = snapshot([("Synth", 1), ("Bus", 1)])
        self.assertFalse(compare_sync_observations(before, off, wrong, 2)["valid"])

    def test_missing_duplicate_renamed_and_malformed_rows_reject(self):
        valid = snapshot([("Synth", 0), ("Bus", 0)])
        invalid = [
            "",
            snapshot([("Synth", 0)]),
            snapshot([("Synth", 0), ("Synth", 0)]),
            valid.replace("Value: 0", "Value: 2"),
            valid.replace("checkbox Description", "checkbox (disabled) Description"),
            valid
            + "\n3 switch Description: Hidden: MIDI Output: Sync, Value: 1, Help: MIDI Output: Sync",
        ]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_sync_rows(text, 2)
        with self.assertRaises(ValueError):
            compare_sync_observations(valid, valid, valid.replace("Bus", "Other"), 2)
        for count in [0, -1, True]:
            with self.assertRaises(ValueError):
                parse_sync_rows(valid, count)

    def test_input_sync_and_help_prose_do_not_satisfy_output_inventory(self):
        text = "Help: MIDI Output: Sync\n3 checkbox Description: X: MIDI Input: Sync, Value: 1"
        with self.assertRaises(ValueError):
            parse_sync_rows(text, 1)


if __name__ == "__main__":
    unittest.main()
