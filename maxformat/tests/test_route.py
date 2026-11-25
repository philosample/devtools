import unittest

from tests.helpers import messy
from maxlayout import layout, lint, route
from maxlayout.maxpat import walk_patchers


def route_all(doc):
    for P in walk_patchers(doc['patcher']):
        route.route_patcher(P)


class Router(unittest.TestCase):
    def test_corners_drops_collinear_points(self):
        pts = [(0, 0), (0, 10), (0, 20), (30, 20), (30, 40)]
        self.assertEqual(route.corners(pts), [(0, 20), (30, 20)])

    def test_every_cord_becomes_right_angled(self):
        doc = messy()
        layout.layout_doc(doc, 'full', fanout='trigger')
        route_all(doc)
        t = lint.lint_doc(doc)['totals']
        self.assertEqual(t['diagonal'], 0)
        self.assertEqual(t['jog'], 0)
        self.assertEqual(t['through_box'], 0)
        self.assertEqual(t['overlap'], 0)

    def test_route_only_never_moves_boxes(self):
        doc = messy()
        before = [b['box']['patching_rect'] for b in doc['patcher']['boxes']]
        route_all(doc)
        self.assertEqual(before, [b['box']['patching_rect'] for b in doc['patcher']['boxes']])

    def test_formatting_improves_the_score(self):
        doc = messy()
        s0 = lint.lint_doc(doc)['score']
        layout.layout_doc(doc, 'full', fanout='trigger')
        route_all(doc)
        self.assertGreater(lint.lint_doc(doc)['score'], s0 + 50)


if __name__ == '__main__':
    unittest.main()
