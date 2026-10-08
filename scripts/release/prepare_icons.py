"""Convert selected source PNGs to Tk previews and multi-size Windows ICOs.

Development only: python -m pip install Pillow; python scripts/release/prepare_icons.py
No redesign or image-content edits: retain the generated colors and alpha channel.
"""

from pathlib import Path

from PIL import Image


ICON_DIR = (
    Path(__file__).resolve().parents[2]
    / "src/attendance_hub/desktop/assets/icons"
)


def main():
    for name in ("c2", "d", "e"):
        with Image.open(ICON_DIR / f"{name}.png") as original:
            image = original.convert("RGBA")
            image.resize((128, 128), Image.Resampling.LANCZOS).save(
                ICON_DIR / f"{name}-preview.png"
            )
            image.save(
                ICON_DIR / f"{name}.ico", format="ICO",
                sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)],
            )


if __name__ == "__main__":
    main()
