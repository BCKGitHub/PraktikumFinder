"""Rebuild app/seed_data/postcodes_de.csv.gz from GeoNames postal codes (CC BY 4.0).

Source: zipcodes.de.csv inside data/DE.zip of https://github.com/zauberware/postal-codes-json-xml-csv,
a conversion of http://www.geonames.org/ data.

  python scripts/build_postcodes.py zipcodes.de.csv app/seed_data/postcodes_de.csv.gz

Large-customer postcodes (companies, banks, authorities) are skipped. Where one postcode covers
several places, the coordinates are averaged and the first place name is kept.
"""
from __future__ import annotations

import collections
import csv
import gzip
import io
import re
import sys

STATES = {
    "Baden-Württemberg", "Bayern", "Berlin", "Brandenburg", "Bremen", "Hamburg", "Hessen", "Mecklenburg-Vorpommern",
    "Niedersachsen", "Nordrhein-Westfalen", "Rheinland-Pfalz", "Saarland", "Sachsen", "Sachsen-Anhalt",
    "Schleswig-Holstein", "Thüringen",
}
COMPANY = re.compile(r"\b(GmbH|AG|KG|mbH|e\.V\.|SE|Postfach|Bank|Versicherung|Großkunde|GKD|Verlag|Universität|Finanzamt)\b", re.I)


def build(src: str, dst: str) -> int:
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    with open(src, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            code = r["zipcode"].strip()
            if len(code) == 5 and code.isdigit() and r["state"] in STATES and r["latitude"]:
                groups[code].append(r)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\n")
    w.writerow(["plz", "ort", "bundesland", "lat", "lon"])
    n = 0
    for code in sorted(groups):
        rows = [r for r in groups[code] if not COMPANY.search(r["place"])]
        if not rows:
            continue
        state = collections.Counter(r["state"] for r in rows).most_common(1)[0][0]
        rows = [r for r in rows if r["state"] == state]
        lat = sum(float(r["latitude"]) for r in rows) / len(rows)
        lon = sum(float(r["longitude"]) for r in rows) / len(rows)
        w.writerow([code, rows[0]["place"], state, f"{lat:.4f}", f"{lon:.4f}"])
        n += 1
    with gzip.open(dst, "wt", encoding="utf-8", compresslevel=9) as fh:
        fh.write(out.getvalue())
    return n


if __name__ == "__main__":
    print(build(sys.argv[1], sys.argv[2]), "Postleitzahlen geschrieben.")
