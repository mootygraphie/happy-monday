"""Notifications push Happy Monday (lancé toutes les 10 min par la GitHub Action « push »).

Abonnements : pushSubs/{joueur}/{id} = {endpoint, keys{p256dh, auth}} (écrits par le site).
Déjà envoyé  : pushSent/... (pour ne jamais envoyer deux fois la même chose).
Notifications :
  - sondage ouvert                  → tous les abonnés, une fois par lundi
  - « plus que 3 h » (dès 18h30)    → abonnés qui n'ont pas répondu, une fois
  - booster disponible (10h-10h59) → abonnés qui ont au moins 1 booster et n'en ont ouvert aucun aujourd'hui, une fois par jour
                                       (passage de 10h raté : rien ce jour-là, jamais de booster le soir)
  - message d'un super admin        → pushQueue/{id} = {by, title, body, to: joueur | [joueurs] | "__all"}
  - Le Vestiaire (chat)              → membres du salon abonnés, sauf salon coupé (chat/mute) ; une @mention passe toujours
                                       (bêta : super admins seulement tant que config/chatOpen n'est pas à true)
  + message « sondage fermé » posté dans le Vestiaire général à la clôture
  - récap de la soirée prêt          → tous les abonnés, une fois, dès que la soirée est enregistrée (sessions/{date})
Préférences : pushPrefs/{joueur} = {boosters: false} pour les couper (le sondage ne peut pas être coupé).
Échanges de vignettes : plus de notification (retirée le 22/11/2026).
Plafond : 4 notifications automatiques par joueur et par jour (pushSent/cap/{jour}/{joueur}).
Robustesse : noms avec accents encodés dans les adresses ; une erreur dans un bloc n'arrête pas les autres
et ne fait jamais échouer l'Action (pas de mail d'erreur), elle est seulement écrite dans le journal.
"""
import datetime, json, os, sys, tempfile, time, traceback, urllib.parse, urllib.request, zoneinfo

DB = "https://happy-monday-a2404-default-rtdb.europe-west1.firebasedatabase.app"
SITE = "https://mootygraphie.github.io/happy-monday/"
VAPID_SUB = "https://mootygraphie.github.io"  # identifiant de l'expéditeur : adresse web SANS chemin (exigé par py_vapid)
PARIS = zoneinfo.ZoneInfo("Europe/Paris")
DRY = os.environ.get("DRY_RUN", "").strip() in ("1", "true", "oui")
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


_TOKEN = {"v": None, "exp": 0}


def auth_headers():
    """Jeton d'accès administrateur à la base (compte de service : secret FIREBASE_SA, ou fichier
    FIREBASE_SA_FILE en local). Indispensable depuis que les règles de la base sont verrouillées."""
    sa = os.environ.get("FIREBASE_SA", "").strip()
    path = os.environ.get("FIREBASE_SA_FILE", "").strip()
    if not sa and not path:
        return {}
    if not _TOKEN["v"] or time.time() > _TOKEN["exp"] - 300:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        scopes = ["https://www.googleapis.com/auth/firebase.database", "https://www.googleapis.com/auth/userinfo.email"]
        cred = (service_account.Credentials.from_service_account_info(json.loads(sa), scopes=scopes) if sa
                else service_account.Credentials.from_service_account_file(path, scopes=scopes))
        cred.refresh(Request())
        _TOKEN["v"], _TOKEN["exp"] = cred.token, time.time() + 3000
    return {"Authorization": "Bearer " + _TOKEN["v"]}


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=auth_headers()), timeout=30) as resp:
        return json.load(resp)


