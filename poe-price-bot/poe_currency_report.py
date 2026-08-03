#!/usr/bin/env python3
"""
poe_currency_report.py
------------------------
Envoie le prix d'une currency Path of Exile (ex: Divine Orb) sur un webhook
Discord. C'est un rapport périodique, pas une alerte sur un plus bas.

Version "one-shot" : le script fait UN envoi puis s'arrête. C'est le
planificateur externe (cron, tâche planifiée, GitHub Actions...) qui le
relance périodiquement — voir le workflow GitHub Actions fourni à côté pour
un hébergement gratuit et sans serveur à gérer.

Utilise la nouvelle API officielle de poe.ninja (documentée sur
https://poe.ninja/docs/api, en place depuis juillet 2026) :
  - /poe1/api/economy/leagues                             -> détecte la ligue en cours
  - /poe1/api/economy/stash/current/currency/overview      -> prix des currencies

Utilisation locale :
    DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." python3 poe_currency_report.py
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime

# ============================== CONFIG ===================================

BASE_URL = "https://poe.ninja"

# Laisse LEAGUE = None pour détecter automatiquement la ligue en cours
# (recommandé : la ligue change tous les 3-4 mois et casse le script sinon).
LEAGUE = None
CURRENCY_TYPE = "Currency"              # "Currency" ou "Fragment"
ITEM_NAME = "Divine Orb"                # nom exact (ou approché) de la currency à suivre

# Priorité à la variable d'environnement DISCORD_WEBHOOK_URL (utilisée par
# GitHub Actions via un secret) ; sinon, valeur codée en dur ci-dessous.
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "COLLE_TON_URL_DE_WEBHOOK_DISCORD_ICI")

# User-Agent descriptif demandé par les guidelines de l'API poe.ninja.
USER_AGENT = "poe-currency-report-script/1.0 (personal use)"

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


def fetch_currency_overview(league: str, currency_type: str) -> dict:
    url = f"{BASE_URL}/poe1/api/economy/stash/current/currency/overview?league={league}&type={currency_type}"
    return http_get_json(url)


def find_currency(data: dict, name: str) -> dict | None:
    name_lower = name.lower()
    for line in data.get("lines", []):
        currency_name = line.get("currencyTypeName", "")
        if currency_name.lower() == name_lower:
            return line
    for line in data.get("lines", []):
        if name_lower in line.get("currencyTypeName", "").lower():
            return line
    return None


def send_discord_report(item_name: str, chaos_equivalent: float, league: str) -> None:
    if not DISCORD_WEBHOOK_URL or "COLLE_TON_URL" in DISCORD_WEBHOOK_URL:
        print("⚠️  Aucune URL de webhook Discord configurée, message non envoyé.")
        return

    content = (
        f"**💰 Prix de {item_name}**\n"
        f"Équivalent en chaos : `{chaos_equivalent}`\n"
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
            print(f"✅ Rapport Discord envoyé (status {resp.status}).")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        print(f"❌ Échec de l'envoi du rapport Discord (HTTP {e.code}) : {body}")
    except urllib.error.URLError as e:
        print(f"❌ Échec de l'envoi du rapport Discord : {e}")


def report_price_once(league: str) -> None:
    try:
        data = fetch_currency_overview(league, CURRENCY_TYPE)
    except urllib.error.HTTPError as e:
        print(f"❌ Erreur HTTP {e.code} en récupérant les données poe.ninja : {e.read().decode(errors='ignore')}")
        return
    except Exception as e:
        print(f"❌ Erreur lors de la récupération des données poe.ninja : {e}")
        return

    item = find_currency(data, ITEM_NAME)
    if item is None:
        print(f"⚠️  Currency '{ITEM_NAME}' introuvable dans la catégorie '{CURRENCY_TYPE}' pour la ligue '{league}'.")
        return

    chaos_equivalent = item.get("chaosEquivalent")

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    print(f"[{now_str}] {ITEM_NAME} : {chaos_equivalent} chaos équivalent")

    send_discord_report(ITEM_NAME, chaos_equivalent, league)


def main() -> None:
    league = LEAGUE or get_current_league()
    print(f"🔍 Rapport pour '{ITEM_NAME}' ({CURRENCY_TYPE}, ligue {league}).")
    report_price_once(league)


if __name__ == "__main__":
    main()
