#!/usr/bin/env python3
"""
בונה את קובץ הנתונים המוצפן של האתר:
1. מוריד את ה-KML של מפת My Maps ושולף רק את השכבות המאושרות (שם, איתור, אשכול, מיקום).
2. מוריד את לשונית "טלפונים" מהגיליון ומצמיד טלפונים לכל משפחה.
3. מצפין הכול בסיסמה (AES-GCM, מפתח מ-PBKDF2) – באתר יושב רק קובץ מוצפן.

שום נתון גלוי לא נשמר במאגר. נשמר רק טביעת אצבע (hash) כדי לדעת אם משהו השתנה.
מזהי המפה והגיליון והסיסמה מגיעים ממשתני סביבה (Secrets) ואינם מודפסים ליומן.

לבדיקה מקומית: KML_FILE / CSV_FILE במקום הורדה.
"""
import base64
import csv
import hashlib
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data" / "data.enc.json"
STATE = ROOT / ".state" / "data.sha256"
ITERATIONS = 250_000

BIDI = re.compile(r"[‎‏‪-‮⁦-⁩֑-ׇ]")
TAGS = re.compile(r"<[^>]+>")
RE_LOC = re.compile(r"איתור\s*:?\s*(\d+)")
RE_CLUSTER = re.compile(r"אשכול\s*:?\s*(\d+)")
TITLES = {"הרב", "ד\"ר", "דר"}
# שמות שמתחילים ב-ו' – לא לחתוך מהם "ו' החיבור" ("כהן אבי ורד" = אבי וורד)
VAV_NAMES = {"ורד", "ורדה", "ורדית", "ויקטוריה", "ויקטור", "ולדימיר", "ויטלי", "ואדים", "ולרי", "ולריה", "ויולט"}


def strip_vav(t: str) -> str:
    """'ומיכל' -> 'מיכל', 'וורד' -> 'ורד', אבל 'ורד' נשאר 'ורד'."""
    if t.startswith("וו") and len(t) > 2:
        return t[1:]
    if t.startswith("ו") and len(t) > 2 and t not in VAV_NAMES:
        return t[1:]
    return t


def fail(msg: str) -> None:
    print(f"שגיאה: {msg}", file=sys.stderr)
    sys.exit(1)


def clean(s: str) -> str:
    s = BIDI.sub("", s or "")
    s = TAGS.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def ident(env: str) -> str:
    # משאירים רק תווים חוקיים במזהה (מסנן BOM ותווים נסתרים שנכנסים בהעתקה)
    return re.sub(r"[^A-Za-z0-9_-]", "", os.environ.get(env, ""))


def fetch(url: str, what: str) -> bytes:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001
        fail(f"הורדת {what} נכשלה ({type(e).__name__}) – בדקו שהשיתוף בקישור פעיל")


# ---------------- מפה ----------------
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
            return (round(sum(p[0] for p in pts) / len(pts), 6),
                    round(sum(p[1] for p in pts) / len(pts), 6))
    return None


RE_CLUSTER_NAME = re.compile(r"^אשכול\s*(\d+)\s*(.*)$")


