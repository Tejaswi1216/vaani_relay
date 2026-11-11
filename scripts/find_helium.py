import json
import re

with open("data/text/moshi_v1.json") as f:
    pages = json.load(f)

for p in pages:
    matches = re.findall(r".{0,40}Helium.{0,60}", p["text"], re.IGNORECASE)
    if matches:
        print(f"Page {p['page']}: {len(matches)} occurrences")
        for m in matches[:2]:
            print(f"   {m.strip()}")