def req(path, method="GET", value=None):
    data = None if value is None else json.dumps(value).encode()
    r = urllib.request.Request(f"{DB}/{urllib.parse.quote(path, safe='/')}.json", data=data, method=method, headers={"Content-Type": "application/json", **auth_headers()})
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
                    try:
                        if not DRY:
                            req(f"pushSubs/{player}/{sid}", "DELETE")
                    except Exception as er:
                        print("suppression abonnement impossible", player, type(er).__name__, str(er)[:120])
                    stats["abonnements expirés"] += 1
                    (subs.get(player) or {}).pop(sid, None)  # plus retenté pendant ce passage
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
    supers = {}
    try:
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
    except Exception as e:
        print("Messages : erreur", type(e).__name__, str(e)[:200])

    sent = req("pushSent") or {}
    now = datetime.datetime.now(PARIS)
    now_ms = time.time() * 1000
    today = now.date()
    day_iso = today.isoformat()
    monday = today if today.weekday() == 0 else today + datetime.timedelta(days=(7 - today.weekday()))
    mon_iso = monday.isoformat()
    mon_lbl = f"lundi {monday.day if monday.day > 1 else '1er'} {MOIS[monday.month - 1]}"

    # Plafond : 4 notifications automatiques par joueur et par jour, quoi qu'il arrive
    CAP = 4
    cap = dict((sent.get("cap") or {}).get(day_iso) or {})

    def auto_send(p, title, body, url, tag):
        """Envoi automatique compté dans le plafond du jour. Renvoie True si c'est réglé (envoyé, ou plafond atteint)."""
        n = cap.get(p) or 0
        if n >= CAP:
            print("  plafond du jour atteint :", p)
            return True
        ok = send(p, title, body, url, tag)
        if ok:
            cap[p] = n + 1
            try:
                mark_val(f"pushSent/cap/{day_iso}/{p}", cap[p])
            except Exception as e:
                print("plafond : écriture impossible", p, type(e).__name__)
        return ok

    def block(name, fn):
        """Un bloc qui plante ne bloque pas les autres et ne fait pas échouer l'Action."""
        try:
            fn()
        except Exception as e:
            print(f"{name} : erreur", type(e).__name__, str(e)[:200])
            traceback.print_exc()

    vote = {}

    def do_vote():
        nonlocal vote
        vote = req(f"sessionVotes/{mon_iso}") or {}
        if vote.get("status") != "open" or now_ms >= (vote.get("closesAt") or 0):
            return
        votes = vote.get("votes") or {}
        if not (sent.get("open") or {}).get(mon_iso):
            print("Sondage ouvert :", mon_iso)
            mark(f"pushSent/open/{mon_iso}")  # noté AVANT d'envoyer : jamais deux fois, même en cas de souci
            opened = vote.get("at") or now_ms
            if now_ms - opened > 12 * 3600 * 1000:  # ouvert il y a plus de 12 h (panne, rattrapage) : trop tard, on n'envoie pas
                print("  ouvert depuis plus de 12 h : notification non envoyée")
                return
            for p in subs:
                auto_send(p, "🗓️ Le sondage est ouvert", f"Tu viens {mon_lbl} ? Réponds avant dimanche 21h30.", "./?n=vote#vote", f"open-{mon_iso}")
        if vote["closesAt"] - now_ms <= 3 * 3600 * 1000:
            done = (sent.get("rem3h") or {}).get(mon_iso) or {}
            for p in subs:
                if p not in votes and not done.get(p):
                    print("Rappel 3 h :", p)
                    mark(f"pushSent/rem3h/{mon_iso}/{p}")
                    auto_send(p, "⏳ Plus que 3 h pour répondre", f"Le sondage pour {mon_lbl} se ferme ce soir à 21h30.", "./?n=vote#vote", f"rem-{mon_iso}")
    block("Sondage", do_vote)

    # ── récap de la soirée prêt : dès qu'une soirée récente (aujourd'hui ou hier) est enregistrée
    def do_recap():
        dates = sorted(fetch(f"{DB}/sessions.json?shallow=true") or {})
        recent = [d for d in dates if (today - datetime.timedelta(days=1)).isoformat() <= d <= day_iso]
        for d in recent:
            if (sent.get("recap") or {}).get(d):
                continue
            ses = req(f"sessions/{d}") or {}
            ms = ses.get("matchs") or []
            if not ms:
                continue
            title = recap_headline(d, ms)
            print("Récap prêt :", d, "·", title)
            mark(f"pushSent/recap/{d}")
            for p in subs:
                auto_send(p, "📰 Le récap de la soirée est en ligne !", title, "./?n=recap#sessions", f"recap-{d}")
    block("Récap", do_recap)

    # ── clôture du sondage : message automatique dans le Vestiaire général
    def do_close():
        if vote.get("status") == "open" and (vote.get("closesAt") or 0) <= now_ms < (vote.get("closesAt") or 0) + 86400 * 1000 \
                and not (sent.get("chatClose") or {}).get(mon_iso):
            votes = vote.get("votes") or {}
            n = sum(1 for v in votes.values() if v == "P") + len(vote.get("plus") or {})
            print("Vestiaire : sondage fermé", mon_iso, n)
            mark(f"pushSent/chatClose/{mon_iso}")
            if not DRY:
                req("chat/msgs/g", "POST", {"sys": "close", "text": f"Le sondage est fermé : {n} présent{'s' if n > 1 else ''} {mon_lbl}.", "at": {".sv": "timestamp"}})
    block("Clôture", do_close)

    # ── Le Vestiaire : nouveaux messages
    # ── anniversaires : message dans le Vestiaire à 10h (date de naissance de « Mon compte »), une fois par an
    def do_bday():
        if now.hour != 10:
            return
        md = now.strftime("-%m-%d")
        done = ((sent.get("bday") or {}).get(str(now.year))) or {}
        for p, pr in (req("profiles") or {}).items():
            nais = (pr or {}).get("naissance") or ""
            if not nais.endswith(md) or done.get(p):
                continue
            try:
                age = now.year - int(nais[:4])
            except ValueError:
                age = 0
            print("Anniversaire :", p, age)
            if not DRY:
                req("chat/msgs/g", "POST", {"sys": "bday", "who": p, "text": f"🎂 Joyeux anniversaire @{p} !" + (f" {age} ans aujourd'hui, toute l'équipe te souhaite une belle journée ⚽" if 0 < age < 100 else " Toute l'équipe te souhaite une belle journée ⚽"), "at": {".sv": "timestamp"}})
            mark(f"pushSent/bday/{now.year}/{p}")
    block("Anniversaires", do_bday)

    # ── Le Vestiaire : nouveaux messages
    block("Vestiaire", lambda: chat_push(subs, supers, sent, now_ms, send, allowed))

    # ── propositions d'échange de vignettes reçues (une notification par proposition)
    def do_trades():
        seasons = sorted(k for k in (fetch(f"{DB}/vignettes.json?shallow=true") or {}) if "-" in k)
        if not seasons:
            return
        T = req(f"vignettes/{seasons[-1]}/_trades") or {}
        done = sent.get("trade") or {}
        for tid, t in T.items():
            if not isinstance(t, dict) or t.get("status") != "wait" or done.get(tid):
                continue
            if (t.get("at") or 0) < now_ms - 24 * 3600 * 1000:
                mark(f"pushSent/trade/{tid}")  # trop ancienne (premier passage) : on ne la notifie pas
                continue
            to, frm = t.get("to"), t.get("from")
            if not to or not allowed(to, "trades"):
                mark(f"pushSent/trade/{tid}")
                continue
            n = len(t.get("give") or [])
            gift = not (t.get("get") or [])
            print("Échange :", frm, "→", to)
            if send(to, f"🎁 {frm} t'offre {'une carte' if n == 1 else f'{n} cartes'}" if gift else f"🔄 {frm} te propose un échange", "À voir dans Vignettes · Échanges.", "./?n=trades#vignettes", f"trade-{tid}"):
                mark(f"pushSent/trade/{tid}")
    block("Échanges", do_trades)

    # ── boosters de vignettes : seulement entre 10h00 et 10h59 (passage raté = rien ce jour-là)
    def do_boost():
        if now.hour != 10:
            return
        seasons = sorted(k for k in (fetch(f"{DB}/vignettes.json?shallow=true") or {}) if "-" in k)
        if not seasons:
            return
        season = seasons[-1]
        # saison des vignettes terminée (config/seasons, ★ Saisons) : boosters en pause, aucun rappel
        sy = int(season[:4])
        trades_end = ((req("config/seasons") or {}).get(str(sy)) or {}).get("tradesEnd") or f"{sy + 1}-07-31T23:59"
        if now.strftime("%Y-%m-%dT%H:%M") > trades_end:
            return
        V = req(f"vignettes/{season}") or {}
        day = (today - datetime.date(1970, 1, 1)).days
        done = (sent.get("boost") or {}).get(day_iso) or {}
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
                mark(f"pushSent/boost/{day_iso}/{p}")
                auto_send(p, "🎁 " + ("Un booster t'attend" if stock == 1 else f"{stock} boosters t'attendent"), "Ouvre-les dans Vignettes.", "./?n=vg#vignettes", f"boost-{day_iso}")
    block("Boosters", do_boost)
    print(stats)


