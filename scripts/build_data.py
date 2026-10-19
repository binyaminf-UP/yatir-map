#!/usr/bin/env python3
"""
מוריד את ה-KML של מפת Google My Maps, שולף רק את השכבות המאושרות,
ושומר קובץ JSON מצומצם (שם, מספר איתור, אשכול, קואורדינטות) לאתר.

שכבות אחרות במפה, תיאורים חופשיים וכל מידע נוסף – לא נשמרים.
מזהה המפה מגיע ממשתנה סביבה (Secret ב-GitHub) ואינו מודפס ליומן.
"""
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "site" / "data" / "residents.json"
BIDI = re.compile(r"[‎‏‪-‮⁦-⁩]")
TAGS = re.compile(r"<[^>]+>")
RE_LOC = re.compile(r"איתור\s*:?\s*(\d+)")
RE_CLUSTER = re.compile(r"אשכול\s*:?\s*(\d+)")


def fail(msg: str) -> None:
    print(f"שגיאה: {msg}", file=sys.stderr)
    sys.exit(1)


def clean(s: str) -> str:
    s = BIDI.sub("", s or "")
    s = TAGS.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def point_of(pm):
    for el in pm.iter():
        if local(el.tag) == "coordinates" and el.text:
            pts = []
            for t in el.text.split():
                parts = t.split(",")
                try:
                    pts.append((float(parts[1]), float(parts[0])))
                except (IndexError, ValueError):
                    pass
            if not pts:
                return None
            if len(pts) > 2 and pts[0] == pts[-1]:
                pts = pts[:-1]
            lat = sum(p[0] for p in pts) / len(pts)
            lon = sum(p[1] for p in pts) / len(pts)
            return round(lat, 6), round(lon, 6)
    return None


def main() -> None:
    # משאירים רק תווים חוקיים במזהה (מסנן BOM ותווים נסתרים שנכנסים בהעתקה)
    map_id = re.sub(r"[^A-Za-z0-9_-]", "", os.environ.get("MAP_ID", ""))
    layers = {clean(x) for x in os.environ.get("LAYERS", "").split("|") if x.strip()}
    if not map_id:
        fail("חסר MAP_ID")
    if not layers:
        fail("חסרה רשימת שכבות (LAYERS)")

    url = f"https://www.google.com/maps/d/kml?forcekml=1&mid={map_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except Exception as e:  # noqa: BLE001
        fail(f"הורדת המפה נכשלה ({type(e).__name__}) – בדקו שהמפה משותפת בקישור")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        fail("התקבל קובץ שאינו KML – ייתכן שהמפה אינה משותפת")

    items, found_layers = [], set()
    for folder in root.iter():
        if local(folder.tag) != "Folder":
            continue
        name_el = child(folder, "name")
        fname = clean(name_el.text if name_el is not None else "")
        if fname not in layers:
            continue
        found_layers.add(fname)
        for pm in folder:
            if local(pm.tag) != "Placemark":
                continue
            n_el, d_el = child(pm, "name"), child(pm, "description")
            name = clean(n_el.text if n_el is not None else "")
            desc = clean(d_el.text if d_el is not None else "")
            loc = RE_LOC.search(desc) or RE_LOC.search(name)
            cl = RE_CLUSTER.search(desc) or RE_CLUSTER.search(name)
            pt = point_of(pm)
            if not name and not loc:
                continue
            items.append({
                "n": name,
                "l": loc.group(1) if loc else "",
                "c": cl.group(1) if cl else "",
                "lat": pt[0] if pt else None,
                "lon": pt[1] if pt else None,
            })

    missing = layers - found_layers
    if missing:
        fail(f"שכבות לא נמצאו במפה: {', '.join(sorted(missing))}")
    if not items:
        fail("לא נמצאו רשומות בשכבות המאושרות")

    items.sort(key=lambda x: (int(x["l"]) if x["l"] else 10**9, x["n"]))

    old_items = None
    if OUT.exists():
        try:
            old_items = json.loads(OUT.read_text(encoding="utf-8")).get("items")
        except (ValueError, OSError):
            pass
    if old_items == items:
        print(f"אין שינוי ({len(items)} רשומות)")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(items),
        "items": items,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"עודכן: {len(items)} רשומות"
          + (f" (היו {len(old_items)})" if old_items is not None else ""))


if __name__ == "__main__":
    main()
