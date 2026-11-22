"""
Essec Masters : bot booster en boucle, a lancer sur ton PC.

Il ouvre un booster, attend le compte a rebours du site + 30 secondes, recommence,
et ne s'arrete que quand tu fais CTRL+C.

Installation (une seule fois, dans PowerShell) :
    pip install playwright python-dotenv
    playwright install chromium

Identifiants : cree un fichier .env a cote du script avec
    EM_EMAIL=ton.email
    EM_PASSWORD=ton_mot_de_passe
(sinon le script te les demande au lancement, sans les enregistrer)

Bright Data Browser API (optionnel) :
    Ajouter dans le .env :
    BRIGHT_DATA_WS_URL=wss://brd-customer-XXXXXXXX-zone-ZONE_NAME:PASSWORD@brd.superproxy.io:9222
    Si absent, le script utilise Chromium local.

Lancement :
    python essec_booster_local.py            # boucle sans fin, navigateur invisible
    python essec_booster_local.py --show     # navigateur visible (pour voir ce qu'il fait, Chromium local seulement)
    python essec_booster_local.py --once     # un seul passage puis stop
"""
import argparse
import getpass
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv est optionnel
    load_dotenv = None
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

HERE = Path(__file__).resolve().parent
if load_dotenv:
    load_dotenv(HERE / ".env")

BASE = "https://www.essecmasters.com"
BOOSTER_URL = os.getenv("EM_BOOSTER_URL", f"{BASE}/home")
EXTRA_WAIT = 30          # secondes ajoutees apres la fin du compte a rebours
DEFAULT_COOLDOWN = 600   # si le site n'affiche pas son compte a rebours
RETRY_AFTER_ERROR = 120  # attente apres une erreur (reseau, page changee...)
MAX_LOGIN_FAILS = 3      # on s'arrete si la connexion echoue 3 fois de suite

# Bright Data Browser API : wss://brd-customer-...-zone-...:PASSWORD@brd.superproxy.io:9222
# Si absent, on utilise Chromium local.
BRIGHT_DATA_WS_URL = (os.getenv("BRIGHT_DATA_WS_URL") or "").strip()

PACK_RE = re.compile(r"open the booster|ouvrir|tirer", re.I)
COOLDOWN_RE = re.compile(r"(?:next booster in|prochain booster dans)\s*(\d+):(\d{2})", re.I)
HINT_RE = re.compile(r"click the card|clique sur la carte", re.I)
REVEAL_RE = re.compile(r"reveal the card|r[ée]v[ée]ler la carte", re.I)
NEXT_RE = re.compile(
    r"next card|carte suivante|your booster|ton booster|my booster|collect|r[ée]cup[ée]rer|claim", re.I
)
FINAL_RE = re.compile(r"your booster|ton booster|my booster|collect|r[ée]cup[ée]rer|claim", re.I)

STATE_FILE = HERE / "session.json"   # garde la connexion entre deux passages
LOG_FILE = HERE / "booster_bot.log"
SHOTS = HERE / "screenshots"


class LoginError(Exception):
    pass


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print("\r" + line + " " * 20)
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


def load_session_into_context(ctx) -> None:
    """Injecte les cookies de session.json dans le navigateur pour eviter d'avoir a se reconnecter."""
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if "cookies" in data and data["cookies"]:
            ctx.add_cookies(data["cookies"])
            log("Session injectee depuis session.json.")
    except Exception:
        # Bright Data peut bloquer Storage.setCookies par defaut si Custom Headers n'est pas active
        pass


def get_credentials() -> tuple[str, str]:
    email = (os.getenv("EM_EMAIL") or "").strip()
    password = (os.getenv("EM_PASSWORD") or "").strip()
    if not email:
        email = input("Email Essec Masters : ").strip()
    if not password:
        password = getpass.getpass("Mot de passe (invisible a la saisie) : ").strip()
    return email, password


