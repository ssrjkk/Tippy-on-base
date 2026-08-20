"""Generate web/static/frame.png — the Farcaster Frame card image (1200x630).

Run once locally (dev-only; needs pillow, NOT part of the runtime
requirements):  python scripts/make_frame_image.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "web" / "static" / "frame.png"
W, H = 1200, 630


def main() -> None:
    img = Image.new("RGB", (W, H), (10, 12, 24))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), fill=(10, 12, 24))
    d.rectangle((60, 60, W - 60, H - 60), outline=(0, 122, 255), width=4)
    d.ellipse((W - 260, 60, W - 60, 260), fill=(0, 122, 255))
    d.polygon([(W - 210, 100), (W - 110, 220), (W - 210, 220)], fill=(255, 255, 255))
    d.text((110, 150), "Base TipBot", fill=(255, 255, 255), font=None, anchor="ls")
    d.text((110, 280), "USDC paywall on Base", fill=(160, 190, 230), font=None, anchor="ls")
    d.text((110, 380), "x402 for AI agents  |  TipBotVault Proof of Reserves", fill=(120, 140, 180), font=None, anchor="ls")
    img.save(OUT, "PNG")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
