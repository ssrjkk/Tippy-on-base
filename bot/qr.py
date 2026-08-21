"""Local QR code generation (no external HTTP dependency)."""

import asyncio
import io

from qrcode import QRCode
from qrcode.constants import ERROR_CORRECT_L
from qrcode.image.pil import PilImage


def _qr_bytes_sync(data: str, size: int = 400) -> bytes:
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


async def qr_bytes(data: str, size: int = 400) -> bytes:
    """Async: render a QR code off the event loop (CPU-bound PIL work)."""
    return await asyncio.to_thread(_qr_bytes_sync, data, size)
