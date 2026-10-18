"""Extraction des offres d'emploi France Travail pour un métier (code ROME).

Étape 1 : se connecter à l'API (obtenir un jeton d'accès).
Étape 2 : récupérer toutes les offres en France et les enregistrer
          dans data/offres_<ROME>_<date>.csv.

Les identifiants sont lus dans les variables d'environnement
FT_CLIENT_ID et FT_CLIENT_SECRET (secrets GitHub, ou fichier .env en local).
Ils ne sont jamais affichés.
"""

import csv
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

CODE_ROME = "M1703"  # Chef de produit

URL_JETON = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
URL_OFFRES = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
TAILLE_PAGE = 150      # maximum autorisé par l'API par requête
INDEX_MAX = 3149       # l'API ne va pas au-delà de la 3150e offre par recherche

DEPARTEMENTS = (
    [f"{n:02d}" for n in range(1, 96) if n != 20]
    + ["2A", "2B", "971", "972", "973", "974", "976"]
)


def lire_fichier_env():
    """En local : charge le fichier .env s'il existe (jamais versionné)."""
    env = Path(__file__).with_name(".env")
    if env.exists():
        for ligne in env.read_text(encoding="utf-8").splitlines():
            if "=" in ligne and not ligne.strip().startswith("#"):
                cle, valeur = ligne.split("=", 1)
                os.environ.setdefault(cle.strip(), valeur.strip().strip('"'))


def nettoyer(nom):
    """Corrige les erreurs de copier-coller : espaces, guillemets, « NOM= » collé avec."""
    valeur = re.sub(r"\s", "", os.environ.get(nom, "")).strip("\"'")
    return valeur.removeprefix(f"{nom}=").strip("\"'")


def obtenir_jeton():
    client_id = nettoyer("FT_CLIENT_ID")
    client_secret = nettoyer("FT_CLIENT_SECRET")
    if client_secret.startswith("PAR_") and not client_id.startswith("PAR_"):
        client_id, client_secret = client_secret, client_id   # secrets inversés
    if not client_id or not client_secret:
        sys.exit("❌ Identifiants absents : FT_CLIENT_ID et FT_CLIENT_SECRET sont vides.")

    reponse = requests.post(
        URL_JETON,
        params={"realm": "/partenaire"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "api_offresdemploiv2 o2dsoffre",
        },
        timeout=30,
    )
    if reponse.status_code != 200:
        try:
            erreur = reponse.json()
            raison = f"{erreur.get('error', '')} {erreur.get('error_description', '')}".strip()
        except ValueError:
            raison = "réponse illisible"
        # Indices sans révéler les codes : début de l'identifiant et longueurs
        print(f"   Identifiant commence par PAR_ : {'oui' if client_id.startswith('PAR_') else 'NON'}, "
              f"{len(client_id)} caractères ; clé secrète : {len(client_secret)} caractères.")
        sys.exit(f"❌ Connexion à l'API refusée (code {reponse.status_code}, raison : {raison}). "
                 "Vérifie tes identifiants et que l'API « Offres d'emploi v2 » est cochée.")
    print("✅ Étape 1 : connexion à l'API France Travail réussie.")
    return reponse.json()["access_token"]


def rechercher(jeton, filtres):
    """Renvoie (offres, total annoncé) pour une recherche, page par page."""
    offres, debut, total = [], 0, 0
    while debut <= INDEX_MAX:
        fin = min(debut + TAILLE_PAGE - 1, INDEX_MAX)
        reponse = requests.get(
            URL_OFFRES,
            headers={"Authorization": f"Bearer {jeton}", "Accept": "application/json"},
            params={**filtres, "codeROME": CODE_ROME, "range": f"{debut}-{fin}"},
            timeout=30,
        )
        if reponse.status_code == 204:          # aucune offre
            break
        if reponse.status_code == 429:          # trop de requêtes : on patiente
            time.sleep(2)
            continue
        if reponse.status_code not in (200, 206):
            sys.exit(f"❌ Erreur de l'API (code {reponse.status_code}) : {reponse.text[:200]}")

        offres += reponse.json().get("resultats", [])
        trouve = re.search(r"/(\d+)", reponse.headers.get("Content-Range", ""))
        total = int(trouve.group(1)) if trouve else len(offres)
        if fin + 1 >= total:
            break
        debut += TAILLE_PAGE
        time.sleep(0.2)
    return offres, total


def toutes_les_offres(jeton):
    offres, total = rechercher(jeton, {})
    if total <= INDEX_MAX + 1:
        return offres
    # Plus de 3150 offres : l'API bloque, on découpe par département.
    print(f"   {total} offres annoncées, découpage par département…")
    offres = []
    for dep in DEPARTEMENTS:
        offres += rechercher(jeton, {"departement": dep})[0]
    return offres


def en_ligne(offre):
    return {
        "intitule": offre.get("intitule", ""),
        "entreprise": (offre.get("entreprise") or {}).get("nom", ""),
        "lieu": (offre.get("lieuTravail") or {}).get("libelle", ""),
        "contrat": offre.get("typeContratLibelle") or offre.get("typeContrat", ""),
        "salaire": (offre.get("salaire") or {}).get("libelle", ""),
        "date_publication": (offre.get("dateCreation") or "")[:10],
        "competences": " | ".join(c.get("libelle", "") for c in offre.get("competences") or []),
    }


def main():
    lire_fichier_env()
    jeton = obtenir_jeton()

    offres = toutes_les_offres(jeton)
    vues = {}
    for offre in offres:                         # supprime les doublons éventuels
        vues[offre.get("id")] = offre
    lignes = [en_ligne(o) for o in vues.values()]

    Path("data").mkdir(exist_ok=True)
    fichier = Path("data") / f"offres_{CODE_ROME}_{date.today().isoformat()}.csv"
    with fichier.open("w", newline="", encoding="utf-8-sig") as f:
        ecrivain = csv.DictWriter(f, fieldnames=list(en_ligne({}).keys()), delimiter=";")
        ecrivain.writeheader()
        ecrivain.writerows(lignes)

    print(f"✅ Étape 2 : {len(lignes)} offres enregistrées dans {fichier}")
    print("\nLes cinq premières lignes :")
    for ligne in lignes[:5]:
        print(" -", " ; ".join(f"{k}: {v}" for k, v in ligne.items() if k != "competences"))

    # Transmis à l'Action GitHub pour le message de la version (commit)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as sortie:
            sortie.write(f"nombre={len(lignes)}\n")


if __name__ == "__main__":
    main()
