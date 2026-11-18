"""Affiche la section du CHANGELOG pour une version (échoue si elle est absente)."""

import re
import sys
from pathlib import Path

version = sys.argv[1]
text = Path(__file__).resolve().parents[2].joinpath("CHANGELOG.md").read_text(encoding="utf-8")
m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.S | re.M)
if not m or not m.group(1).strip():
    sys.exit(f"CHANGELOG.md : aucune section « ## [{version}] ». Ajoute-la avant de publier.")
print(m.group(1).strip())