def chat_push(subs, supers, sent, now_ms, send, allowed=lambda p, k: True):
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
        msgs = fetch(f"{DB}/chat/msgs/{rid}.json?orderBy=%22$key%22&limitToLast=30") or {}
        L = sorted(((k, m) for k, m in msgs.items() if isinstance(m, dict)), key=lambda x: x[0])
        for p in members:
            since = max(window, (done.get(p) or {}).get(rid) or 0, (reads.get(p) or {}).get(rid) or 0, (reads.get(p) or {}).get("_init") or 0)
            new = [m for _, m in L if (m.get("at") or 0) > since and m.get("by") != p and m.get("sys") not in ("open", "club")]
            if not new:
                continue
            top = max(m.get("at") or 0 for m in new)
            ment = [m for m in new if (not m.get("sys") and re.search("@" + re.escape(p) + r"(?![\wÀ-ÿ])", m.get("text") or "")) or (m.get("sys") == "bday" and m.get("who") == p)]
            tous = [m for m in new if not m.get("sys") and re.search(r"@tous(?![\wÀ-ÿ])", m.get("text") or "", re.I)]
            # cloche coupée sur ce salon, ou notifications du Vestiaire désactivées : seulement les mentions et @tous
            if ((mutes.get(p) or {}).get(rid) or not allowed(p, "chat")) and not ment and not tous:
                mark_val(f"pushSent/chat/{p}/{rid}", top)
                continue
            last = ment[-1] if ment else tous[-1] if tous else new[-1]
            line = (last.get("text") or "") if last.get("sys") else f"{last.get('by')} : {last.get('text') or ''}"
            if ment and ment[-1].get("sys") == "bday":
                title = "🎂 Joyeux anniversaire !"
            elif ment:
                title = f"@ {ment[-1].get('by')} t'a mentionné · {name}"
            elif tous:
                title = f"📣 {tous[-1].get('by')} → tous · {name}"
            else:
                title = f"💬 {name}" + (f" · {len(new)} nouveaux messages" if len(new) > 1 else "")
            print("Vestiaire :", p, rid, len(new), "mention" if ment else "")
            if send(p, title[:60], line[:180], f"./?n=chat&r={rid}", f"chat-{rid}"):
                mark_val(f"pushSent/chat/{p}/{rid}", top)


