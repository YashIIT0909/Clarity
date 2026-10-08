"""SYNTHETIC test fixtures for the screenshot flow: Instagram-style ad cards composed from real product photos we
already fetched. The shop handle is fictional; the price text is decoration (Clarity never reads prices from images).
Run: ../../../backend/.venv/bin/python make_ads.py [file ...]   (no names: all; named: only those, so a new ad never
rewrites a pinned one, whose cached Lens results are keyed by its bytes)"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
OUT = HERE.parent / "out"
ADS = {  # file -> (product photo, headline, price line)
    "ad_airpods_49.png": (OUT / "287595183543.jpg", "AirPods Pro 2", "FLASH SALE  $49  -  today only"),
    "ad_sony_xm5_329.png": (OUT / "sony-xm5_orig.jpg", "Sony WH-1000XM5", "$329  -  free shipping"),
    "ad_sony_xm5_59.png": (OUT / "sony-xm5_orig.jpg", "Sony WH-1000XM5", "CLEARANCE  $59  -  last units"),
    # India walk-away (2026-10-09). "Rs." as Indian ads write it: the default font has no ₹ glyph.
    "ad_sony_xm5_rs4999.png": (OUT / "sony-xm5_orig.jpg", "Sony WH-1000XM5", "MEGA SALE  Rs. 4,999  -  last units"),
    # India wait: a legit-looking shop asking ₹10,000 over Amazon.in's ₹19,989.
    "ad_sony_xm5_rs29990.png": (OUT / "sony-xm5_orig.jpg", "Sony WH-1000XM5", "Rs. 29,990  -  free delivery"),
}
font = lambda size: ImageFont.load_default(size=size)

for name, (photo, headline, price) in ADS.items():
    if sys.argv[1:] and name not in sys.argv[1:]:
        continue
    card = Image.new("RGB", (1080, 1350), "white")
    d = ImageDraw.Draw(card)
    d.ellipse((40, 40, 120, 120), fill="#d62976")
    d.text((140, 50), "example_gadget_shop", fill="black", font=font(34))
    d.text((140, 92), "Sponsored", fill="#777", font=font(26))
    img = Image.open(photo).convert("RGB")
    img.thumbnail((1000, 900))
    card.paste(img, ((1080 - img.width) // 2, 160 + (900 - img.height) // 2))
    d.text((40, 1100), headline, fill="black", font=font(60))
    d.text((40, 1180), price, fill="#c00", font=font(48))
    d.text((40, 1260), "Tap to shop", fill="#3897f0", font=font(34))
    card.save(HERE / name)
    print("wrote", HERE / name, card.size)
