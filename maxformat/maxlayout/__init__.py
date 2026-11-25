"""maxlayout - make Max/MSP patches look like circuit boards.

Modules:
  cli      the maxformat.py command
  lint     score tidiness; verify behaviour didn't change
  layout   place boxes (tidy / full)
  route    right-angle cord routing
  style    net colours and board themes
  present  presentation-mode layout
  build    new patch from a JSON spec
  render   SVG/PNG preview
  maxpat   .maxpat reading/writing and Max's port geometry
"""
__version__ = '0.1.0'
