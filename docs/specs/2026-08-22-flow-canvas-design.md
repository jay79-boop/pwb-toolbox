# Flow Canvas — design spec (2026-08-22)

A clean-room redesign of the workflow-canvas idea from puzzleapp.io, built as a
single self-contained HTML file at `static/flow-canvas.html`. It opens from
`file://` with no server, no build step, and no network — same pattern as the
trade journal and `static/karaoke-box.html`.

## Purpose

Three at once, per the owner: a working process-mapping tool, a design
reference, and a copy-anywhere template. Scope is the canvas plus one info
panel — no chat sidebar, no top nav, none of the original's clutter.

## Visual system

Two themes, toggled from the toolbar and persisted:

- **Paper** (light): warm white `#FAFAF8` canvas with a dot grid, white cards,
  curved bezier connectors. For sharing and daylight.
- **Slate** (dark): graphite `#14161D` canvas with a line grid, dark cards,
  right-angle elbow connectors. For daily use next to terminals.

Shared language in both: status as a colored bar on the card (left edge in
Paper, top strip in Slate) plus a text pill — Draft gray, Working amber,
Testing cyan, Live green. Owner chip labels Person (blue), AI (violet),
Automation (teal). Decision steps get a badge, not a diamond. Branch edges can
carry a short label.

## Mechanics

- Double-click empty canvas, or the ＋ Step button, adds a step.
- Click a step to select it; the panel becomes its editor (title, status,
  owner, duration, decision flag, notes).
- Drag cards to rearrange; drag from a card's right-edge port onto another
  card to connect them. Click a connector to select it (label, delete).
- **Tidy** runs a left-to-right layered auto-layout; dragging overrides it.
- **Deletion is deliberately hard to do by accident**: no Delete-key binding;
  delete lives only in the editor panel and asks for confirmation; Ctrl+Z
  undoes the last 50 actions including deletes.
- Every change auto-saves to `localStorage`. Export/Import JSON moves a flow
  between machines (localStorage is per-browser, same caveat as the journal).

## Non-goals (v1)

Swimlanes, multiple pages, zoom, collaboration, AI anything. The layout math
is small enough that no separate tested module is split out yet; if it grows,
follow the `option-lab.js` pattern.
