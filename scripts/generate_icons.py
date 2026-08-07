"""Render TapTap's PNG and Windows ICO assets from the SVG master."""

from io import BytesIO
from pathlib import Path

from PIL import Image

try:
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
except ImportError as exc:  # pragma: no cover - depends on developer tooling
    raise SystemExit(
        "PySide6 is required to regenerate icons. Install the project's "
        "Linux dependencies or run: python -m pip install PySide6"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
# PACKAGING: Edit only the SVG master, then regenerate both tracked outputs.
SVG_SOURCE = STATIC / "app-icon.svg"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_frame(renderer: QSvgRenderer, size: int) -> Image.Image:
    """Render one size directly so small ICO frames never inherit resize artifacts."""
    canvas = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()

    encoded = QBuffer()
    encoded.open(QIODevice.OpenModeFlag.WriteOnly)
    if not canvas.save(encoded, "PNG"):
        raise RuntimeError(f"Could not render the {size}x{size} icon frame")
    with Image.open(BytesIO(bytes(encoded.data()))) as image:
        return image.convert("RGBA").copy()


def main() -> None:
    renderer = QSvgRenderer(str(SVG_SOURCE))
    if not renderer.isValid():
        raise SystemExit(f"Invalid SVG master: {SVG_SOURCE}")

    frames = [render_frame(renderer, size) for size in ICON_SIZES]
    frames[-1].save(STATIC / "app-icon.png", format="PNG", optimize=True)
    frames[-1].save(
        STATIC / "app-icon.ico",
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
        append_images=frames[:-1],
    )


if __name__ == "__main__":
    main()
