"""Standort-Hilfen: PLZ/Ort -> Koordinaten, Entfernungen, Umkreis.

Es werden bewusst nur grobe Standorte gespeichert (PLZ-/Ortsmittelpunkt),
niemals Strassen oder Hausnummern. Anderen Nutzern wird nur "ca. X km" gezeigt.

Geocoding laeuft ueber Nominatim (OpenStreetMap), mit Photon (komoot) als
Rueckfallebene. Jedes Ergebnis wird dauerhaft in der Tabelle geo_cache
gespeichert, d. h. jede PLZ wird hoechstens einmal extern abgefragt.
Nutzungsregeln von Nominatim: max. 1 Anfrage/Sekunde, identifizierender
User-Agent - beides ist hier umgesetzt (siehe GEO_CONTACT).
"""
import json
import logging
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088
COUNTRIES = {"DE": "Deutschland", "AT": "Österreich", "CH": "Schweiz"}
RADIUS_CHOICES = [5, 10, 25, 50, 100, 200]

_PLZ_RE = {"DE": re.compile(r"^\d{5}$"), "AT": re.compile(r"^\d{4}$"), "CH": re.compile(r"^\d{4}$")}

_rate_lock = threading.Lock()
_last_request = 0.0


# ---------- Mathe ----------

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bounding_box(lat, lon, km):
    """Grobes Rechteck um einen Punkt - als schneller Vorfilter in der Datenbank."""
    dlat = km / 111.0
    dlon = km / (111.0 * max(0.01, math.cos(math.radians(lat))))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def format_distance(km) -> str:
    if km is None:
        return ""
    if km < 1:
        return "weniger als 1 km"
    if km < 10:
        return f"ca. {round(km)} km"
    return f"ca. {int(round(km / 5.0) * 5)} km"  # auf 5 km runden: schuetzt die Privatsphaere


# ---------- Eingabe ----------

def parse_query(text: str, country: str = "DE"):
    """Gibt ('plz', '10115') oder ('city', 'Berlin') zurueck, sonst None."""
    text = (text or "").strip()
    if not text:
        return None
    m = re.match(r"^(\d{4,5})(?:\s+(.+))?$", text)
    if m:
        plz = m.group(1)
        if not _PLZ_RE.get(country, _PLZ_RE["DE"]).match(plz):
            return None
        return ("plz", plz)
    if re.match(r"^[^\W\d_][\w\s.\-'’(),/äöüÄÖÜß]*$", text) and len(text) <= 80:
        return ("city", " ".join(text.split()))
    return None


# ---------- HTTP ----------

def _contact() -> str:
    return os.environ.get("GEO_CONTACT", "kontakt@seitentausch.invalid")


def _http_json(url: str, timeout: float = 5.0):
    """Eigene kleine Funktion, damit Tests sie ersetzen koennen."""
    req = urllib.request.Request(
        url, headers={"User-Agent": f"Seitentausch/1.0 ({_contact()})", "Accept-Language": "de"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _throttle():
    """Hoechstens eine externe Anfrage pro Sekunde (Nominatim-Regel)."""
    global _last_request
    with _rate_lock:
        wait = 1.05 - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _nominatim(kind, value, country):
    params = {"format": "jsonv2", "limit": 1, "addressdetails": 1, "countrycodes": country.lower()}
    params["postalcode" if kind == "plz" else "city"] = value
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
    _throttle()
    data = _http_json(url)
    if not data:
        return None
    hit = data[0]
    addr = hit.get("address", {})
    city = (addr.get("city") or addr.get("town") or addr.get("village")
            or addr.get("municipality") or addr.get("county") or "")
    plz = addr.get("postcode") or (value if kind == "plz" else "")
    return {"lat": float(hit["lat"]), "lon": float(hit["lon"]), "city": city, "plz": plz.split(";")[0]}


def _photon(kind, value, country):
    q = f"{value} {COUNTRIES.get(country, '')}".strip()
    url = "https://photon.komoot.io/api/?" + urllib.parse.urlencode({"q": q, "limit": 1, "lang": "de"})
    _throttle()
    data = _http_json(url)
    feats = (data or {}).get("features") or []
    if not feats:
        return None
    props = feats[0].get("properties", {})
    if props.get("countrycode") and props["countrycode"].upper() != country:
        return None
    lon, lat = feats[0]["geometry"]["coordinates"][:2]
    city = props.get("city") or props.get("name") or ""
    plz = props.get("postcode") or (value if kind == "plz" else "")
    return {"lat": float(lat), "lon": float(lon), "city": city, "plz": plz}


# ---------- Hauptfunktion ----------

def geocode(text: str, country: str = "DE"):
    """PLZ oder Ortsname -> {'lat','lon','city','plz'} oder None.

    Ergebnisse werden dauerhaft gecacht. Gibt None zurueck, wenn nichts
    gefunden wurde oder kein Geocoding-Dienst erreichbar war.
    """
    from extensions import db
    from models import GeoCache

    country = (country or "DE").upper()
    parsed = parse_query(text, country)
    if not parsed:
        return None
    kind, value = parsed
    key = f"{country}:{kind}:{value.lower()}"

    cached = GeoCache.query.filter_by(query_key=key).first()
    if cached and cached.lat is not None:
        label = (cached.label or "").split("|")
        return {"lat": cached.lat, "lon": cached.lon,
                "plz": label[0] if label else "", "city": label[1] if len(label) > 1 else ""}

    result = None
    for provider in (_nominatim, _photon):
        try:
            result = provider(kind, value, country)
        except Exception as exc:  # Netzwerk, Timeout, Rate-Limit ...
            log.warning("Geocoding via %s fehlgeschlagen: %s", provider.__name__, exc)
            result = None
        if result:
            break
    if not result:
        return None

    try:
        db.session.add(GeoCache(
            query_key=key, lat=result["lat"], lon=result["lon"],
            label=f"{result.get('plz', '')}|{result.get('city', '')}",
        ))
        db.session.commit()
    except Exception:  # paralleler Insert o. ae. - Cache ist nur Komfort
        db.session.rollback()
    return result