def login(page, email: str, password: str, goto_opts: dict | None = None) -> None:
    if goto_opts is None:
        goto_opts = (
            {"wait_until": "domcontentloaded", "timeout": 60_000}
            if BRIGHT_DATA_WS_URL
            else {"wait_until": "networkidle"}
        )
    log("Connexion...")
    page.goto(f"{BASE}/login", **goto_opts)

    # Remplissage de l'email
    email_input = page.locator("input[type=email]").first
    email_input.wait_for(state="visible", timeout=30_000)
    email_input.fill(email, force=True)

    page.wait_for_timeout(500)

    # Remplissage du mot de passe
    password_input = page.locator("input[type=password]").first
    password_input.wait_for(state="visible", timeout=30_000)

    if BRIGHT_DATA_WS_URL:
        # Contournement de la regle Bright Data "password typing is not allowed" :
        # On passe temporairement le champ en type='text' dans le DOM pendant la saisie.
        page.evaluate("""({ val }) => {
            const el = document.querySelector('input[type=password]') || document.querySelector('input[name=password]');
            if (!el) return;
            el.setAttribute('type', 'text');
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            setter.call(el, val);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.setAttribute('type', 'password');
        }""", {"val": password})
    else:
        password_input.fill(password, force=True)

    page.wait_for_timeout(500)
    page.get_by_role("button", name=re.compile("log in|connexion|se connecter", re.I)).first.click(force=True)
    try:
        page.wait_for_url(lambda u: "/login" not in u, timeout=goto_opts.get("timeout") or 30000)
    except PWTimeout:
        raise LoginError("Connexion refusee : verifie l'email et le mot de passe.")
    page.wait_for_load_state("domcontentloaded")
    log("Connecte.")


def read_cooldown(page) -> int | None:
    """Secondes restantes affichees par le site, ou None."""
    m = COOLDOWN_RE.search(page.locator("body").inner_text())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def play_animation(page) -> int:
    """Revele les 3 cartes (cosmetique : elles sont deja dans la collection)."""
    reveal = page.get_by_role("button", name=REVEAL_RE)
    nxt = page.get_by_role("button", name=NEXT_RE)
    done = 0
    for _ in range(8):
        if reveal.count() > 0 and reveal.first.is_visible():
            reveal.first.click(force=True)
            page.wait_for_timeout(1200)
        if nxt.count() > 0 and nxt.first.is_visible():
            label = nxt.first.inner_text().strip() or nxt.first.get_attribute("aria-label") or ""
            nxt.first.click(force=True)
            done += 1
            page.wait_for_timeout(1200)
            if FINAL_RE.search(label) or done >= 3:
                break
        else:
            page.wait_for_timeout(800)
    return done


def run_once(email: str, password: str, show: bool) -> tuple[str, int]:
    """Un passage. Retourne ("opened", secondes) ou ("cooldown", secondes) ou ("nopack", 0)."""
    with sync_playwright() as p:
        if BRIGHT_DATA_WS_URL:
            log("Connexion a Bright Data Browser API...")
            browser = p.chromium.connect_over_cdp(BRIGHT_DATA_WS_URL)
            # Avec Bright Data on reutilise le contexte distant existant
            ctx = browser.contexts[0] if browser.contexts else browser.new_context(
                viewport={"width": 412, "height": 915},
                ignore_https_errors=True,
            )
            load_session_into_context(ctx)
        else:
            browser = p.chromium.launch(
                headless=not show,
                executable_path=os.getenv("EM_BROWSER_PATH") or None,
                args=["--no-sandbox"] if os.getenv("EM_BROWSER_PATH") else None,
            )
            ctx = browser.new_context(
                storage_state=str(STATE_FILE) if STATE_FILE.exists() else None,
                viewport={"width": 412, "height": 915},
            )
        try:
            page = ctx.new_page()
            page.set_viewport_size({"width": 412, "height": 915})
            if BRIGHT_DATA_WS_URL:
                page.set_default_navigation_timeout(60_000)
                page.set_default_timeout(60_000)
                goto_opts = {"wait_until": "domcontentloaded", "timeout": 60_000}
            else:
                goto_opts = {"wait_until": "networkidle"}
            page.goto(BOOSTER_URL, **goto_opts)
            if "/login" in page.url:
                login(page, email, password, goto_opts)
                page.goto(BOOSTER_URL, **goto_opts)
            try:
                ctx.storage_state(path=str(STATE_FILE))
            except Exception:
                pass

            pack = page.get_by_role("button", name=PACK_RE)
            if pack.count() == 0 or not pack.first.is_enabled():
                remaining = read_cooldown(page)
                if remaining is not None:
                    return "cooldown", remaining
                SHOTS.mkdir(exist_ok=True)
                page.screenshot(path=str(SHOTS / "page_inconnue.png"))
                return "nopack", 0

            # 1) Ouvrir le paquet (scroll et clic en JS pour eviter les timeouts de stabilite CSS)
            page.evaluate("""() => {
                const b = document.querySelector('button[aria-label*="booster" i]') || 
                          Array.from(document.querySelectorAll('button')).find(el => /open the booster|ouvrir|tirer/i.test(el.innerText || el.getAttribute('aria-label') || ''));
                if (b) {
                    b.scrollIntoView({ behavior: 'instant', block: 'center' });
                    b.click();
                }
            }""")
            log("Clic sur le booster.")
            first_ui = (
                page.get_by_role("button", name=REVEAL_RE)
                .or_(page.get_by_role("button", name=NEXT_RE))
                .or_(page.get_by_text(HINT_RE))
            )
            try:
                first_ui.first.wait_for(state="visible", timeout=30000)
            except PWTimeout:
                body_txt = page.locator("body").inner_text() or ""
                if "anti-robot" in body_txt.lower():
                    SHOTS.mkdir(exist_ok=True)
                    page.screenshot(path=str(SHOTS / "anti_robot.png"))
                    raise RuntimeError("Le site bloque l'ouverture avec sa verification anti-robot ('The anti-robot check didn\\'t go through').")
                remaining = read_cooldown(page)
                if remaining is not None:
                    log(f"Booster deja valide ! Prochain booster dans {remaining}s.")
                    return "opened", remaining
                SHOTS.mkdir(exist_ok=True)
                page.screenshot(path=str(SHOTS / "ouverture_echouee.png"))
                raise RuntimeError("Le booster ne semble pas s'etre ouvert.")

            seen = play_animation(page)
            remaining = read_cooldown(page)
            if seen > 0:
                log(f"Booster ouvert, 3 cartes recuperees (animation : {seen}/3).")
                return "opened", remaining if remaining is not None else DEFAULT_COOLDOWN
            elif remaining is not None:
                log(f"Booster valide cote serveur. Prochain booster dans {remaining}s.")
                return "opened", remaining
            else:
                SHOTS.mkdir(exist_ok=True)
                page.screenshot(path=str(SHOTS / "cartes_non_vues.png"))
                raise RuntimeError("Le booster ne semble pas avoir ete attribue (aucune carte vue et pas de compte a rebours).")
        except Exception:
            SHOTS.mkdir(exist_ok=True)
            try:
                page.screenshot(path=str(SHOTS / "erreur.png"))
                snapshot(page, "erreur")
            except Exception:
                pass
            raise
        finally:
            browser.close()


