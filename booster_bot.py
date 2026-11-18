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
BUTTON_RE = re.compile(os.getenv("EM_BUTTON_TEXT", "open the booster|ouvrir|tirer"), re.I)
NEXT_RE = re.compile(r"carte suivante|ton booster", re.I)
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


def snapshot(page, tag: str) -> None:
    """Ecrit dans le log ce que la page affiche (boutons + debut du texte)."""
    try:
        log(f"[{tag}] URL : {page.url}")
        for b in page.get_by_role("button").all():
            if b.is_visible():
                label = (b.get_attribute("aria-label") or "") + " | " + b.inner_text().strip()
                log(f"[{tag}]   bouton : {label!r}")
        txt = re.sub(r"\s+", " ", page.locator("body").inner_text())[:700]
        log(f"[{tag}] texte : {txt}")
    except Exception as e:
        log(f"[{tag}] snapshot impossible : {e}")


def open_booster(headless: bool, debug: bool) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            storage_state=str(STATE_FILE) if STATE_FILE.exists() else None,
            viewport={"width": 412, "height": 915},
        )
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

        SHOTS.mkdir(exist_ok=True)

        # 1) Cooldown actif ? (le site affiche "PROCHAIN BOOSTER DANS m:ss")
        if page.get_by_text(re.compile("prochain booster", re.I)).count() > 0:
            m = re.search(r"(\d+:\d{2})", page.locator("body").inner_text())
            log(f"Cooldown en cours, prochain booster dans {m.group(1) if m else '?'}.")
            return

        # 2) Ouvrir le booster (le paquet flotte : on force le clic)
        btn = page.get_by_role("button", name=BUTTON_RE)
        if btn.count() == 0 or not btn.first.is_enabled():
            log("Aucun booster disponible (bouton absent ou desactive).")
            log(f"URL actuelle : {page.url}")
            for b in page.get_by_role("button").all():
                if b.is_visible():
                    log(f"  bouton visible : {b.inner_text().strip()!r}")
            page.screenshot(path=str(SHOTS / "etat_page.png"))
            return
        btn.first.click(force=True)
        log("Clic sur le booster, attente de la premiere carte...")
        appeared = page.get_by_text(re.compile(r"clique sur la carte", re.I)).or_(
            page.get_by_role("button", name=NEXT_RE))
        try:
            appeared.first.wait_for(state="visible", timeout=6000)
        except PWTimeout:
            snapshot(page, "apres clic 1")
            log("Rien n'a change, nouvel essai avec un clic JavaScript.")
            try:
                btn.first.evaluate("e => e.click()")
            except Exception as e:
                log(f"  clic JS impossible : {e}")
            try:
                appeared.first.wait_for(state="visible", timeout=6000)
            except PWTimeout:
                snapshot(page, "apres clic 2")

        # 3) Boucle : reveler la carte, puis "Carte suivante (n/3)" / "Ton booster"
        next_btn = page.get_by_role("button", name=NEXT_RE)
        hint = page.get_by_text(re.compile(r"clique sur la carte", re.I))
        cards = 0
        for _ in range(12):
            try:
                next_btn.first.wait_for(state="visible", timeout=1500)
                label = next_btn.first.inner_text().strip()
                page.screenshot(path=str(SHOTS / f"carte_{cards}.png"))
                log(f"  clic sur {label!r}")
                next_btn.first.click(force=True)
                if re.search(r"ton booster", label, re.I):
                    page.wait_for_timeout(2000)
                    log(f"Booster complet : {cards} carte(s) recuperee(s).")
                    page.screenshot(path=str(SHOTS / "fin.png"))
                    return
                page.wait_for_timeout(800)
                continue
            except PWTimeout:
                pass

            if hint.count() > 0 and hint.first.is_visible():
                if reveal_card(page, hint, next_btn):
                    cards += 1
                    continue
                log("Impossible de reveler la carte.")
                break
            page.wait_for_timeout(1000)

        page.screenshot(path=str(SHOTS / "etat_final.png"))
        snapshot(page, "echec")
        raise RuntimeError(f"Sequence incomplete ({cards} carte(s) revelee(s)).")


def reveal_card(page, hint, next_btn) -> bool:
    """Clique sur la carte face cachee. Plusieurs methodes, on s'arrete des que ca marche."""
    def done() -> bool:
        try:
            next_btn.first.wait_for(state="visible", timeout=4000)
            return True
        except PWTimeout:
            return False

    # a) un vrai bouton "reveler / carte"
    cand = page.get_by_role(
        "button", name=re.compile(r"^(?!.*(suivante|ton booster)).*(reveal|révél|revel|card|carte)", re.I)
    )
    if cand.count() > 0:
        cand.first.click(force=True)
        if done():
            return True
    # b) le plus grand element cliquable (curseur "pointer") du panneau de la carte
    found = page.evaluate("""() => {
      const hint = [...document.querySelectorAll('*')].find(
        e => e.children.length === 0 && /clique sur la carte/i.test(e.textContent || ''));
      if (!hint) return false;
      let panel = hint;
      for (let i = 0; i < 4 && panel.parentElement; i++) panel = panel.parentElement;
      const c = [...panel.querySelectorAll('*')].filter(e => getComputedStyle(e).cursor === 'pointer');
      if (!c.length) return false;
      c.sort((a, b) => {
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
        return rb.width * rb.height - ra.width * ra.height;
      });
      c[0].setAttribute('data-bot-card', '1');
      return true;
    }""")
    if found:
        page.locator("[data-bot-card='1']").first.click(force=True)
        page.evaluate("document.querySelectorAll('[data-bot-card]').forEach(e => e.removeAttribute('data-bot-card'))")
        if done():
            return True
    # c) dernier recours : cliquer sur le texte d'aide
    hint.first.click(force=True)
    return done()


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