def recap_headline(date, ms):
    """Même titre que la page Sessions (_crHeadline) : même classement, même tirage (empreinte FNV-1a de « date|h »)."""
    TL = {"Rouge": "Roses", "Vert": "Jaunes", "Bleue": "Bleus"}
    st = {}
    for m in ms:
        a, b, sa, sb = m.get("eqA"), m.get("eqB"), m.get("scoreA") or 0, m.get("scoreB") or 0
        for t in (a, b):
            st.setdefault(t, {"t": t, "pts": 0, "gf": 0, "ga": 0})
        st[a]["gf"] += sa; st[a]["ga"] += sb; st[b]["gf"] += sb; st[b]["ga"] += sa
        if sa > sb: st[a]["pts"] += 3
        elif sb > sa: st[b]["pts"] += 3
        else: st[a]["pts"] += 1; st[b]["pts"] += 1
    stand = sorted(st.values(), key=lambda x: (-x["pts"], -(x["gf"] - x["ga"]), -x["gf"]))
    if not stand:
        return "Soirée sans match"
    h = 2166136261
    for ch in date + "|h":
        h ^= ord(ch); h = (h * 16777619) & 0xFFFFFFFF
    pick = lambda L: L[h % len(L)]
    w, s2 = stand[0], (stand[1] if len(stand) > 1 else None)
    W, X = TL.get(w["t"], w["t"]), (TL.get(s2["t"], s2["t"]) if s2 else "")
    if s2 and s2["pts"] == w["pts"]:
        return pick([f"Coude à coude entre les {W} et les {X}", f"{W} et {X} se neutralisent", f"Impossible de départager les {W} et les {X}"])
    gap = w["pts"] - s2["pts"] if s2 else 99
    adj = {"Rouge": "rose", "Vert": "jaune", "Bleue": "bleue"}.get(w["t"], W.lower())
    if gap >= 6:
        L = [f"Les {W} écrasent la soirée", f"Récital des {W}", f"Personne n'arrête les {W}", f"Les {W} au-dessus du lot"]
    elif gap >= 3:
        L = [f"Les {W} raflent la mise", f"Soirée {adj} !", f"Les {W} prennent le pouvoir", f"Les {W} s'imposent"]
    else:
        L = [f"Les {W} l'emportent sur le fil", f"Les {W} gagnent d'un souffle", f"Victoire au finish pour les {W}"]
    return pick(L)


def mark_val(path, value):
    if DRY:
        print("  (simulation) marquer", path, value)
    else:
        req(path, "PUT", value)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # panne générale (réseau, base) : écrite dans le journal, sans faire échouer l'Action
        traceback.print_exc()