def kml_color(c: str) -> str:
    """KML: aabbggrr → #rrggbb"""
    c = (c or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{8}", c):
        return ""
    return "#" + c[6:8] + c[4:6] + c[2:4]


def load_clusters(raw: bytes) -> list:
    """גבולות האשכולות: מצולעים בשם 'אשכול N' מכל שכבה. נשמרים רק מספר, תווית, צבע וגבול."""
    root = ET.fromstring(raw)
    styles, maps = {}, {}
    for el in root.iter():
        tag = local(el.tag)
        if tag == "Style" and el.get("id"):
            col = ""
            for sub in el.iter():
                if local(sub.tag) == "LineStyle":
                    c = child(sub, "color")
                    col = kml_color(c.text if c is not None else "")
            styles["#" + el.get("id")] = col
        elif tag == "StyleMap" and el.get("id"):
            for pair in el:
                k, u = child(pair, "key"), child(pair, "styleUrl")
                if k is not None and u is not None and (k.text or "").strip() == "normal":
                    maps["#" + el.get("id")] = (u.text or "").strip()
    out = {}
    for pm in root.iter():
        if local(pm.tag) != "Placemark":
            continue
        n_el = child(pm, "name")
        m = RE_CLUSTER_NAME.match(clean(n_el.text if n_el is not None else ""))
        if not m:
            continue
        ring = None
        for el in pm.iter():
            if local(el.tag) == "outerBoundaryIs":
                for c in el.iter():
                    if local(c.tag) == "coordinates" and c.text:
                        ring = c.text
        if not ring:
            continue
        pts = []
        for t in ring.split():
            parts = t.split(",")
            try:
                pts.append([round(float(parts[1]), 6), round(float(parts[0]), 6)])
            except (IndexError, ValueError):
                pass
        if len(pts) < 3:
            continue
        su = child(pm, "styleUrl")
        su = (su.text or "").strip() if su is not None else ""
        color = styles.get(maps.get(su, su), "") or "#607d8b"
        num = m.group(1)
        out[num] = {"id": num, "label": clean(m.group(2)), "color": color, "poly": pts}
    return sorted(out.values(), key=lambda c: int(c["id"]))


def in_poly(lat: float, lon: float, poly: list) -> bool:
    inside = False
    for i in range(len(poly)):
        y1, x1 = poly[i]
        y2, x2 = poly[i - 1]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def assign_clusters(items: list, clusters: list) -> int:
    """רשומה בלי מספר אשכול בתיאור – משויכת לפי המיקום שלה בתוך גבול האשכול."""
    n = 0
    for it in items:
        if it["c"] or it["lat"] is None:
            continue
        for c in clusters:
            if in_poly(it["lat"], it["lon"], c["poly"]):
                it["c"] = c["id"]
                n += 1
                break
    return n


def load_map(raw: bytes, layers: set) -> list:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        fail("התקבל קובץ מפה שאינו KML – ייתכן שהמפה אינה משותפת")
    items, found = [], set()
    for folder in root.iter():
        if local(folder.tag) != "Folder":
            continue
        name_el = child(folder, "name")
        fname = clean(name_el.text if name_el is not None else "")
        if fname not in layers:
            continue
        found.add(fname)
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
    missing = layers - found
    if missing:
        fail(f"שכבות לא נמצאו במפה: {', '.join(sorted(missing))}")
    if not items:
        fail("לא נמצאו רשומות בשכבות המאושרות")
    return items


# ---------------- טלפונים ----------------
def norm_phone(s: str, home: bool = False) -> str:
    d = re.sub(r"\D", "", s or "")
    if d.startswith("972"):
        d = "0" + d[3:]
    if len(d) == 9 and d[0] == "5":          # נייד שאיבד את ה-0
        d = "0" + d
    if home and len(d) == 7:                 # קווי בלי קידומת – באזור 02
        d = "02" + d
    return d if 9 <= len(d) <= 10 else ""


def name_tokens(s: str) -> list:
    s = clean(s).replace("–", "-")
    s = re.sub(r"\s*-\s*", "-", s)
    return [t for t in s.split(" ") if t and t not in TITLES]


def split_family(s: str):
    """'פרנקל בנימין ומיכל' -> ('פרנקל', ['בנימין', 'מיכל'])"""
    toks = name_tokens(s)
    if not toks:
        return "", []
    given = [strip_vav(t) if i > 0 else t for i, t in enumerate(toks[1:])]
    return toks[0], given


def load_phones(raw: bytes) -> list:
    text = raw.decode("utf-8-sig", errors="replace")
    if text.lstrip().startswith("<"):
        fail("התקבל דף HTML במקום הגיליון – ייתכן שהגיליון אינו משותף")
    rows = list(csv.reader(io.StringIO(text)))
    header = None
    for i, r in enumerate(rows):
        cells = [clean(c) for c in r]
        if cells and cells[0].startswith("משפחה") and "איש" in cells and "אישה" in cells:
            header = i
            col = {name: cells.index(name) for name in ("בית", "איש", "אישה")}
            break
    if header is None:
        fail('לא נמצאה שורת כותרת ("משפחה, בית, איש, אישה") בלשונית הטלפונים')

    out = []
    for r in rows[header + 1:]:
        if not r or not clean(r[0]):
            continue
        get = lambda k: r[col[k]] if col[k] < len(r) else ""  # noqa: E731
        fam = clean(r[0])
        surname, given = split_family(fam)
        man, woman, home = norm_phone(get("איש")), norm_phone(get("אישה")), norm_phone(get("בית"), home=True)
        if not (man or woman or home):
            continue
        if len(given) >= 2:
            nm, nw = given[-2], given[-1]
        elif len(given) == 1:
            nm = nw = given[0]
        else:
            nm = nw = ""
        out.append({"fam": fam, "surname": surname, "given": set(given),
                    "pm": man, "pw": woman, "ph": home, "nm": nm, "nw": nw})
    return out


def match_tokens(s: str):
    """מילים להשוואה: מפרקים מקפים וסוגריים, מורידים ו' החיבור ותארים.
    מחזיר (רשימת זוגות (מילה מנורמלת, שלד), כמה מהמילים הראשונות הן שם המשפחה).
    'בזיז-רפאל אבי ובתיה' -> שם המשפחה הוא שתי המילים הראשונות."""
    s = re.sub(r"[()\"'׳״]", " ", clean(s)).replace("–", "-")
    s = re.sub(r"\s*-\s*", "-", s).strip()
    out, sur = [], 0
    for chunk in s.split(" "):
        for t in [x for x in chunk.split("-") if x]:
            if t in TITLES:
                continue
            if out:
                t = strip_vav(t)
            t = t.translate(FINALS_MAP)
            out.append((t, skeleton(t)))
        if out and not sur:
            sur = len(out)
    return out, sur


FINALS_MAP = str.maketrans("ךםןףץ", "כמנפצ")
SKEL_MAP = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ", "ק": "כ", "ט": "ת"})


def skeleton(t: str) -> str:
    """שלד לשם: בלי אהו"י, ק=כ, ט=ת, סופיות כרגילות.
    כך 'שלו'='שליו', 'יעקבי'='יעקובי', 'רבלין'='ריבלין', 'קליפה'='כליפה'."""
    t = t.translate(SKEL_MAP)
    s = re.sub(r"[אהוי]", "", t)
    return s if len(s) >= 2 else t


def common_tokens(mt: list, pt: list, ms_n: int = 1, ps_n: int = 1):
    """סופר מילים משותפות ובודק אם שמות המשפחה תואמים.
    מילה זהה – תמיד נספרת. התאמה לפי שלד בלבד נספרת רק אם השלד באורך 3 ומעלה, או ששתיהן
    בעמדת שם המשפחה – כדי ש'בן'/'הבן' או 'חן'/'אוחנה' לא ייצרו התאמות שגויות.
    מחזיר (מספר מילים משותפות, האם יש מילה משותפת שהיא שם משפחה בשני הצדדים)."""
    used, n, sur = set(), 0, False
    for i, (mw, ms) in enumerate(mt):
        for j, (pw, ps) in enumerate(pt):
            if j in used:
                continue
            both_sur = i < ms_n and j < ps_n
            if mw == pw or (ms == ps and (len(ms) >= 3 or both_sur)):
                used.add(j)
                n += 1
                sur = sur or both_sur
                break
    return n, sur


def attach_phones(items: list, phones: list):
    """מצמיד לכל רשומת מפה את שורת הטלפונים המתאימה.
    התאמה = (שם משפחה תואם + לפחות 2 מילים משותפות) או (לפחות 3 מילים משותפות).
    שם ממוקף ('בזיז-רפאל') נחשב כולו שם משפחה.
    כך 'רפאל אבי' מתאים ל-'בזיז-רפאל אבי ובתיה', 'משה ורננה בר ששת' ל-'בר ששת משה ורננה',
    אבל 'דוד משה ורחל' לא מתאים ל-'כהן דוד ורחל', ו-'לוין יוסף' לא ל-'לוין צבי'."""
    ptoks = [match_tokens(p["fam"]) for p in phones]
    used = set()
    for it in items:
        mt, msur = match_tokens(it["n"])
        if not mt:
            continue
        best, best_score = None, 0
        for idx, (pt, psur) in enumerate(ptoks):
            if not pt:
                continue
            n_common, sur_match = common_tokens(mt, pt, msur, psur)
            if not ((sur_match and n_common >= 2) or n_common >= 3):
                continue
            score = n_common * 10 - abs(len(mt) - len(pt))
            if score > best_score:
                best, best_score = idx, score
        if best is not None:
            p = phones[best]
            used.add(best)
            for k in ("pm", "pw", "ph", "nm", "nw"):
                if p[k]:
                    it[k] = p[k]
    # משפחות שיש להן טלפון אבל אין להן נקודה במפה – נכנסות לחיפוש בלי מיקום
    extra = []
    for idx, p in enumerate(phones):
        if idx in used:
            continue
        rec = {"n": p["fam"], "l": "", "c": "", "lat": None, "lon": None}
        for k in ("pm", "pw", "ph", "nm", "nw"):
            if p[k]:
                rec[k] = p[k]
        extra.append(rec)
    return len(used), extra


# ---------------- הצפנה ----------------
def encrypt(payload: bytes, password: str) -> dict:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=ITERATIONS).derive(password.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, payload, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITERATIONS,
            "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def main() -> None:
    layers = {clean(x) for x in os.environ.get("LAYERS", "").split("|") if x.strip()}
    # אותו נרמול כמו בדפדפן: בלי רווחים ותווים נסתרים, ואותיות סופיות כרגילות
    password = re.sub(r"[\s\ufeff\u200b-\u200f\u202a-\u202e\u2066-\u2069]", "", os.environ.get("SITE_PASSWORD", ""))
    password = password.translate(str.maketrans("ךםןףץ", "כמנפצ"))
    if not layers:
        fail("חסרה רשימת שכבות (LAYERS)")
    if not password:
        fail("חסרה סיסמה (SITE_PASSWORD)")

    if os.environ.get("KML_FILE"):
        kml = Path(os.environ["KML_FILE"]).read_bytes()
    else:
        map_id = ident("MAP_ID") or fail("חסר MAP_ID")
        kml = fetch(f"https://www.google.com/maps/d/kml?forcekml=1&mid={map_id}", "המפה")
    items = load_map(kml, layers)
    clusters = load_clusters(kml)
    n_assigned = assign_clusters(items, clusters)

    matched, extra, n_phones = 0, [], 0
    if os.environ.get("CSV_FILE"):
        csv_raw = Path(os.environ["CSV_FILE"]).read_bytes()
    elif ident("SHEET_ID"):
        sheet, gid = ident("SHEET_ID"), ident("SHEET_GID") or "0"
        csv_raw = fetch(f"https://docs.google.com/spreadsheets/d/{sheet}/export?format=csv&gid={gid}", "הגיליון")
    else:
        csv_raw = None
    if csv_raw is not None:
        phones = load_phones(csv_raw)
        n_phones = len(phones)
        matched, extra = attach_phones(items, phones)
        items += extra

    items.sort(key=lambda x: (int(x["l"]) if x["l"] else 10**9, x["n"]))
    payload = {"items": items, "clusters": clusters}
    api = os.environ.get("SCAN_API", "").strip()
    if re.fullmatch(r"https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec", api):
        payload["api"] = api   # כתובת שרת הסריקות – רק בתוך הקובץ המוצפן
    tok = os.environ.get("SCAN_TOKEN", "").strip()
    if re.fullmatch(r"[0-9a-f]{32,128}", tok):
        payload["tok"] = tok   # טוקן אקראי לשרת – לא נגזר מהסיסמה
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(plain).hexdigest()

    # הקובץ המוצפן נוצר תמיד (לפרסום), אבל טביעת האצבע קובעת אם יש שינוי
    data = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    data.update(encrypt(plain, password))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

    old = STATE.read_text().strip() if STATE.exists() else ""
    changed = old != digest
    if changed:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(digest + "\n")
    print(f"רשומות במפה: {len(items) - len(extra)} | שורות טלפונים: {n_phones} | "
          f"הותאמו: {matched} | נוספו ללא מיקום: {len(extra)} | "
          f"אשכולות: {len(clusters)} (שויכו לפי מיקום: {n_assigned}) | "
          f"שרת סריקות: {'מחובר' if 'api' in payload and 'tok' in payload else 'לא מוגדר'} | "
          f"{'יש שינוי' if changed else 'אין שינוי'}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")


if __name__ == "__main__":
    main()
