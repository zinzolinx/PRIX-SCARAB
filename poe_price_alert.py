#!/usr/bin/env python3
"""
poe_price_alert.py
-------------------
Surveille le prix d'un item Path of Exile sur poe.ninja et envoie une alerte
sur un webhook Discord quand le prix atteint un nouveau plus bas (depuis que
le script tourne) ou passe sous un seuil fixe.

Version "one-shot" : le script fait UNE vérification puis s'arrête. C'est le
planificateur externe (cron, tâche planifiée, GitHub Actions...) qui le
relance périodiquement — voir le workflow GitHub Actions fourni à côté pour
un hébergement gratuit et sans serveur à gérer.

Utilise la nouvelle API officielle de poe.ninja (documentée sur
https://poe.ninja/docs/api, en place depuis juillet 2026).

IMPORTANT : les Scarabs (comme Fossiles, Essences, Cartes de divination, ...)
sont servis par l'endpoint "Exchange overview", PAS par "Stash item overview"
(celui-ci ne couvre que les uniques, maps, gemmes, etc.).

  - /poe1/api/economy/leagues                        -> détecte la ligue en cours
  - /poe1/api/economy/exchange/current/overview       -> prix des scarabs/fossiles/etc.

Utilisation locale :
    DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." python3 poe_price_alert.py

En local sans variable d'environnement, il utilise la valeur codée en dur
ci-dessous (pratique pour tester rapidement, mais évite de committer une
vraie URL de webhook dans un dépôt Git public).
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# ============================== CONFIG ===================================

BASE_URL = "https://poe.ninja"

# Laisse LEAGUE = None pour détecter automatiquement la ligue en cours
# (recommandé : la ligue change tous les 3-4 mois et casse le script sinon).
LEAGUE = None
ITEM_TYPE = "Scarab"                    # catégorie de l'API (Scarab, Fossil, Essence, DivinationCard, ...)
ITEM_NAME = "Divination Scarab of The Cloister"  # nom exact (ou approché) de l'item à suivre

# Priorité à la variable d'environnement DISCORD_WEBHOOK_URL (utilisée par
# GitHub Actions via un secret) ; sinon, valeur codée en dur ci-dessous.
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "COLLE_TON_URL_DE_WEBHOOK_DISCORD_ICI")

STATE_FILE = Path(__file__).parent / "poe_price_state.json"

# Optionnel : n'alerte que si le prix descend sous ce seuil (en chaos).
# Mets None pour désactiver et n'alerter que sur un nouveau plus bas absolu.
ALERT_BELOW_CHAOS = 10

# User-Agent descriptif demandé par les guidelines de l'API poe.ninja.
USER_AGENT = "poe-price-alert-script/1.0 (personal use)"

# ===========================================================================


def http_get_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def get_current_league() -> str:
    leagues = http_get_json(f"{BASE_URL}/poe1/api/economy/leagues")
    if not leagues:
        raise RuntimeError("Impossible de récupérer la liste des ligues poe.ninja.")
    current = leagues[0]
    print(f"ℹ️  Ligue détectée automatiquement : {current['name']} (id: {current['id']})")
    return current["id"]


def fetch_exchange_overview(league: str, item_type: str) -> dict:
    url = f"{BASE_URL}/poe1/api/economy/exchange/current/overview?league={league}&type={item_type}"
    return http_get_json(url)


def build_id_name_map(core: dict) -> dict:
    """Construit un dict {id: nom} à partir de core.items, quelle que soit
    sa forme exacte (liste d'objets ou dict déjà indexé par id)."""
    items = core.get("items") if isinstance(core, dict) else None
    id_to_name = {}
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and "id" in it:
                id_to_name[str(it["id"])] = it.get("name", str(it["id"]))
    elif isinstance(items, dict):
        for k, v in items.items():
            if isinstance(v, dict):
                id_to_name[str(k)] = v.get("name", str(k))
            else:
                id_to_name[str(k)] = str(v)
    return id_to_name


def to_slug(name: str) -> str:
    """Convertit un nom lisible en slug façon poe.ninja, ex:
    'Divination Scarab of The Cloister' -> 'divination-scarab-of-the-cloister'."""
    return "-".join(name.lower().split())


def find_item(data: dict, name: str):
    """Retourne (line, nom_résolu) ou (None, None) si introuvable.

    Sur l'endpoint "exchange overview", `core.items` ne contient que les
    métadonnées des devises de référence (chaos/divine), PAS le nom de
    chaque item échangé. Le champ `id` de chaque ligne est en réalité un
    slug (ex: "divination-scarab-of-the-cloister"), identique à celui
    utilisé dans l'URL de la page détail sur poe.ninja. On matche donc
    d'abord sur ce slug, avec un repli sur core.items pour les devises
    classiques (Chaos Orb, Divine Orb, ...).
    """
    name_lower = name.lower()
    target_slug = to_slug(name)

    # 1) Correspondance directe par slug (cas des scarabs, fossiles, etc.)
    for line in data.get("lines", []):
        line_id = str(line.get("id", "")).lower()
        if line_id == target_slug or line_id == name_lower:
            return line, name
    for line in data.get("lines", []):
        line_id = str(line.get("id", "")).lower()
        if target_slug in line_id or line_id in target_slug:
            return line, line.get("id")

    # 2) Repli : correspondance par nom via core.items (devises de référence)
    core = data.get("core", {}) or {}
    id_to_name = build_id_name_map(core)
    for line in data.get("lines", []):
        item_name = id_to_name.get(str(line.get("id")), "")
        if item_name.lower() == name_lower:
            return line, item_name
    for line in data.get("lines", []):
        item_name = id_to_name.get(str(line.get("id")), "")
        if name_lower in item_name.lower():
            return line, item_name

    return None, None


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def send_discord_alert(item_name: str, chaos_value: float, lowest: float, is_new_low: bool, league: str) -> None:
    if not DISCORD_WEBHOOK_URL or "COLLE_TON_URL" in DISCORD_WEBHOOK_URL:
        print("⚠️  Aucune URL de webhook Discord configurée, alerte non envoyée.")
        return

    title = f"📉 Nouveau plus bas pour {item_name} !" if is_new_low else f"🔔 Alerte prix pour {item_name}"

    content = (
        f"**{title}**\n"
        f"Prix actuel : `{chaos_value} chaos`\n"
        f"Plus bas observé : `{lowest} chaos`\n"
        f"Ligue : `{league}`\n"
        f"Heure : {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )

    payload = json.dumps({"content": content}).encode()
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"✅ Alerte Discord envoyée (status {resp.status}).")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        print(f"❌ Échec de l'envoi de l'alerte Discord (HTTP {e.code}) : {body}")
    except urllib.error.URLError as e:
        print(f"❌ Échec de l'envoi de l'alerte Discord : {e}")


def check_price_once(league: str) -> None:
    try:
        data = fetch_exchange_overview(league, ITEM_TYPE)
    except urllib.error.HTTPError as e:
        print(f"❌ Erreur HTTP {e.code} en récupérant les données poe.ninja : {e.read().decode(errors='ignore')}")
        return
    except Exception as e:
        print(f"❌ Erreur lors de la récupération des données poe.ninja : {e}")
        return

    line, resolved_name = find_item(data, ITEM_NAME)
    if line is None:
        sample_ids = [str(l.get("id")) for l in data.get("lines", [])[:15]]
        print(f"⚠️  Item '{ITEM_NAME}' introuvable dans la catégorie '{ITEM_TYPE}' pour la ligue '{league}'.")
        if sample_ids:
            print(f"    Exemples d'identifiants disponibles dans cette catégorie : {sample_ids}")
        else:
            print(f"    Aucune ligne retournée pour cette catégorie (réponse vide).")
        return

    chaos_value = line.get("primaryValue")
    if chaos_value is None:
        print("⚠️  Pas de valeur disponible pour cet item.")
        return

    state = load_state()
    key = f"{league}:{ITEM_TYPE}:{ITEM_NAME}"
    entry = state.get(key, {})
    lowest = entry.get("lowest", chaos_value)
    threshold_alerted = entry.get("threshold_alerted", False)

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    print(f"[{now_str}] {resolved_name} : {chaos_value} chaos (plus bas connu : {lowest} chaos)")

    is_new_low = chaos_value < lowest
    below_threshold = ALERT_BELOW_CHAOS is not None and chaos_value <= ALERT_BELOW_CHAOS

    if is_new_low:
        lowest = chaos_value

    should_alert_threshold = below_threshold and not threshold_alerted
    if is_new_low or should_alert_threshold:
        send_discord_alert(resolved_name, chaos_value, lowest, is_new_low, league)

    threshold_alerted = below_threshold

    state[key] = {
        "lowest": lowest,
        "last_checked": now_str,
        "last_value": chaos_value,
        "threshold_alerted": threshold_alerted,
    }
    save_state(state)


def main() -> None:
    league = LEAGUE or get_current_league()
    print(f"🔍 Vérification de '{ITEM_NAME}' ({ITEM_TYPE}, ligue {league}).")
    check_price_once(league)


if __name__ == "__main__":
    main()
