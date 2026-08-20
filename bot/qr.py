"""Local QR code generation (no external HTTP dependency)."""

import io

from qrcode import QRCode
from qrcode.constants import ERROR_CORRECT_L
from qrcode.image.pil import PilImage


def qr_bytes(data: str, size: int = 400) -> bytes:
    """Render `data` as a PNG QR code in memory (pure local, no network).

    `size` is the target pixel width/height of the rendered image.
    """
    qr = QRCode(border=2, error_correction=ERROR_CORRECT_L)
    qr.add_data(data)
    qr.make(fit=True)
    modules = len(qr.get_matrix())
    qr.box_size = max(1, size // modules)
    img: PilImage = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
