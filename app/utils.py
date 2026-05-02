import hashlib
import io
import os
import random
import shutil
import string
import zipfile
from pathlib import Path

import bcrypt
import qrcode

_default_uploads = "/data/uploads" if Path("/data").exists() else str(Path(__file__).parent.parent / "uploads")
UPLOAD_DIR = Path(os.getenv("DROPSITE_UPLOADS", _default_uploads))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB

ADJECTIVES = [
    "swift", "bright", "cool", "bold", "calm", "fresh", "keen", "warm",
    "wild", "pure", "fair", "glad", "kind", "neat", "safe", "wise",
    "deep", "fast", "fine", "free", "good", "high", "long", "nice",
    "open", "real", "rich", "soft", "sure", "tall", "true", "vast",
]
NOUNS = [
    "wave", "star", "moon", "leaf", "tree", "bird", "lake", "rock",
    "hill", "dawn", "dusk", "rain", "snow", "wind", "fire", "rose",
    "reef", "peak", "vale", "cove", "pine", "fern", "hawk", "lynx",
    "fox", "elk", "owl", "bay", "sky", "sun", "gem", "oak",
]


def generate_slug() -> str:
    adj = random.choice(ADJECTIVES)
    noun = random.choice(NOUNS)
    num = random.randint(10, 99)
    return f"{adj}-{noun}-{num}"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def generate_qr_code(url: str) -> bytes:
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#6C63FF", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def extract_upload(file_bytes: bytes, filename: str, slug: str) -> Path:
    site_dir = UPLOAD_DIR / slug
    site_dir.mkdir(parents=True, exist_ok=True)

    if filename.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            zf.extractall(site_dir)
        # If zip contains a single directory, move its contents up
        contents = list(site_dir.iterdir())
        if len(contents) == 1 and contents[0].is_dir():
            inner = contents[0]
            for item in inner.iterdir():
                shutil.move(str(item), str(site_dir / item.name))
            inner.rmdir()
    else:
        # Single file upload — write it
        ext = Path(filename).suffix.lower()
        if ext in (".html", ".htm"):
            dest = site_dir / "index.html"
        else:
            dest = site_dir / filename
        dest.write_bytes(file_bytes)

    return site_dir


def get_file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def cleanup_site(slug: str) -> None:
    site_dir = UPLOAD_DIR / slug
    if site_dir.exists():
        shutil.rmtree(site_dir)
