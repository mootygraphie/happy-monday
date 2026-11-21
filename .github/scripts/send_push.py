"""Notifications push Happy Monday (lancé toutes les 10 min par la GitHub Action « push »).

Abonnements : pushSubs/{joueur}/{id} = {endpoint, keys{p256dh, auth}} (écrits par le site).
Déjà envoyé  : pushSent/... (pour ne jamais envoyer deux fois la même chose).
Notifications :
  - sondage ouvert                  → tous les abonnés, une fois par lundi
  - « plus que 3 h » (dès 18h30)    → abonnés qui n'ont pas répondu, une fois
  - booster disponible (dès 10h)    → abonnés qui ont au moins 1 booster et n'en ont ouvert aucun aujourd'hui, une fois par jour
  - proposition d'échange reçue     → le destinataire, une fois par proposition
  - message d'un super admin        → pushQueue/{id} = {by, title, body, to: joueur | [joueurs] | "__all"}
  - Le Vestiaire (chat)              → membres du salon abonnés, sauf salon coupé (chat/mute) ; une @mention passe toujours
                                       (bêta : super admins seulement tant que config/chatOpen n'est pas à true)
  + message « sondage fermé » posté dans le Vestiaire général à la clôture
Préférences : pushPrefs/{joueur} = {trades: false, boosters: false} pour les couper
(le sondage ne peut pas être coupé).
"""
import datetime, json, os, sys, tempfile, time, urllib.request, zoneinfo

