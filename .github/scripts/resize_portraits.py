"""Reduit les portraits trop grands du dossier portraits/ a 900 px de large.

Le format et le nom du fichier sont conserves (Boris.png reste Boris.png), seule la taille
change. Les images deja a 900 px ou moins ne sont pas touchees, donc le script peut tourner
a chaque depot sans rien refaire. Lance par .github/workflows/portraits.yml, ou a la main :
    python3 .github/scripts/resize_portraits.py
"""
from pathlib import Path

from PIL import Image, ImageOps

MAX_WIDTH = 900
FOLDER = Path(__file__).resolve().parents[2] / "portraits"
EXTS = {".png", ".jpeg", ".jpg", ".webp"}

import unicodedata

changed = []
# Noms de fichiers : accents en forme composee (NFC). Certains telephones et Mac envoient « é » en deux
# morceaux (e + accent) : meme nom a l'ecran, mais le site ne trouve plus la photo.
for path in sorted(FOLDER.iterdir()):
    nfc = unicodedata.normalize("NFC", path.name)
    if nfc != path.name:
        target = path.with_name(nfc)
        if target.exists():
            target.unlink()  # une ancienne version au bon nom : la nouvelle la remplace
        path.rename(target)
        changed.append(f"{nfc} : nom corrige (accents)")
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
            small.convert("RGB").save(path, "JPEG", quality=85, optimize=True, progressive=True)
        elif ext == ".webp":
            small.save(path, "WEBP", quality=85)
        else:
            small.save(path, "PNG", optimize=True)
    changed.append(f"{path.name}: {before // 1024} Ko -> {path.stat().st_size // 1024} Ko")

print("\n".join(changed) if changed else "Aucun portrait a reduire.")
