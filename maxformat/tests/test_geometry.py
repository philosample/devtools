import unittest

from tests.helpers import ROOT  # noqa: F401  (sets sys.path)
from maxlayout.maxpat import Box, pitch_width, guess_ports, PORT_INSET, PITCH


class PortGeometry(unittest.TestCase):
    def box(self, w, nin, nout):
        return Box({'id': 'b', 'patching_rect': [100, 50, w, 22], 'numinlets': nin, 'numoutlets': nout})

    def test_single_port_sits_9_5_from_left(self):
        self.assertEqual(self.box(60, 1, 1).inlet_xy(0), (109.5, 50))
        self.assertEqual(self.box(60, 1, 1).outlet_xy(0), (109.5, 72))

    def test_last_port_sits_9_5_from_right(self):
        b = self.box(80, 3, 1)
        self.assertAlmostEqual(b.inlet_xy(2)[0], 100 + 80 - PORT_INSET)

    def test_ports_evenly_spaced(self):
        b = self.box(79, 3, 1)
        xs = [b.inlet_xy(i)[0] for i in range(3)]
        self.assertAlmostEqual(xs[1] - xs[0], xs[2] - xs[1])

    def test_pitch_width_puts_every_port_on_the_pitch(self):
        for nin, nout in [(2, 1), (3, 2), (4, 1), (2, 5)]:
            w = pitch_width(70, nin, nout)
            self.assertGreaterEqual(w, 70)
            b = self.box(w, nin, nout)
            for i in range(nin):
                self.assertAlmostEqual(((b.inlet_xy(i)[0] - 100 - PORT_INSET) / PITCH) % 1, 0, places=6)


class PortGuesses(unittest.TestCase):
    def test_variadic_objects(self):
        self.assertEqual(guess_ports('t b b i')[:2], (1, 3))
        self.assertEqual(guess_ports('pack 0 0 0')[:2], (3, 1))
        self.assertEqual(guess_ports('dac~ 1 2 3 4')[:2], (4, 0))
        self.assertEqual(guess_ports('gate 4')[:2], (2, 4))

    def test_signal_objects_output_signal(self):
        self.assertEqual(guess_ports('cycle~ 440')[2], ['signal'])

    def test_attributes_are_not_arguments(self):
        self.assertEqual(guess_ports('t b b @foo 1')[:2], (1, 2))


if __name__ == '__main__':
    unittest.main()
