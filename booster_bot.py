"""
Bot Essec Masters : ouvre un booster a chaque execution.
Planifie-le toutes les 10 min (Planificateur de taches Windows) ou lance-le avec --loop.

Installation (une fois) :
    pip install playwright python-dotenv
    playwright install chromium

Fichier .env a cote du script :
    EM_EMAIL=ton.email@essec.edu
    EM_PASSWORD=ton_mot_de_passe
    # Optionnels, a ajuster apres avoir inspecte le site (voir --debug) :
    EM_BOOSTER_URL=https://www.essecmasters.com/home
    EM_BUTTON_TEXT=ouvrir|open|booster|tirer
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

BASE = "https://www.essecmasters.com"
EMAIL = (os.getenv("EM_EMAIL") or "").strip()
PASSWORD = (os.getenv("EM_PASSWORD") or "").strip()
BOOSTER_URL = os.getenv("EM_BOOSTER_URL", f"{BASE}/home")
BUTTON_RE = re.compile(os.getenv("EM_BUTTON_TEXT", "ouvrir|open|booster|tirer"), re.I)
STATE_FILE = HERE / "session.json"   # garde la session pour ne pas se reconnecter a chaque fois
LOG_FILE = HERE / "booster_bot.log"
SHOTS = HERE / "screenshots"


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def login(page) -> None:
    log("Connexion...")
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill("input[type=email]", EMAIL)
    page.fill("input[type=password]", PASSWORD)
    page.get_by_role("button", name=re.compile("log in|connexion|se connecter", re.I)).click()
    try:
        page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
    except PWTimeout:
        # Affiche le message d'erreur du site s'il y en a un
        for sel in ("[role=alert]", ".error", "[class*=error]", "[class*=toast]"):
            try:
                txt = page.locator(sel).first.inner_text(timeout=1000).strip()
                if txt:
                    log(f"Message du site : {txt!r}")
                    break
            except Exception:
                pass
        raise RuntimeError("Connexion echouee : verifie EM_EMAIL / EM_PASSWORD.")
    page.wait_for_load_state("networkidle")
    log("Connecte.")


def open_booster(headless: bool, debug: bool) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(storage_state=str(STATE_FILE) if STATE_FILE.exists() else None)
        page = ctx.new_page()
        try:
            _run(page, ctx, headless, debug)
        except Exception:
            SHOTS.mkdir(exist_ok=True)
            try:
                page.screenshot(path=str(SHOTS / "erreur.png"))
                log(f"URL au moment de l'erreur : {page.url}")
            except Exception:
                pass
            browser.close()
            raise
        browser.close()


def _run(page, ctx, headless, debug) -> None:
    if True:
        page.goto(BOOSTER_URL, wait_until="networkidle")
        if "/login" in page.url:
            login(page)
            page.goto(BOOSTER_URL, wait_until="networkidle")
        ctx.storage_state(path=str(STATE_FILE))

        if debug:
            # Liste les boutons visibles pour trouver le bon texte a mettre dans EM_BUTTON_TEXT
            for b in page.get_by_role("button").all():
                if b.is_visible():
                    log(f"bouton visible : {b.inner_text().strip()!r}")
            input("Mode debug : regarde la page, puis Entree pour fermer...")
            return

        btn = page.get_by_role("button", name=BUTTON_RE)
        if btn.count() == 0 or not btn.first.is_enabled():
            log("Aucun booster disponible (bouton absent ou desactive).")
            log(f"URL actuelle : {page.url}")
            for b in page.get_by_role("button").all():
                if b.is_visible():
                    log(f"  bouton visible : {b.inner_text().strip()!r}")
            SHOTS.mkdir(exist_ok=True)
            page.screenshot(path=str(SHOTS / "etat_page.png"))
        else:
            # Le paquet flotte (animation CSS) : Playwright le juge "instable", on force le clic
            btn.first.click(force=True)
            page.wait_for_timeout(5000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                pass
            for b in page.get_by_role("button").all():
                if b.is_visible():
                    label = b.get_attribute("aria-label") or b.inner_text().strip()
                    log(f"  bouton apres clic : {label!r}")
            SHOTS.mkdir(exist_ok=True)
            shot = SHOTS / f"booster_{datetime.now():%Y%m%d_%H%M%S}.png"
            page.screenshot(path=str(shot))
            log(f"Booster ouvert. Capture : {shot.name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="tourne en continu toutes les 10 min")
    ap.add_argument("--debug", action="store_true", help="navigateur visible + liste des boutons")
    args = ap.parse_args()

    if not EMAIL or not PASSWORD:
        sys.exit("Renseigne EM_EMAIL et EM_PASSWORD dans le fichier .env")

    while True:
        try:
            open_booster(headless=not args.debug, debug=args.debug)
        except Exception as e:
            log(f"Erreur : {e}")
            if not args.loop:
                sys.exit(1)
        if not args.loop or args.debug:
            break
        time.sleep(600)


if __name__ == "__main__":
    main()
