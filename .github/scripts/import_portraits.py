"""Range dans portraits/ les portraits envoyes depuis l'appli par les super admins.

L'appli depose chaque photo (deja reduite, en JPEG) dans la base : portraitQueue/{id} =
{name, data (data:image/jpeg;base64,...), by, at}. Ce script, lance toutes les 10 min par
.github/workflows/push.yml, ecrit portraits/{name}.jpeg (en remplacant les autres formats du meme
nom), met a jour la liste du Pantheon, enregistre sur GitHub, republie le site, puis note le
resultat dans la base (status: done / error). Seuls les envois des super admins sont acceptes.
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import requests

DB = "https://happy-monday-a2404-default-rtdb.europe-west1.firebasedatabase.app"
ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "portraits"
NAME_OK = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ0-9 '_.-]{1,40}$")


def headers():
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    cred = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["FIREBASE_SA"]),
        scopes=["https://www.googleapis.com/auth/firebase.database", "https://www.googleapis.com/auth/userinfo.email"])
    cred.refresh(Request())
    return {"Authorization": "Bearer " + cred.token}


def main():
    if not os.environ.get("FIREBASE_SA"):
        print("FIREBASE_SA manquant : rien a faire.")
        return
    h = headers()
    queue = requests.get(f"{DB}/portraitQueue.json", headers=h, timeout=20).json() or {}
    todo = {k: v for k, v in queue.items() if isinstance(v, dict) and v.get("data") and not v.get("status")}
    if not todo:
        print("Aucun portrait en attente.")
        return
    supers = requests.get(f"{DB}/config/superAdmins.json", headers=h, timeout=20).json() or {}
    done, results = [], {}
    for qid, q in todo.items():
        name = unicodedata.normalize("NFC", str(q.get("name") or "").strip())
        try:
            if supers.get(q.get("by")) is not True:
                raise ValueError("envoi refuse : pas super admin")
            if not NAME_OK.match(name) or name.startswith("."):
                raise ValueError(f"nom de fichier refuse : {name!r}")
            m = re.match(r"^data:image/(jpeg|jpg|png|webp);base64,(.+)$", q["data"], re.S)
            if not m:
                raise ValueError("format d'image non reconnu")
            raw = base64.b64decode(m.group(2))
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("image trop lourde (4 Mo max)")
            ext = "jpeg" if m.group(1) in ("jpeg", "jpg") else m.group(1)
            for old in FOLDER.glob("*"):
                if old.is_file() and unicodedata.normalize("NFC", old.stem) == name and old.suffix.lower() in (".jpeg", ".jpg", ".png", ".webp"):
                    old.unlink()  # l'ancienne version, quel que soit son format
            (FOLDER / f"{name}.{ext}").write_bytes(raw)
            done.append(name)
            results[qid] = {"status": "done", "file": f"{name}.{ext}", "doneAt": int(time.time() * 1000), "data": None}
            print("Portrait range :", f"{name}.{ext}", f"({len(raw) // 1024} Ko) par", q.get("by"))
        except Exception as e:
            results[qid] = {"status": "error", "err": str(e)[:120], "doneAt": int(time.time() * 1000), "data": None}
            print("Portrait refuse :", qid, e)
    if done:
        subprocess.run([sys.executable, str(ROOT / ".github/scripts/resize_portraits.py")], check=True)
        L = sorted([{"n": p.stem[9:], "f": p.stem} for p in FOLDER.iterdir() if p.name.lower().startswith("pantheon_") and p.suffix != ".json"], key=lambda x: x["n"].lower())
        (FOLDER / "pantheon.json").write_text(json.dumps(L, ensure_ascii=False))
        git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, check=True)
        git("config", "user.name", "github-actions[bot]")
        git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        git("add", "-A", "portraits")
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0:
            git("commit", "-m", "Portraits envoyes depuis l'appli : " + ", ".join(done))
            for i in range(4):
                if subprocess.run(["git", "pull", "--rebase", "-q", "origin", "main"], cwd=ROOT).returncode == 0 and subprocess.run(["git", "push"], cwd=ROOT).returncode == 0:
                    break
                time.sleep(5)
            else:
                raise SystemExit("Envoi sur GitHub impossible : on reessaiera au prochain passage.")
            subprocess.run(["gh", "api", "-X", "POST", f"repos/{os.environ.get('GITHUB_REPOSITORY', 'mootygraphie/happy-monday')}/pages/builds"], cwd=ROOT)
    for qid, r in results.items():
        requests.patch(f"{DB}/portraitQueue/{qid}.json", headers=h, data=json.dumps(r), timeout=20)


if __name__ == "__main__":
    main()