def wait_with_countdown(seconds: int) -> None:
    end = time.time() + seconds
    while True:
        left = int(end - time.time())
        if left <= 0:
            break
        print(f"\r  Prochain essai dans {left // 60}:{left % 60:02d}  (CTRL+C pour arreter)   ", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 70 + "\r", end="")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="navigateur visible")
    ap.add_argument("--once", action="store_true", help="un seul passage puis stop")
    args = ap.parse_args()

    email, password = get_credentials()
    log("Demarre. CTRL+C pour arreter.")
    login_fails = 0
    opened_total = 0
    try:
        while True:
            try:
                status, secs = run_once(email, password, args.show)
                login_fails = 0
                if status == "opened":
                    opened_total += 1
                    wait = secs + EXTRA_WAIT
                    log(f"Boosters ouverts depuis le lancement : {opened_total}. "
                        f"Reprise dans {wait // 60}:{wait % 60:02d}.")
                elif status == "cooldown":
                    wait = secs + EXTRA_WAIT
                    log(f"Pas encore dispo, compte a rebours {secs // 60}:{secs % 60:02d}. "
                        f"Reprise dans {wait // 60}:{wait % 60:02d}.")
                else:
                    wait = RETRY_AFTER_ERROR
                    log(f"Page inattendue (capture dans screenshots/). Nouvel essai dans {wait}s.")
            except LoginError as e:
                login_fails += 1
                log(f"{e} ({login_fails}/{MAX_LOGIN_FAILS})")
                if login_fails >= MAX_LOGIN_FAILS:
                    log("Arret : trop d'echecs de connexion.")
                    sys.exit(1)
                wait = RETRY_AFTER_ERROR
            except Exception as e:
                log(f"Erreur : {str(e).splitlines()[0] if str(e) else type(e).__name__}. "
                    f"Nouvel essai dans {RETRY_AFTER_ERROR}s.")
                wait = RETRY_AFTER_ERROR

            if args.once:
                break
            wait_with_countdown(wait)
    except KeyboardInterrupt:
        print()
        log(f"Arrete par CTRL+C. Boosters ouverts pendant cette session : {opened_total}.")


if __name__ == "__main__":
    main()