DB = "https://happy-monday-a2404-default-rtdb.europe-west1.firebasedatabase.app"
SITE = "https://mootygraphie.github.io/happy-monday/"
VAPID_SUB = "https://mootygraphie.github.io"  # identifiant de l'expéditeur : adresse web SANS chemin (exigé par py_vapid)
PARIS = zoneinfo.ZoneInfo("Europe/Paris")
DRY = os.environ.get("DRY_RUN", "").strip() in ("1", "true", "oui")
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def req(path, method="GET", value=None):
    data = None if value is None else json.dumps(value).encode()
    r = urllib.request.Request(f"{DB}/{path}.json", data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.load(resp)


def mark(path):
    if DRY:
        print("  (simulation) marquer", path)
    else:
        req(path, "PUT", True)


def main():
    subs = req("pushSubs") or {}
    test = os.environ.get("TEST_PLAYER", "").strip()
    key = os.environ.get("VAPID_PRIVATE_KEY", "")
    if not key and not DRY:
        sys.exit("VAPID_PRIVATE_KEY manquant")
    keyfile = None
    if key:
        f = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
        f.write(key)
        f.close()
        keyfile = f.name
    from pywebpush import webpush, WebPushException

    stats = {"envoyées": 0, "échecs": 0, "abonnements expirés": 0}

    def send(player, title, body, url, tag):
        """Renvoie True si c'est « réglé » (au moins un envoi réussi, ou plus aucun abonnement valide)."""
        ok = False
        items = list((subs.get(player) or {}).items())
        expired = 0
        for sid, s in items:
            if DRY:
                print(f"  (simulation) → {player} : {title} · {body}")
                ok = True
                continue
            try:
                webpush(subscription_info={"endpoint": s["endpoint"], "keys": s["keys"]},
                        data=json.dumps({"title": title, "body": body, "url": url, "tag": tag}),
                        vapid_private_key=keyfile, vapid_claims={"sub": VAPID_SUB}, ttl=6 * 3600)
                stats["envoyées"] += 1
                ok = True
            except WebPushException as e:
                code = getattr(e.response, "status_code", None)
                if code in (404, 410):  # abonnement expiré ou révoqué : on le supprime
                    req(f"pushSubs/{player}/{sid}", "DELETE")
                    stats["abonnements expirés"] += 1
                    expired += 1
                else:
                    stats["échecs"] += 1
                    print("échec", player, code, str(e)[:200])
            except Exception as e:  # une erreur sur un abonnement n'arrête pas les autres envois
                stats["échecs"] += 1
                print("échec", player, type(e).__name__, str(e)[:200])
        return ok or (items and expired == len(items))

    if test:
        if test not in subs:
            sys.exit(f"Aucun abonnement pour « {test} ». Active d'abord les notifications dans le menu ☰ de l'appli.")
        send(test, "✅ Notifications activées", "Tu recevras ici les rappels du Happy Monday.", "./", "test")
        print(stats)
        return

    if not subs:
        print("Aucun abonné pour l'instant.")
        return
    prefs = req("pushPrefs") or {}
    allowed = lambda p, k: (prefs.get(p) or {}).get(k) is not False

    # ── messages des super admins (file d'attente)
    queue = req("pushQueue") or {}
    supers = req("config/superAdmins") or {}
    for qid, q in queue.items():
        if not isinstance(q, dict) or q.get("sentAt"):
            continue
        if supers.get(q.get("by")) is not True:
            print("Demande ignorée (pas super admin) :", qid, q.get("by"))
            if not DRY:
                req(f"pushQueue/{qid}", "PATCH", {"sentAt": int(time.time() * 1000), "count": 0, "refused": True})
            continue
        to = q.get("to")
        if to == "__all":
            targets = list(subs)
        elif isinstance(to, (list, dict)):  # plusieurs joueurs choisis
            targets = [t for t in (to.values() if isinstance(to, dict) else to) if t in subs]
        else:
            targets = [to] if to in subs else []
        before = stats["envoyées"]
        print("Message de", q.get("by"), "→", q.get("to"), ":", q.get("title"))
        results = [send(p, (q.get("title") or "Happy Monday")[:60], (q.get("body") or "")[:200], "./", f"msg-{qid}") for p in targets]
        tries = (q.get("tries") or 0) + 1
        if not DRY and (any(results) or not targets or tries >= 6):  # réessaie aux passages suivants (6 fois max)
            req(f"pushQueue/{qid}", "PATCH", {"sentAt": int(time.time() * 1000), "count": stats["envoyées"] - before})
        elif not DRY:
            req(f"pushQueue/{qid}", "PATCH", {"tries": tries})

    sent = req("pushSent") or {}
    now = datetime.datetime.now(PARIS)
    now_ms = time.time() * 1000
    today = now.date()
    monday = today if today.weekday() == 0 else today + datetime.timedelta(days=(7 - today.weekday()))
    mon_iso = monday.isoformat()
    mon_lbl = f"lundi {monday.day if monday.day > 1 else '1er'} {MOIS[monday.month - 1]}"

    # ── vote de présence
    vote = req(f"sessionVotes/{mon_iso}")
    if vote and vote.get("status") == "open" and now_ms < (vote.get("closesAt") or 0):
        votes = vote.get("votes") or {}
        if not (sent.get("open") or {}).get(mon_iso):
            print("Sondage ouvert :", mon_iso)
            results = [send(p, "🗓️ Le sondage est ouvert", f"Tu viens {mon_lbl} ? Réponds avant dimanche 21h30.", "./?n=vote#vote", f"open-{mon_iso}") for p in subs]
            if any(results):
                mark(f"pushSent/open/{mon_iso}")
        if vote["closesAt"] - now_ms <= 3 * 3600 * 1000:
            done = (sent.get("rem3h") or {}).get(mon_iso) or {}
            for p in subs:
                if p not in votes and not done.get(p):
                    print("Rappel 3 h :", p)
                    if send(p, "⏳ Plus que 3 h pour répondre", f"Le sondage pour {mon_lbl} se ferme ce soir à 21h30.", "./?n=vote#vote", f"rem-{mon_iso}"):
                        mark(f"pushSent/rem3h/{mon_iso}/{p}")

    # ── clôture du sondage : message automatique dans le Vestiaire général
    if vote and vote.get("status") == "open" and (vote.get("closesAt") or 0) <= now_ms < (vote.get("closesAt") or 0) + 86400 * 1000 \
            and not (sent.get("chatClose") or {}).get(mon_iso):
        votes = vote.get("votes") or {}
        n = sum(1 for v in votes.values() if v == "P") + len(vote.get("plus") or {})
        print("Vestiaire : sondage fermé", mon_iso, n)
        if not DRY:
            req("chat/msgs/g", "POST", {"sys": "close", "text": f"Le sondage est fermé : {n} présent{'s' if n > 1 else ''} {mon_lbl}.", "at": {".sv": "timestamp"}})
        mark(f"pushSent/chatClose/{mon_iso}")

    # ── Le Vestiaire : nouveaux messages
    try:
        chat_push(subs, supers, sent, now_ms, send)
    except Exception as e:
        print("Vestiaire : erreur", type(e).__name__, str(e)[:200])

    # ── vignettes : boosters et échanges (saison la plus récente)
    with urllib.request.urlopen(f"{DB}/vignettes.json?shallow=true", timeout=30) as resp:
        seasons = sorted(k for k in (json.load(resp) or {}) if "-" in k)
    if seasons:
        season = seasons[-1]
        V = req(f"vignettes/{season}") or {}
        day = (today - datetime.date(1970, 1, 1)).days
        if now.hour >= 10:
            done = (sent.get("boost") or {}).get(today.isoformat()) or {}
            midnight = datetime.datetime.combine(today, datetime.time(0, 0), PARIS).timestamp() * 1000
            logs = req(f"vignettesLog/{season}") or {}
            for p in subs:
                b = (V.get(p) or {}).get("boosters")
                if not b or done.get(p) or not allowed(p, "boosters"):
                    continue
                # déjà ouvert un booster aujourd'hui (journal des ouvertures, hors échanges) : pas de rappel
                if any(isinstance(e, dict) and e.get("cards") and (e.get("at") or 0) >= midnight for e in (logs.get(p) or {}).values()):
                    continue
                stock = min(5, (b.get("stock") or 0) + max(0, day - (b.get("day") or day)))
                if stock > 0:
                    print("Boosters :", p, stock)
                    if send(p, "🎁 " + ("Un booster t'attend" if stock == 1 else f"{stock} boosters t'attendent"), "Ouvre-les dans Vignettes.", "./?n=vg#vignettes", f"boost-{today.isoformat()}"):
                        mark(f"pushSent/boost/{today.isoformat()}/{p}")
        done = sent.get("trade") or {}
        for tid, t in (V.get("_trades") or {}).items():
            if t.get("status") != "wait" or done.get(tid) or t.get("to") not in subs or not allowed(t.get("to"), "trades"):
                continue
            if now_ms > (t.get("at") or 0) + 3 * 86400 * 1000:
                continue
            gift = not t.get("get")
            print("Échange :", tid, t.get("from"), "→", t.get("to"))
            if send(t["to"], ("🎁 " if gift else "🔄 ") + (f"{t.get('from')} t'offre un cadeau" if gift else f"{t.get('from')} te propose un échange"),
                    "À voir dans Vignettes · Échanges.", "./?n=vg#vignettes", f"trade-{tid}"):
                mark(f"pushSent/trade/{tid}")
    print(stats)


def chat_push(subs, supers, sent, now_ms, send):
    """Un push par salon et par personne, regroupant les messages arrivés depuis le dernier passage."""
    import re
    open_all = req("config/chatOpen") is True
    rooms = req("chat/rooms") or {}
    rooms.setdefault("g", {})
    reads = req("chat/read") or {}
    mutes = req("chat/mute") or {}
    done = sent.get("chat") or {}
    allowed_people = set(subs) if open_all else {p for p in subs if supers.get(p) is True}
    window = now_ms - 2 * 3600 * 1000  # rien de plus vieux que 2 h (premier passage, panne de l'Action)
    for rid, room in rooms.items():
        if not isinstance(room, dict):
            continue
        gen = rid == "g"
        if not gen and not room.get("name"):
            continue
        name = "Le Vestiaire" if gen else room.get("name")
        members = allowed_people if gen else {p for p in (room.get("members") or {}) if p in allowed_people}
        if not members:
            continue
        with urllib.request.urlopen(f"{DB}/chat/msgs/{rid}.json?orderBy=%22$key%22&limitToLast=30", timeout=30) as resp:
            msgs = json.load(resp) or {}
        L = sorted(((k, m) for k, m in msgs.items() if isinstance(m, dict)), key=lambda x: x[0])
        for p in members:
            since = max(window, (done.get(p) or {}).get(rid) or 0, (reads.get(p) or {}).get(rid) or 0, (reads.get(p) or {}).get("_init") or 0)
            new = [m for _, m in L if (m.get("at") or 0) > since and m.get("by") != p and m.get("sys") not in ("open", "club")]
            if not new:
                continue
            top = max(m.get("at") or 0 for m in new)
            ment = [m for m in new if not m.get("sys") and re.search("@" + re.escape(p) + r"(?![\wÀ-ÿ])", m.get("text") or "")]
            if (mutes.get(p) or {}).get(rid) and not ment:
                mark_val(f"pushSent/chat/{p}/{rid}", top)
                continue
            last = ment[-1] if ment else new[-1]
            line = (last.get("text") or "") if last.get("sys") else f"{last.get('by')} : {last.get('text') or ''}"
            if ment:
                title = f"@ {ment[-1].get('by')} t'a mentionné · {name}"
            else:
                title = f"💬 {name}" + (f" · {len(new)} nouveaux messages" if len(new) > 1 else "")
            print("Vestiaire :", p, rid, len(new), "mention" if ment else "")
            if send(p, title[:60], line[:180], f"./?n=chat&r={rid}", f"chat-{rid}"):
                mark_val(f"pushSent/chat/{p}/{rid}", top)


def mark_val(path, value):
    if DRY:
        print("  (simulation) marquer", path, value)
    else:
        req(path, "PUT", value)


if __name__ == "__main__":
    main()
