import copy, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EXAMPLES = os.path.join(ROOT, 'examples')
MESSY = os.path.join(EXAMPLES, 'messy-synth.maxpat')
FM_SPEC = os.path.join(EXAMPLES, 'fm-voice.spec.json')

from maxlayout.maxpat import load  # noqa: E402


def messy():
    return load(MESSY)


def fresh(doc):
    return copy.deepcopy(doc)
