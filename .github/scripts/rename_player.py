"""Renomme un joueur partout dans la base (fiche, feuilles de match, statuts, vignettes, comptes...).

Lance par .github/workflows/rename.yml (a la main). Sans APPLY=1 : simulation, affiche ce qui
changerait sans rien ecrire. Remplace :
  - les valeurs texte egales a l'ancien nom (ex. "Chagot" -> "Cyril") ;
  - les cles egales a l'ancien nom (ex. presences/Chagot -> presences/Cyril) ;
  - les cles et valeurs de cartes « p:Chagot » -> « p:Cyril » (albums de vignettes).
Le texte libre (messages du Vestiaire, notifications deja envoyees) n'est pas touche.
"""
import json
import os
import sys
import time

import requests

DB = "https://happy-monday-a2404-default-rtdb.europe-west1.firebasedatabase.app"
SKIP = {"chat", "pushQueue", "pushDebug", "pushDead", "pushTest", "notifRead", "saisieLog", "authOrphans", "authDelete", "pwReset", "portraitQueue"}


def headers():
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    cred = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["FIREBASE_SA"]),
        scopes=["https://www.googleapis.com/auth/firebase.database", "https://www.googleapis.com/auth/userinfo.email"])
    cred.refresh(Request())
    return {"Authorization": "Bearer " + cred.token}


def main():
    old, new = os.environ["OLD"].strip(), os.environ["NEW"].strip()
    apply = os.environ.get("APPLY", "").strip() in ("1", "oui", "true")
    if not old or not new or old == new:
        sys.exit("Noms invalides.")
    h = headers()
    root = requests.get(f"{DB}/.json?shallow=true", headers=h, timeout=30).json() or {}
    total = 0
    for node in sorted(root):
        if node in SKIP:
            continue
        data = requests.get(f"{DB}/{node}.json", headers=h, timeout=60).json()
        hits = []

        def walk(v, path):
            if isinstance(v, dict):
                out = {}
                for k, x in v.items():
                    nk = new if k == old else ("p:" + new if k == "p:" + old else k)
                    if nk != k:
                        hits.append(f"cle {path}/{k}")
                        if nk in v:
                            print(f"  ATTENTION : {path}/{nk} existe deja, fusion impossible, laisse tel quel")
                            nk = k
                    out[nk] = walk(x, f"{path}/{k}")
                return out
            if isinstance(v, list):
                return [walk(x, f"{path}/{i}") for i, x in enumerate(v)]
            if v == old or v == "p:" + old:
                hits.append(f"valeur {path}")
                return new if v == old else "p:" + new
            return v

        res = walk(data, node)
        if hits:
            total += len(hits)
            print(f"{node} : {len(hits)} changement(s)")
            for x in hits[:8]:
                print("   ", x)
            if len(hits) > 8:
                print(f"    … et {len(hits) - 8} autres")
            if apply:
                r = requests.put(f"{DB}/{node}.json", headers=h, data=json.dumps(res), timeout=120)
                r.raise_for_status()
                print("   enregistre")
                time.sleep(1)
    print(f"Total : {total} changement(s) {'enregistres' if apply else '(simulation, rien ecrit)'}")


if __name__ == "__main__":
    main()
