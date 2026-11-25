# Presentation mode - details

House rule: **every patch gets a presentation view and opens in it.**

Each box can have a second rectangle that's used only in presentation mode:

```json
"presentation": 1,
"presentation_rect": [x, y, w, h]
```
Only boxes with `presentation: 1` appear there. The patcher's
`"openinpresentation": 1` makes the patch open in presentation mode.

## Layout
- **Header:** patch title (bold, 18pt) top-left at (15, 15), with the version comment
  just to its right. If there are no controls, the header is the whole view; add
  any other identifying info that's available, such as author or a one-line purpose.
- **Sections mirror the patching columns,** in the same left-to-right order. Each
  section is its module's bold title comment, then that module's controls in a row,
  up to 6 per row before wrapping.
- **Spacing:** 15px between controls in a row, 45px between sections, 8px from a
  section title down to its controls. Snap to 7.5px.
- **Labels go under controls,** left-aligned with them, and need `presentation: 1`
  too. The patching-view label comment is reused; it just gets a
  `presentation_rect`. `live.dial`, `live.slider`, `live.gain~`, `live.text` and
  `live.tab` draw their own label (`parameter_shortname`), so they don't need one.
- **No panels.**
- **What to show:** what a performer touches or watches: dials, sliders, toggles,
  buttons, menus, number displays, meters, scopes. No object boxes or plumbing
  message boxes.
- **Max for Live devices** are 169px tall: keep sections on one row and check the
  total height.
