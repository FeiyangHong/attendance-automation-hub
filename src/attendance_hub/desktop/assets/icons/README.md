# Desktop icon candidates

- `c2`: C2 refined apricot schedule card.
- `d`: D white/blue linked endpoints on charcoal.
- `e`: E apricot two-period calendar.

Generated with the built-in ImageGen tool, selected by the user. Source PNGs are
retained here with their original alpha. Previews and multi-size ICO files are
format/size conversions only; no additional design changes were made.

Prompt briefs: C2 retains the apricot/charcoal/terracotta card, with balanced
negative-space slots and rounded corners; D uses two interlocking soft links to
represent remote endpoints; E uses two calendar rows to represent morning and
evening attendance. All three requested modern rounded flat geometry, clear
small-size silhouettes, transparent exterior, and no text or 3D decoration.

To regenerate the conversions (Pillow is a development-only dependency):

```powershell
python -m pip install Pillow
python scripts/release/prepare_icons.py
```

Runtime needs only Tkinter, not Pillow. Selection is stored outside package assets
in the Git-ignored `config/desktop_appearance.json`.
