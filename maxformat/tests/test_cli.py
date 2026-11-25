import os
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import ROOT, MESSY, FM_SPEC

def read_text(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


CMD = [sys.executable, os.path.join(ROOT, 'maxformat.py')]


def run(*args):
    return subprocess.run(CMD + list(args), capture_output=True, text=True)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, 'out.maxpat')

    def test_input_output(self):
        r = run(MESSY, self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(self.out))
        self.assertIn('✓ wrote', r.stdout)

    def test_check_writes_nothing(self):
        r = run(MESSY, '--check')
        self.assertEqual(r.returncode, 0)
        self.assertIn('tidiness score', r.stdout)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_output_is_required(self):
        self.assertEqual(run(MESSY).returncode, 2)

    def test_refusal_writes_nothing(self):
        r = run(MESSY, self.out, '--no-triggers')
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(self.out))
        self.assertIn('--no-triggers', r.stderr)

    def test_full_with_trigger_and_theme(self):
        r = run(MESSY, self.out, '--theme', 'pcb')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_default_puts_sources_above_what_they_feed(self):
        self.assertEqual(run(MESSY, self.out).returncode, 0)
        from maxlayout.maxpat import load, Patcher
        P = Patcher(load(self.out)['patcher'])
        for ln in P.lines:
            s, t = P.by_id[ln.src[0]], P.by_id[ln.dst[0]]
            self.assertLess(s.rect[1], t.rect[1], f"{s.text} should be above {t.text}")

    def test_keep_layout(self):
        self.assertEqual(run(MESSY, self.out, '--keep-layout').returncode, 0)

    def test_object_lookup(self):
        r = run('--object', 'cycle~')
        self.assertEqual(r.returncode, 0)
        self.assertIn('oscillator', r.stdout)
        self.assertIn('outlet 0: signal', r.stdout)

    def test_png_flag(self):
        r = run(MESSY, self.out, '--png')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('preview', r.stdout)

    def test_builds_from_spec(self):
        r = run(FM_SPEC, self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(self.out))

    def test_input_is_never_modified(self):
        before = read_text(MESSY)
        run(MESSY, self.out)
        self.assertEqual(before, read_text(MESSY))


if __name__ == '__main__':
    unittest.main()
