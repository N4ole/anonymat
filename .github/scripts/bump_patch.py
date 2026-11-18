"""Version correctrice automatique : X.Y.Z -> X.Y.(Z+1) dans version.py + section CHANGELOG.

Usage : python bump_patch.py "ligne 1" "ligne 2" ...   (lignes = changements inclus)
Affiche la nouvelle version.
"""

import datetime
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
vfile = root / "version.py"
text = vfile.read_text(encoding="utf-8")
m = re.search(r'__version__ = "(\d+)\.(\d+)\.(\d+)"', text)
if not m:
    sys.exit("version.py : __version__ introuvable")
new = f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"
vfile.write_text(text.replace(m.group(0), f'__version__ = "{new}"'), encoding="utf-8")

changes = [c.strip() for c in sys.argv[1:] if c.strip()] or ["mises à jour internes"]
section = (f"## [{new}] - {datetime.date.today().isoformat()}\n\n"
           "### Sécurité\n"
           "- Mise à jour automatique des dépendances, testée par la CI (tests + compilation + auto-test du .exe) :\n"
           + "".join(f"  - {c}\n" for c in changes) + "\n")
cl = root / "CHANGELOG.md"
log = cl.read_text(encoding="utf-8")
i = log.find("\n## [")
log = log + "\n" + section if i < 0 else log[:i + 1] + section + log[i + 1:]
cl.write_text(log, encoding="utf-8")
print(new)
