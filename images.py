"""Foto-Upload: pruefen, drehen, verkleinern und als JPEG komprimieren.

Handyfotos sind oft 3-8 MB gross. Hier werden sie auf maximal 900 px Kantenlaenge
und ca. 60-150 KB verkleinert (aehnlich wie bildcomprim.py in der Buchhandlung),
bevor sie in der Datenbank landen.
"""
import io

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 900
JPEG_QUALITY = 82
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
Image.MAX_IMAGE_PIXELS = 50_000_000  # Schutz vor "Dekompressionsbomben"


class ImageError(ValueError):
    """Fehlermeldung, die dem Nutzer direkt angezeigt werden darf."""


def process_upload(file_storage) -> bytes:
    raw = file_storage.read()
    if not raw:
        raise ImageError("Die Bilddatei ist leer.")
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()  # prueft, ob die Datei wirklich ein Bild ist
        img = Image.open(io.BytesIO(raw))  # verify() macht das Objekt unbrauchbar
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageError("Diese Datei ist kein gültiges Bild (erlaubt: JPG, PNG, WebP).")

    if img.format not in ALLOWED_FORMATS:
        raise ImageError("Bitte ein JPG-, PNG- oder WebP-Bild hochladen.")

    img = ImageOps.exif_transpose(img)  # Handyfotos richtig herum drehen
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    else:
        img = img.convert("RGB")

    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return out.getvalue()
