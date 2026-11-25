import unittest

from tests.helpers import FM_SPEC
from maxlayout import build, lint
from maxlayout.maxpat import Patcher, read_json


class Build(unittest.TestCase):
    def setUp(self):
        self.doc, self.ids = build.build(read_json(FM_SPEC))

    def test_follows_the_house_style(self):
        res = lint.lint_doc(self.doc, presentation=True, min_gap=build.STYLE['min_gap'])
        self.assertGreaterEqual(res['score'], 85)
        for k in ('diagonal', 'jog', 'through_box', 'box_overlap', 'too_close', 'upward'):
            self.assertEqual(res['totals'][k], 0, k)

    def test_no_panels_and_no_colours(self):
        P = Patcher(self.doc['patcher'])
        self.assertFalse([b for b in P.boxes if b.maxclass == 'panel'])
        self.assertFalse([l for l in P.lines if 'color' in l.d])
        self.assertNotIn('editing_bgcolor', self.doc['patcher'])

    def test_modules_are_columns_left_to_right(self):
        P = Patcher(self.doc['patcher'])
        titles = sorted((b.rect[0], b.text) for b in P.boxes
                        if b.maxclass == 'comment' and b.d.get('fontface') == 1 and b.text.isupper())
        self.assertEqual([t for _, t in titles], ['CONTROLS', 'MODULATOR', 'CARRIER', 'OUTPUT'])

    def test_always_has_a_presentation_view(self):
        self.assertEqual(self.doc['patcher']['openinpresentation'], 1)

    def test_presentation_without_controls_shows_title_and_version(self):
        doc, _ = build.build({'title': 'Plain', 'version': '2.1',
                              'objects': [{'id': 'a', 'text': 'cycle~'}, {'id': 'b', 'text': 'dac~'}],
                              'cords': ['a:0 -> b:0']})
        shown = [b['box']['text'] for b in doc['patcher']['boxes'] if b['box'].get('presentation')]
        self.assertEqual(shown, ['Plain', 'v2.1'])

    def test_every_cord_uses_a_real_port(self):
        P = Patcher(self.doc['patcher'])
        for ln in P.lines:
            self.assertLess(ln.src[1], P.by_id[ln.src[0]].nout)
            self.assertLess(ln.dst[1], P.by_id[ln.dst[0]].nin)

    def test_presentation_controls_are_placed(self):
        P = Patcher(self.doc['patcher'])
        shown = [b for b in P.boxes if b.d.get('presentation') and b.maxclass.startswith('live.')]
        self.assertEqual(len(shown), 4)

    def test_rejects_audio_into_a_message_inlet(self):
        spec = {'objects': [{'id': 'a', 'text': 'cycle~ 440'}, {'id': 'b', 'text': '+ 60'}],
                'cords': ['a:0 -> b:0']}
        with self.assertRaises(SystemExit) as cm:
            build.build(spec)
        self.assertIn('audio', str(cm.exception))

    def test_allows_numbers_into_signal_float_inlets(self):
        spec = {'objects': [{'id': 'm', 'text': 'mtof'}, {'id': 'c', 'text': 'cycle~'}],
                'cords': ['m:0 -> c:0']}
        build.build(spec)

    def test_rejects_a_cord_to_a_missing_port(self):
        spec = {'objects': [{'id': 'a', 'text': 'cycle~'}, {'id': 'b', 'text': 'dac~'}],
                'cords': ['a:3 -> b:0']}
        with self.assertRaises(SystemExit):
            build.build(spec)


if __name__ == '__main__':
    unittest.main()
