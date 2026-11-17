"""Reduit les portraits trop grands du dossier portraits/ a 600 px de large.

Le format et le nom du fichier sont conserves (Boris.png reste Boris.png), seule la taille
change. Les images deja a 600 px ou moins ne sont pas touchees, donc le script peut tourner
a chaque depot sans rien refaire. Lance par .github/workflows/portraits.yml, ou a la main :
    python3 .github/scripts/resize_portraits.py
"""
from pathlib import Path

from PIL import Image, ImageOps

MAX_WIDTH = 600
FOLDER = Path(__file__).resolve().parents[2] / "portraits"
EXTS = {".png", ".jpeg", ".jpg", ".webp"}

changed = []
for path in sorted(FOLDER.iterdir()):
    if path.suffix.lower() not in EXTS:
        continue
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)  # photos de telephone : remet dans le bon sens
        if im.width <= MAX_WIDTH:
            continue
        before = path.stat().st_size
        height = round(im.height * MAX_WIDTH / im.width)
        small = im.resize((MAX_WIDTH, height), Image.LANCZOS)
        ext = path.suffix.lower()
        if ext in (".jpeg", ".jpg"):
            small.convert("RGB").save(path, "JPEG", quality=82, optimize=True, progressive=True)
        elif ext == ".webp":
            small.save(path, "WEBP", quality=82)
        else:
            small.save(path, "PNG", optimize=True)
    changed.append(f"{path.name}: {before // 1024} Ko -> {path.stat().st_size // 1024} Ko")

print("\n".join(changed) if changed else "Aucun portrait a reduire.")
