"""The formatter must never change what a patch does."""
import unittest

from tests.helpers import messy, fresh
from maxlayout import layout, lint, route
from maxlayout.maxpat import walk_patchers, Patcher


def find(doc, text):
    for b in doc['patcher']['boxes']:
        if b['box'].get('text') == text:
            return b['box']
    raise KeyError(text)


class BehaviourCheck(unittest.TestCase):
    def test_full_layout_refuses_to_reorder_a_fanout(self):
        doc = messy()
        problems, _ = layout.layout_doc(doc, 'full', fanout='keep')
        self.assertTrue(problems)

    def test_trigger_insertion_preserves_behaviour(self):
        orig = messy()
        doc = fresh(orig)
        problems, triggers = layout.layout_doc(doc, 'full', fanout='trigger')
        self.assertEqual(problems, [])
        self.assertGreater(triggers, 0)
        beh = lint.lint_doc(doc, before=orig)['behaviour']
        self.assertFalse(any(beh.values()), beh)

    def test_tidy_preserves_behaviour(self):
        orig = messy()
        doc = fresh(orig)
        problems, _ = layout.layout_doc(doc, 'tidy')
        self.assertEqual(problems, [])
        for P in walk_patchers(doc['patcher']):
            route.route_patcher(P)
        self.assertFalse(any(lint.lint_doc(doc, before=orig)['behaviour'].values()))

    def test_detects_a_lost_cord(self):
        orig = messy()
        doc = fresh(orig)
        doc['patcher']['lines'].pop(0)
        self.assertTrue(lint.lint_doc(doc, before=orig)['behaviour']['lost_cords'])

    def test_detects_a_changed_fanout_order(self):
        orig = messy()
        doc = fresh(orig)
        # metro feeds [random 12] and the envelope message; swap their x positions
        a, b = find(doc, 'random 12'), find(doc, '1 5, 0 220')
        a['patching_rect'][0], b['patching_rect'][0] = b['patching_rect'][0], a['patching_rect'][0]
        self.assertTrue(lint.lint_doc(doc, before=orig)['behaviour']['fanout_order'])

    def test_detects_reordered_subpatcher_inlets(self):
        orig = messy()
        doc = fresh(orig)
        sub = find(doc, 'p delay')['patcher']
        sub['boxes'].append({'box': {'id': 'obj-99', 'maxclass': 'inlet', 'index': 2, 'comment': '',
                                     'numinlets': 0, 'numoutlets': 1, 'outlettype': [''],
                                     'patching_rect': [900, 10, 30, 30]}})
        orig2 = fresh(doc)
        ins = [b['box'] for b in sub['boxes'] if b['box']['maxclass'] == 'inlet']
        ins[0]['patching_rect'][0], ins[1]['patching_rect'][0] = ins[1]['patching_rect'][0], ins[0]['patching_rect'][0]
        self.assertTrue(lint.lint_doc(doc, before=orig2)['behaviour']['io_order'])

    def test_inserted_trigger_uses_a_max_style_id(self):
        import re
        doc = messy()
        _, added = layout.layout_doc(doc, 'full', fanout='trigger')
        self.assertGreater(added, 0)
        ids = [b['box']['id'] for b in doc['patcher']['boxes']]
        self.assertTrue(all(re.match(r'^obj-\d+$', i) for i in ids), ids)
        self.assertEqual(len(ids), len(set(ids)))
        t = next(b['box'] for b in doc['patcher']['boxes'] if b['box'].get('text', '').startswith('t '))
        lines = [l['patchline'] for l in doc['patcher']['lines']]
        self.assertEqual(sum(1 for l in lines if l['destination'][0] == t['id']), 1)
        self.assertEqual(sum(1 for l in lines if l['source'][0] == t['id']), t['numoutlets'])

    def test_inserted_triggers_use_real_max_types(self):
        doc = messy()
        layout.layout_doc(doc, 'flow', fanout='trigger')
        ts = [b['box']['text'] for b in doc['patcher']['boxes'] if b['box'].get('text', '').startswith('t ')]
        self.assertTrue(ts)
        for t in ts:
            self.assertTrue(all(a in 'bifls' for a in t.split()[1:]), t)
        self.assertIn('t f f f', ts)      # mtof outputs floats

    def test_unknown_data_types_are_not_guessed(self):
        from maxlayout.maxpat import trigger_letter, guess_ports
        box = lambda t: {'maxclass': 'newobj', 'text': t, 'outlettype': guess_ports(t)[2]}
        self.assertEqual(trigger_letter(box('mtof'), 0), 'f')
        self.assertEqual(trigger_letter(box('metro 100'), 0), 'b')
        self.assertEqual(trigger_letter(box('random 8'), 0), 'i')
        self.assertIsNone(trigger_letter(box('route foo'), 0))   # could be a bang or a list

    def test_flags_a_as_an_invalid_trigger_type(self):
        from maxlayout.maxpat import check_trigger
        self.assertEqual(check_trigger({'maxclass': 'newobj', 'text': 't a a'})[0], 'error')
        self.assertIsNone(check_trigger({'maxclass': 'newobj', 'text': 't b 0 stop'}))

    def test_detects_ids_max_cannot_load(self):
        orig = messy()
        doc = fresh(orig)
        doc['patcher']['boxes'][0]['box']['id'] = 'obj-x1'
        self.assertTrue(lint.lint_doc(doc, before=orig)['behaviour']['bad_ids'])

    def test_send_receive_replacement_is_the_same_connection(self):
        from maxlayout.maxpat import Patcher
        orig = messy()
        doc = fresh(orig)
        P = Patcher(doc['patcher'])
        ln = next(l for l in P.lines if P.by_id[l.src[0]].text == 'startwindow')
        self.assertEqual(layout.insert_sends(P, [ln]), 1)
        texts = [b['box'].get('text', '') for b in doc['patcher']['boxes']]
        self.assertIn('s #0-startwindow-dac', texts)
        self.assertIn('r #0-startwindow-dac', texts)
        beh = lint.lint_doc(doc, before=orig)['behaviour']
        self.assertFalse(any(beh.values()), beh)

    def test_object_text_is_never_changed(self):
        orig = messy()
        doc = fresh(orig)
        layout.layout_doc(doc, 'full', fanout='trigger')
        texts = lambda d: sorted(b['box'].get('text', '') for b in d['patcher']['boxes'])
        self.assertTrue(set(texts(orig)) <= set(texts(doc)))


if __name__ == '__main__':
    unittest.main()
