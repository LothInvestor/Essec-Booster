"""
Rappel booster Essec Masters : te previent quand ton booster est pret.
Il ne se connecte PAS au site : tu ouvres le booster toi-meme, puis tu appuies sur Entree.

Notifications :
  - bip + message sur le PC
  - notification sur ton telephone via l'appli gratuite ntfy (optionnel) :
      1. installe "ntfy" sur ton telephone
      2. abonne-toi a un nom de sujet difficile a deviner, ex : essec-charles-8f3k2
      3. ajoute dans le fichier .env : EM_NTFY_TOPIC=essec-charles-8f3k2

Lancement : python rappel_booster.py      (CTRL+C pour arreter)
"""
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

COOLDOWN = 10 * 60  # 10 minutes

HERE = Path(__file__).resolve().parent
env = HERE / ".env"
if env.exists():
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
TOPIC = os.getenv("EM_NTFY_TOPIC", "").strip()


def notify(msg: str) -> None:
    print(f"\n[{datetime.now():%H:%M:%S}] {msg}")
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(880, 250)
    except Exception:
        print("\a", end="")
    if TOPIC:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{TOPIC}",
                data=msg.encode("utf-8"),
                headers={"Title": "Essec Masters", "Tags": "black_joker", "Priority": "high"},
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"  (notification telephone impossible : {e})")


def countdown(seconds: int) -> None:
    end = time.time() + seconds
    while (left := int(end - time.time())) > 0:
        print(f"\r  Booster pret dans {left // 60}:{left % 60:02d}   (CTRL+C pour arreter)  ",
              end="", flush=True)
        time.sleep(1)
    print()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("Rappel booster Essec Masters.")
    print("Notifications telephone :", f"actives (sujet {TOPIC})" if TOPIC else "desactivees (voir EM_NTFY_TOPIC)")
    count = 0
    try:
        while True:
            input("\nOuvre ton booster sur le site, puis appuie sur Entree... ")
            count += 1
            ready = datetime.now() + timedelta(seconds=COOLDOWN)
            print(f"  Booster n°{count} note. Prochain a {ready:%H:%M:%S}.")
            countdown(COOLDOWN)
            notify("Ton booster Essec Masters est pret !")
    except (KeyboardInterrupt, EOFError):
        print(f"\nArrete. Boosters ouverts pendant cette session : {count}.")


if __name__ == "__main__":
    main()
