"""
Bot Essec Masters : ouvre un booster (3 cartes) a chaque execution.
Planifie via GitHub Actions (voir .github/workflows/booster.yml).

Le site s'affiche en anglais ou en francais selon le navigateur : tous les textes
recherches ci-dessous existent donc en deux langues.

Test en local (Windows) :
    pip install playwright python-dotenv && playwright install chromium
    fichier .env : EM_EMAIL=... et EM_PASSWORD=...
    python booster_bot.py --debug
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

PACK_RE = re.compile(r"open the booster|ouvrir|tirer", re.I)
COOLDOWN_RE = re.compile(r"next booster in|prochain booster", re.I)
HINT_RE = re.compile(r"click the card|clique sur la carte", re.I)
REVEAL_RE = re.compile(r"reveal the card|r[ée]v[ée]ler la carte", re.I)
NEXT_RE = re.compile(
    r"next card|carte suivante|your booster|ton booster|my booster|collect|r[ée]cup[ée]rer|claim", re.I
)
FINAL_RE = re.compile(r"your booster|ton booster|my booster|collect|r[ée]cup[ée]rer|claim", re.I)

STATE_FILE = HERE / "session.json"
LOG_FILE = HERE / "booster_bot.log"
SHOTS = HERE / "screenshots"


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def snapshot(page, tag: str) -> None:
    """Ecrit dans le log ce que la page affiche (boutons + debut du texte)."""
    try:
        log(f"[{tag}] URL : {page.url}")
        for b in page.get_by_role("button").all():
            if b.is_visible():
                label = (b.get_attribute("aria-label") or "") + " | " + b.inner_text().strip()[:60]
                log(f"[{tag}]   bouton : {label!r}")
        txt = re.sub(r"\s+", " ", page.locator("body").inner_text())[:500]
        log(f"[{tag}] texte : {txt}")
    except Exception as e:
        log(f"[{tag}] snapshot impossible : {e}")


def login(page) -> None:
    log("Connexion...")
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill("input[type=email]", EMAIL)
    page.fill("input[type=password]", PASSWORD)
    page.get_by_role("button", name=re.compile("log in|connexion|se connecter", re.I)).click()
    try:
        page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
    except PWTimeout:
        raise RuntimeError("Connexion echouee : verifie EM_EMAIL / EM_PASSWORD.")
    page.wait_for_load_state("networkidle")
    log("Connecte.")


def open_booster(headless: bool, debug: bool) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            storage_state=str(STATE_FILE) if STATE_FILE.exists() else None,
            viewport={"width": 412, "height": 915},
        )
        page = ctx.new_page()
        try:
            _run(page, ctx, debug)
        except Exception:
            SHOTS.mkdir(exist_ok=True)
            try:
                page.screenshot(path=str(SHOTS / "erreur.png"))
                snapshot(page, "erreur")
            except Exception:
                pass
            browser.close()
            raise
        browser.close()


def _run(page, ctx, debug: bool) -> None:
    page.goto(BOOSTER_URL, wait_until="networkidle")
    if "/login" in page.url:
        login(page)
        page.goto(BOOSTER_URL, wait_until="networkidle")
    ctx.storage_state(path=str(STATE_FILE))
    SHOTS.mkdir(exist_ok=True)

    if debug:
        snapshot(page, "debug")
        input("Mode debug : regarde la page, puis Entree pour fermer...")
        return

    pack = page.get_by_role("button", name=PACK_RE)
    if pack.count() == 0 or not pack.first.is_enabled():
        # Pas de paquet : on est en cooldown (ou la page a change)
        m = re.search(r"(\d+:\d{2})", page.locator("body").inner_text())
        if page.get_by_text(COOLDOWN_RE).count() > 0:
            log(f"Cooldown en cours, prochain booster dans {m.group(1) if m else '?'}.")
        else:
            log("Aucun paquet trouve et pas de compte a rebours : la page a peut-etre change.")
            snapshot(page, "pas de paquet")
            page.screenshot(path=str(SHOTS / "etat_page.png"))
        return

    # 1) Ouvrir le paquet (il flotte : clic force). Les 3 cartes sont attribuees des ce clic.
    pack.first.click(force=True)
    log("Clic sur le booster.")
    first_ui = (
        page.get_by_role("button", name=REVEAL_RE)
        .or_(page.get_by_role("button", name=NEXT_RE))
        .or_(page.get_by_text(HINT_RE))
    )
    try:
        first_ui.first.wait_for(state="visible", timeout=10000)
    except PWTimeout:
        snapshot(page, "apres clic")
        page.screenshot(path=str(SHOTS / "etat_final.png"))
        raise RuntimeError("Le booster ne semble pas s'etre ouvert.")
    log("Booster ouvert, 3 cartes recuperees. Deroule de l'animation...")

    # 2) Animation : reveler la carte, puis "Carte suivante", x3 (au mieux, pas bloquant)
    reveal = page.get_by_role("button", name=REVEAL_RE)
    nxt = page.get_by_role("button", name=NEXT_RE)
    done = 0
    for _ in range(8):
        if reveal.count() > 0 and reveal.first.is_visible():
            reveal.first.click(force=True)
            page.wait_for_timeout(1200)
            page.screenshot(path=str(SHOTS / f"carte_{done + 1}.png"))
        if nxt.count() > 0 and nxt.first.is_visible():
            label = nxt.first.inner_text().strip() or nxt.first.get_attribute("aria-label") or ""
            nxt.first.click(force=True)
            done += 1
            log(f"  carte {done}/3 vue, clic sur {label!r}")
            page.wait_for_timeout(1200)
            if FINAL_RE.search(label) or done >= 3:
                break
        else:
            page.wait_for_timeout(800)

    page.screenshot(path=str(SHOTS / "fin.png"))
    if done < 3:
        # Les cartes sont deja dans la collection : on signale sans faire echouer le run.
        log(f"Animation incomplete ({done}/3), mais le booster est bien recupere.")
        snapshot(page, "animation incomplete")
    else:
        log("Booster complet : 3 cartes recuperees.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="tourne en continu toutes les 5 min")
    ap.add_argument("--debug", action="store_true", help="navigateur visible + etat de la page")
    args = ap.parse_args()

    if not EMAIL or not PASSWORD:
        sys.exit("Renseigne EM_EMAIL et EM_PASSWORD (fichier .env ou secrets GitHub)")

    while True:
        try:
            open_booster(headless=not args.debug, debug=args.debug)
        except Exception as e:
            log(f"Erreur : {e}")
            if not args.loop:
                sys.exit(1)
        if not args.loop or args.debug:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()
