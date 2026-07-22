"""
weather.py — RaceFusion geocoding, track lookup, and weather data functions.

Exports:
  geocode(), lookup_track(), _track_key(), _TRACK_OVERRIDES
  fetch_weather(), fetch_weather_rdp(), fetch_metar()
  calc_density_altitude(), sea_level_to_station_pressure()
  wind_dir_label(), _haversine_km()
  _get_weatherkit_token(), _fetch_weatherkit_current()
  _wk_parse_hourly(), _wk_msl_to_station(), _fetch_track_elev_ft()
"""

import math
import re
import requests
import streamlit as st

from database import _sb, _get_secret


# ── Geocoding ─────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Geocoding location…")
def geocode(location: str) -> tuple[float | None, float | None, str]:
    """
    Accepts:
      - "lat, lon"  e.g. "42.694, -88.059"  → used directly
      - Any city/place name string           → queried via Open-Meteo geocoding API
    Tries progressively simpler forms of the name until one hits.
    """
    location = location.strip()

    # ── Direct coordinates? ──────────────────────────────────────────────────
    coord_match = re.match(r"^(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)$", location)
    if coord_match:
        lat, lon = float(coord_match.group(1)), float(coord_match.group(2))
        return lat, lon, f"{lat:.4f}, {lon:.4f}"

    # ── Try progressively simpler search terms ───────────────────────────────
    # e.g. "Union Grove, WI" → try "Union Grove, WI", then "Union Grove WI", then "Union Grove"
    candidates = [location]
    if "," in location:
        # Replace comma+space with just space
        candidates.append(location.replace(", ", " ").replace(",", " "))
        # Try just the city part before the first comma
        candidates.append(location.split(",")[0].strip())

    url = "https://geocoding-api.open-meteo.com/v1/search"
    for candidate in candidates:
        try:
            r = requests.get(url, params={"name": candidate, "count": 1}, timeout=10)
            data = r.json()
            if data.get("results"):
                res = data["results"][0]
                label = f"{res.get('name','')}, {res.get('admin1','')}, {res.get('country','')}".strip(", ")
                return res["latitude"], res["longitude"], label
        except Exception:
            continue

    return None, None, location


# ── Track lookup with Supabase cache ─────────────────────────────────────────
# In-process cache so the same track isn't re-queried within one Streamlit render
_track_cache: dict[str, dict | None] = {}

# Verified exact coordinates for known tracks — these override geocoding entirely
_TRACK_OVERRIDES: dict[str, dict] = {
    "great lakes dragaway": {
        "lat": 42.6586, "lon": -88.0324, "elev_ft": 715.2,
        "display_name": "Great Lakes Dragaway, Union Grove, WI",
    },
    "tri state raceway": {
        "lat": 42.4680, "lon": -91.2857, "elev_ft": 1059.7,
        "display_name": "Tri-State Raceway, Earlville, IA",
    },
    "summit motorsports park": {
        "lat": 41.2389, "lon": -82.5454, "elev_ft": 876.0,
        "display_name": "Summit Motorsports Park, Norwalk, OH",
    },
}


def _track_key(name: str) -> str:
    """Normalise a track name for deduplication.
    Lowercases, strips punctuation, and collapses whitespace so that
    'Great Lakes DRAGAWAY', 'Great Lakes Dragaway', and 'great lakes dragaway'
    all produce the same key.
    """
    s = re.sub(r"[^\w\s]", " ", (name or "").lower())  # strip punctuation → space
    return re.sub(r"\s+", " ", s).strip()


def _auto_track_key(name: str) -> str:
    """_track_key plus auto-stripping of venue-preserving ' at <place>' suffixes.

    'Lucas Oil Raceway Park At Indianapolis' and 'Lucas Oil Raceway Park'
    both normalise to 'lucas oil raceway park'. The suffix is only stripped
    when at least two words precede ' at ', so short legitimate names are
    never mangled. Pure text normalisation — no geocoding.
    """
    k = _track_key(name)
    parts = k.split(" at ", 1)
    if len(parts) == 2 and len(parts[0].split()) >= 2:
        return parts[0].strip()
    return k


def canonical_track(name: str, aliases: "dict | None" = None) -> "tuple[str, str]":
    """Return (group_key, display_name) for a raw timeslip track name.

    Applies auto-normalisation (_auto_track_key) and then the user's manual
    merges from cfg['track_aliases'] ({auto_key: canonical display name}).
    The raw text stored in each run's timeslip is never modified — merging
    only changes how runs are grouped and labelled.
    """
    raw = (name or "").strip()
    key = _auto_track_key(raw)
    canon = (aliases or {}).get(key)
    if canon:
        return _auto_track_key(canon), canon.strip()
    return key, (raw.title() if raw else "")


def build_track_display_map(raw_names, aliases: "dict | None" = None) -> dict:
    """Map canonical group_key → ONE consistent display name for a set of runs.

    canonical_track() alone falls back to each run's raw text for display, so
    two buckets of the same canonical track can show different labels (e.g.
    'Lucas Oil Raceway Park' vs 'Lucas Oil Raceway Park At Indianapolis').
    This picks a single deterministic representative per key:
      1. a manual-merge alias target, if one points at this key
      2. otherwise the SHORTEST raw variant seen (title-cased) — the base name
         wins over suffixed variants, and single-variant tracks keep their
         exact raw text.
    """
    aliases = aliases or {}
    # Alias display names, keyed by the canonical key they resolve to
    _alias_disp = {_auto_track_key(v): v.strip() for v in aliases.values() if v}

    _best_raw: dict = {}
    for raw in raw_names:
        raw = (raw or "").strip()
        if not raw:
            continue
        key = canonical_track(raw, aliases)[0]
        cur = _best_raw.get(key)
        if cur is None or len(raw) < len(cur):
            _best_raw[key] = raw

    return {
        key: (_alias_disp.get(key) or raw.title())
        for key, raw in _best_raw.items()
    }


def lookup_track(track_name: str, city_state: str = "") -> dict | None:
    """
    Return {"lat", "lon", "elev_ft", "display_name"} for a drag strip.

    Resolution order:
      1. In-process memory cache (same render)
      2. Supabase `tracks` table (persistent cross-user cache)
      3. Nominatim (OpenStreetMap) — tries drag-racing-specific queries first
      4. Open-Meteo geocoder with city/state as fallback
    On success, stores the result in the tracks table for future calls.
    Returns None if the track cannot be located.
    """
    key = _track_key(track_name or city_state)
    if not key:
        return None

    # 0. Hardcoded overrides — verified exact coordinates, always win.
    #    Use _track_key() so whitespace normalization matches the dict keys.
    #    Check track_name first, then city_state as fallback.
    _ov = _TRACK_OVERRIDES.get(_track_key(track_name)) or _TRACK_OVERRIDES.get(_track_key(city_state))
    if _ov is not None:
        print(f"[lookup_track] OVERRIDE hit for '{track_name or city_state}' → "
              f"lat={_ov['lat']}, lon={_ov['lon']}, elev_ft={_ov.get('elev_ft')}")
        _track_cache[key] = _ov
        return _ov

    # 1. In-process memory cache (same render pass)
    if key in _track_cache:
        return _track_cache[key]

    # 1b. Session-state cache (survives re-renders within the same session)
    _ss_geo = st.session_state.setdefault("_track_geo_cache", {})
    if key in _ss_geo:
        _track_cache[key] = _ss_geo[key]
        return _ss_geo[key]

    # 2. Supabase tracks table
    if _sb:
        try:
            rows = _sb.table("tracks").select("*").eq("name_key", key).execute().data
            if rows:
                r = rows[0]
                result = {
                    "lat": r["lat"], "lon": r["lon"],
                    "elev_ft": r.get("elev_ft"),
                    "display_name": r.get("display_name") or track_name,
                }
                _track_cache[key] = result
                return result
        except Exception:
            pass  # tracks table may not exist yet — continue to geocode

    # 3. Nominatim (OpenStreetMap) — drag-racing-specific queries
    lat, lon, display = None, None, track_name or city_state

    queries = []
    if track_name:
        queries += [
            f"{track_name} dragstrip",
            f"{track_name} dragway",
            f"{track_name} drag strip",
        ]
        if city_state:
            queries.append(f"{track_name} {city_state}")
        queries.append(track_name)
        # Additional retries for tracks that don't match "dragstrip/dragway" keywords:
        # 1. Explicit "drag strip" suffix (catches "raceway" tracks)
        queries.append(f"{track_name} drag strip")
        # 2. First two lexical words (hyphens treated as spaces) + "raceway"
        _nom_words = track_name.replace("-", " ").split()
        if len(_nom_words) >= 2:
            queries.append(f"{_nom_words[0]} {_nom_words[1]} raceway")

    for q in queries:
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": q, "format": "json", "limit": 3,
                        "addressdetails": "0", "email": "chris@weebenterprises.com"},
                headers={"User-Agent": "RaceFusion/1.0 chris@weebenterprises.com"},
                timeout=10,
            )
            hits = r.json()
            if hits:
                h = hits[0]
                lat, lon = float(h["lat"]), float(h["lon"])
                display = h.get("display_name", track_name) or track_name
                break
        except Exception:
            continue

    # 4. Open-Meteo geocoder fallback (uses city/state, good for small towns)
    if lat is None and city_state:
        lat_m, lon_m, label_m = geocode(city_state)
        if lat_m is not None:
            lat, lon, display = lat_m, lon_m, label_m or city_state

    if lat is None:
        _track_cache[key] = None
        return None

    # Fetch elevation via open-elevation.com
    elev_ft: float | None = None
    try:
        er = requests.get(
            "https://api.open-elevation.com/api/v1/lookup",
            params={"locations": f"{lat},{lon}"},
            timeout=10,
        )
        em = (er.json().get("results") or [{}])[0].get("elevation")
        if em is not None:
            elev_ft = float(em) / 0.3048  # metres → feet
    except Exception:
        pass  # non-fatal; DA will use 0 ft as fallback

    # Shorten Nominatim's verbose display_name to something readable
    short_display = display.split(",")[0].strip() if display else (track_name or city_state)

    result = {"lat": lat, "lon": lon, "elev_ft": elev_ft, "display_name": short_display}

    # Cache in session state (persists across re-renders in this session)
    st.session_state.setdefault("_track_geo_cache", {})[key] = result

    # Cache in Supabase tracks table (gracefully skip if table missing)
    if _sb:
        try:
            _sb.table("tracks").upsert({
                "name_key":    key,
                "display_name": short_display,
                "lat":         lat,
                "lon":         lon,
                "elev_ft":     elev_ft,
                "city_state":  city_state or "",
                "source":      "nominatim",
            }, on_conflict="name_key").execute()
        except Exception:
            pass  # non-fatal — operate without persistent caching

    _track_cache[key] = result
    return result


# ── Apple WeatherKit auth + helpers ──────────────────────────────────────────
_WK_BASE = "https://weatherkit.apple.com/api/v1"


def _get_weatherkit_token() -> str | None:
    """Return a cached WeatherKit JWT; regenerate when less than 10 min remain."""
    TEAM_ID    = _get_secret("APPLE_TEAM_ID")
    SERVICE_ID = _get_secret("APPLE_SERVICE_ID")
    KEY_ID     = _get_secret("APPLE_KEY_ID")
    PRIV_KEY   = _get_secret("APPLE_PRIVATE_KEY")
    if not all([TEAM_ID, SERVICE_ID, KEY_ID, PRIV_KEY]):
        return None
    import time as _time
    now    = int(_time.time())
    cached = st.session_state.get("_wk_token_cache")
    if cached and cached["exp"] - now > 600:  # reuse if > 10 min remain
        return cached["token"]
    try:
        import jwt as _jwt
        exp   = now + 3600
        token = _jwt.encode(
            payload={
                "iss": TEAM_ID,
                "iat": now,
                "exp": exp,
                "sub": SERVICE_ID,
            },
            key=PRIV_KEY,
            algorithm="ES256",
            headers={"kid": KEY_ID, "id": f"{TEAM_ID}.{SERVICE_ID}"},
        )
        st.session_state["_wk_token_cache"] = {"token": token, "exp": exp}
        return token
    except Exception:
        return None


def _wk_msl_to_station(press_hpa: float, elev_ft: float | None) -> float:
    """Convert WeatherKit sea-level pressure (hPa) to station pressure (hPa)."""
    if not elev_ft:
        return press_hpa
    press_inhg = press_hpa * 0.02953
    stn_inhg   = press_inhg * ((1 - 0.0000068756 * elev_ft) ** 5.2561)
    return stn_inhg / 0.02953


def _fetch_track_elev_ft(lat: float, lon: float) -> float | None:
    """Quick elevation lookup via open-elevation.com."""
    try:
        r = requests.get(
            "https://api.open-elevation.com/api/v1/lookup",
            params={"locations": f"{lat},{lon}"},
            timeout=8,
        )
        v = (r.json().get("results") or [{}])[0].get("elevation")
        return float(v) / 0.3048 if v is not None else None
    except Exception:
        return None


def _wk_parse_hourly(hr: dict, lat: float, lon: float) -> dict:
    """Extract and normalise fields from a WeatherKit hourly or current block."""
    temp_c   = hr.get("temperature")
    dewp_c   = hr.get("dewPoint")
    humidity = hr.get("humidity")    # 0.0–1.0 fraction
    press_sl = hr.get("pressure")   # sea-level hPa
    wspd_kmh = hr.get("windSpeed")
    wdir_deg = hr.get("windDirection")

    temp_f = temp_c * 9 / 5 + 32 if temp_c is not None else None

    # Magnus-derived RH (August-Roche-Magnus, matches airdensityonline.com)
    humidity_pct = None
    if temp_c is not None and dewp_c is not None:
        import math as _math
        _m = lambda t: _math.exp(17.625 * t / (243.04 + t))
        humidity_pct = min(100.0, 100.0 * _m(dewp_c) / _m(temp_c))
    if humidity_pct is None and humidity is not None:
        humidity_pct = min(100.0, float(humidity) * 100.0)

    # WeatherKit pressure is already sea-level (QNH/altimeter setting) — use as-is
    press_hpa = float(press_sl) if press_sl is not None else None

    windspeed_mph = float(wspd_kmh) / 1.60934 if wspd_kmh is not None else None

    return {
        "temperature_f": temp_f,
        "humidity_pct":  humidity_pct,
        "pressure_hpa":  press_hpa,
        "windspeed_mph": windspeed_mph,
        "wind_dir_deg":  wdir_deg,
    }


# ── Historical / recent weather (hybrid WeatherKit + Open-Meteo) ──────────────
def fetch_weather(lat: float, lon: float, date_str: str, hour: int = 12) -> dict:
    """Hybrid weather fetch: WeatherKit for recent runs (≤10 days), Open-Meteo archive for older.

    Routing:
      • date within 10 days of today AND Apple credentials present → WeatherKit forecastHourly
      • otherwise (older date OR missing credentials OR WeatherKit error) → Open-Meteo archive

    Always returns the same dict shape:
        temperature_f, humidity_pct, pressure_hpa, windspeed_mph, wind_dir_deg, _source
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td, date as _date
    import math as _math

    try:
        run_date = _date(int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10]))
    except ValueError:
        run_date = _date.today()

    days_old = (_date.today() - run_date).days
    token     = _get_weatherkit_token() if days_old <= 10 else None

    print(f"[fetch_weather] lat={lat}, lon={lon}, date={date_str}, hour={hour}, "
          f"days_old={days_old}, source={'WeatherKit' if token else 'Open-Meteo'}")

    # ── WeatherKit path (recent runs ≤ 10 days) ───────────────────────────────
    if token is not None:
        try:
            target       = _dt(run_date.year, run_date.month, run_date.day, hour, 0, 0, tzinfo=_tz.utc)
            hourly_start = target.strftime("%Y-%m-%dT%H:%M:%SZ")
            hourly_end   = (target + _td(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            r = requests.get(
                f"{_WK_BASE}/weather/en/{lat}/{lon}",
                params={
                    "dataSets":    "forecastHourly",
                    "hourlyStart": hourly_start,
                    "hourlyEnd":   hourly_end,
                    "countryCode": "US",
                    "timezone":    "UTC",
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            r.raise_for_status()
            wk_hours = r.json().get("forecastHourly", {}).get("hours", [])
            if wk_hours:
                result = _wk_parse_hourly(wk_hours[0], lat, lon)
                result["_source"] = "weatherkit"
                # WeatherKit pressure is sea-level (QNH) — convert to station pressure for DA
                if result.get("pressure_hpa") is not None:
                    _wk_elev_ft = _fetch_track_elev_ft(lat, lon) or 0.0
                    if _wk_elev_ft > 0:
                        _wk_stn_inhg = sea_level_to_station_pressure(
                            result["pressure_hpa"] * 0.02953, _wk_elev_ft
                        )
                        result["pressure_hpa"] = _wk_stn_inhg / 0.02953
                return result
        except Exception:
            pass  # fall through to Open-Meteo archive

    # ── Open-Meteo archive path (older runs or WeatherKit unavailable) ────────
    omw_r = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude":         lat,
            "longitude":        lon,
            "start_date":       date_str,
            "end_date":         date_str,
            "hourly":           "temperature_2m,dewpoint_2m,relativehumidity_2m,surface_pressure,windspeed_10m,winddirection_10m",
            "temperature_unit": "fahrenheit",
            "windspeed_unit":   "mph",
            "timezone":         "auto",
        },
        timeout=15,
    )
    omw_r.raise_for_status()
    omw_hourly = omw_r.json().get("hourly", {})
    omw_times  = omw_hourly.get("time", [])
    target_str = f"{date_str}T{hour:02d}:00"
    idx = omw_times.index(target_str) if target_str in omw_times else min(hour, max(0, len(omw_times) - 1))

    def _val(key):
        arr = omw_hourly.get(key, [])
        return arr[idx] if idx < len(arr) else None

    omw_temp_f = _val("temperature_2m")
    omw_dewp_f = _val("dewpoint_2m")
    humidity_pct = None
    if omw_temp_f is not None and omw_dewp_f is not None:
        # August-Roche-Magnus (more accurate than reported RH)
        _t_c = (omw_temp_f - 32) * 5 / 9
        _d_c = (omw_dewp_f - 32) * 5 / 9
        _m   = lambda t: _math.exp(17.625 * t / (243.04 + t))
        humidity_pct = min(100.0, 100.0 * _m(_d_c) / _m(_t_c))
    if humidity_pct is None:
        humidity_pct = _val("relativehumidity_2m")

    # Open-Meteo returns surface_pressure which is already station pressure — use as-is
    press_hpa = _val("surface_pressure")

    return {
        "temperature_f": omw_temp_f,
        "humidity_pct":  humidity_pct,
        "pressure_hpa":  press_hpa,    # station pressure (absolute) in hPa
        "windspeed_mph": _val("windspeed_10m"),
        "wind_dir_deg":  _val("winddirection_10m"),
        "_source":       "open-meteo",
    }


def wind_dir_label(deg: float | None) -> str:
    if deg is None:
        return "—"
    dirs = ["N","NE","E","SE","S","SW","W","NW"]
    return dirs[round(deg / 45) % 8]


def sea_level_to_station_pressure(slp_inhg: float, elevation_ft: float) -> float:
    """Convert sea-level (QNH) pressure to station pressure at elevation_ft using ISA lapse rate."""
    elevation_m = elevation_ft * 0.3048
    return slp_inhg * (1 - (0.0065 * elevation_m / 288.15)) ** 5.2561


def station_to_sea_level_pressure(station_inhg: float, elevation_ft: float) -> float:
    """Inverse of sea_level_to_station_pressure — convert absolute (uncorrected)
    station pressure to its sea-level (altimeter setting) equivalent."""
    elevation_m = elevation_ft * 0.3048
    return station_inhg / (1 - (0.0065 * elevation_m / 288.15)) ** 5.2561


def calc_density_altitude(temp_f: float | None, pressure_hpa: float | None,
                           humidity_pct: float | None = None,
                           elevation_ft: float = 0.0) -> float | None:
    """
    Motorsports-standard density altitude — matches Air Density Online.
    Reference: 60°F, 29.9213 inHg, 0% humidity, sea level.

    pressure_hpa: station (uncorrected/absolute) pressure — NOT sea-level adjusted.
                  Elevation is already reflected in this value; elevation_ft is
                  accepted for API compatibility but not used in the calculation.
    humidity_pct: relative humidity 0–100 (defaults to 0 = dry air if not provided).
    """
    if temp_f is None or pressure_hpa is None:
        return None

    temp_c = (temp_f - 32) * 5 / 9
    hum    = humidity_pct if humidity_pct is not None else 0.0

    # Convert station pressure hPa → inHg
    pressure_inhg = pressure_hpa * 0.02953

    # Saturation vapor pressure (Magnus formula) hPa → inHg
    e_s_hpa  = 6.1078 * 10 ** (7.5 * temp_c / (237.3 + temp_c))
    e_s_inhg = e_s_hpa / 33.8639

    # Actual vapor pressure
    e_inhg = (hum / 100.0) * e_s_inhg

    # Remove water vapor to get effective dry-air pressure
    p_dry = pressure_inhg - e_inhg

    # Density ratio vs. motorsports standard (60°F = 519.69 R, 29.9213 inHg)
    density_ratio = (p_dry / 29.9213) * (519.69 / (temp_f + 459.69))

    # Density altitude in feet
    da = 145442.16 * (1 - density_ratio ** 0.235)

    return round(da)


# ── Race Day Predictor helpers ────────────────────────────────────────────────
def _fetch_weatherkit_current(lat: float, lon: float) -> dict:
    """WeatherKit current conditions — fallback when no METAR station is found within 150 nm."""
    token = _get_weatherkit_token()
    if token is None:
        return {"_source": "weatherkit-unavailable"}
    try:
        r = requests.get(
            f"{_WK_BASE}/weather/en/{lat}/{lon}",
            params={"dataSets": "currentWeather", "countryCode": "US"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        r.raise_for_status()
        cur    = r.json().get("currentWeather", {})
        result = _wk_parse_hourly(cur, lat, lon)
        result["_source"] = "weatherkit"
        return result
    except Exception:
        return {"_source": "weatherkit-error"}


def fetch_weather_rdp(lat: float, lon: float, elev_ft: float = 0.0) -> dict:
    """Fetch current conditions for Race Day Predictor using WeatherKit currentWeather.

    Uses the currentWeather dataset (not forecastHourly) for live observed values
    that match Air Density Online.  Converts sea-level pressure → station pressure
    using the track elevation so density altitude is accurate.

    Falls back to Open-Meteo forecast if WeatherKit credentials are unavailable.
    """
    token = _get_weatherkit_token()
    if token is not None:
        try:
            r = requests.get(
                f"{_WK_BASE}/weather/en/{lat}/{lon}",
                params={"dataSets": "currentWeather", "countryCode": "US"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            r.raise_for_status()
            cur    = r.json().get("currentWeather", {})
            result = _wk_parse_hourly(cur, lat, lon)
            result["_source"] = "weatherkit-current"
            print(f"[fetch_weather_rdp] currentWeather raw: temp={cur.get('temperature')}°C "
                  f"humidity={cur.get('humidity')} pressure={cur.get('pressure')}hPa")
            # Convert sea-level (QNH) pressure → station pressure for accurate DA
            if result.get("pressure_hpa") is not None and elev_ft > 0:
                _stn_inhg = sea_level_to_station_pressure(
                    result["pressure_hpa"] * 0.02953, elev_ft
                )
                result["pressure_hpa"] = _stn_inhg / 0.02953
                print(f"[fetch_weather_rdp] station pressure after conversion: "
                      f"{result['pressure_hpa']:.2f} hPa ({_stn_inhg:.4f} inHg) "
                      f"at elev_ft={elev_ft}")
            return result
        except Exception as _rdp_wk_err:
            print(f"[fetch_weather_rdp] WeatherKit error: {_rdp_wk_err}")

    # Fallback: Open-Meteo forecast for today
    from datetime import date as _d, datetime as _dtt
    _today    = _d.today().strftime("%Y-%m-%d")
    _cur_hour = _dtt.now().hour
    result    = fetch_weather(lat, lon, _today, _cur_hour)
    return result


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    import math
    R  = 6371.0
    φ1 = math.radians(lat1);  φ2 = math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a  = math.sin(dφ/2)**2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def fetch_metar(lat: float, lon: float) -> dict:
    """Find the nearest METAR station and return current conditions.

    Uses NOAA aviationweather.gov — free, no API key, real measured data
    updated every ~hour.  Same source professional racing teams use.

    Searches all stations within a ~150 nm bounding box, picks the nearest
    one that has a complete report (temp + altimeter), and converts:
      • temp/dewpoint → temperature_f and humidity_pct
      • altim (hPa, sea-level QNH) → station pressure (hPa):
            1. hPa → inHg:  P_inHg = altim × 0.02953
            2. Altimeter → station:  P_stn_inHg = P_inHg × (1 − 6.8756×10⁻⁶ × Z_ft)^5.2561
            3. inHg → hPa:  P_stn_hPa = P_stn_inHg / 0.02953
        Track elevation (from Open-Meteo elevation API) is used for Z_ft;
        falls back to the METAR station's own elevation if unavailable.

    Falls back to Open-Meteo forecast API if no METAR is found within 150 nm.
    """
    import math
    from datetime import datetime, timezone as _tz

    # ── Step 1: fetch track elevation (for accurate station pressure) ─────────
    track_elev_ft: float | None = None
    try:
        _er = requests.get(
            "https://api.open-meteo.com/v1/elevation",
            params={"latitude": lat, "longitude": lon},
            timeout=10,
        )
        _em = (_er.json().get("elevation") or [None])[0]
        if _em is not None:
            track_elev_ft = float(_em) / 0.3048  # metres → feet
    except Exception:
        pass

    # ── Step 2: search within ~150 nm bounding box ───────────────────────────
    pad  = 2.5   # degrees ≈ 150 nm
    bbox = f"{lat-pad:.2f},{lon-pad:.2f},{lat+pad:.2f},{lon+pad:.2f}"
    stations: list = []
    try:
        _r = requests.get(
            "https://aviationweather.gov/api/data/metar",
            params={"bbox": bbox, "format": "json", "hoursBack": 2},
            timeout=15,
        )
        if _r.ok:
            stations = _r.json() or []
    except Exception:
        pass

    # ── Step 3: pick nearest station with temp + altimeter ───────────────────
    best: dict | None = None
    best_dist: float  = float("inf")
    for s in stations:
        slat, slon = s.get("lat"), s.get("lon")
        if None in (slat, slon):
            continue
        if s.get("temp") is None or s.get("altim") is None:
            continue
        d = _haversine_km(lat, lon, float(slat), float(slon))
        if d < best_dist:
            best_dist, best = d, s

    if best is None:
        return _fetch_weatherkit_current(lat, lon)

    # ── Step 4: parse fields ──────────────────────────────────────────────────
    temp_c  = float(best["temp"])
    # NOAA JSON may use "dewp" (°C), "dwpc" (°C), or "dwpf" (°F) depending on format version.
    dewp_c: float | None = None
    if best.get("dewp") is not None:
        dewp_c = float(best["dewp"])
    elif best.get("dwpc") is not None:
        dewp_c = float(best["dwpc"])
    elif best.get("dwpf") is not None:
        dewp_c = (float(best["dwpf"]) - 32) * 5 / 9   # Fahrenheit → Celsius

    altim   = float(best["altim"])          # hPa (altimeter setting / QNH, sea-level corrected)
    elev_ft = float(best.get("elev") or 0) # METAR station elevation, feet (fallback)
    icao    = best.get("icaoId") or best.get("id") or "???"
    name    = (best.get("name") or icao).strip()
    obs_ts  = best.get("obsTime")           # Unix timestamp
    wspd_kt = best.get("wspd")

    temp_f = temp_c * 9 / 5 + 32

    # Debug: print raw METAR fields so we can verify what the API returns.
    import sys as _sys_metar
    print(
        f"[METAR-DEBUG] station={icao}  temp={best.get('temp')}°C  "
        f"dewp={best.get('dewp')}  dwpc={best.get('dwpc')}  dwpf={best.get('dwpf')}  "
        f"relHum={best.get('relHum')}  altim={best.get('altim')} hPa  elev={best.get('elev')} ft  "
        f"keys={sorted(k for k in best if best[k] is not None)}",
        file=_sys_metar.stderr, flush=True,
    )

    # RH priority:
    #   1. relHum from NOAA response — server-computed from full-precision observations
    #   2. Magnus formula from dewpoint — good when relHum absent
    _relay_rh = best.get("relHum")
    if _relay_rh is not None:
        humidity_pct = min(100.0, float(_relay_rh))
    elif dewp_c is not None:
        # August-Roche-Magnus (Alduchov & Eskridge 1996) — matches airdensityonline.com
        _magnus = lambda t: math.exp(17.625 * float(t) / (243.04 + float(t)))
        humidity_pct = min(100.0, 100.0 * _magnus(dewp_c) / _magnus(temp_c))
    else:
        humidity_pct = None

    # ── Step 5: altimeter (hPa, sea-level) → station pressure (hPa) ──────────
    # NOAA METAR `altim` is in hPa — NOT inHg.  Using it directly as inHg
    # would produce a pressure of ~34,000 hPa and a DA of −184,979 ft.
    # Use track elevation from Open-Meteo; fall back to airport elevation.
    calc_elev_ft = track_elev_ft if track_elev_ft is not None else elev_ft
    # 1. hPa → inHg
    pressure_inhg = altim * 0.02953
    # 2. Altimeter setting → station pressure at track elevation
    pressure_station_inhg = pressure_inhg * ((1 - (0.0000068756 * calc_elev_ft)) ** 5.2561)
    # 3. inHg → hPa (DA formula expects hPa)
    pressure_hpa = pressure_station_inhg / 0.02953

    windspeed_mph = float(wspd_kt) * 1.15078 if wspd_kt is not None else None

    # Format observation timestamp
    obs_label = ""
    if obs_ts:
        try:
            dt        = datetime.fromtimestamp(int(obs_ts), tz=_tz.utc)
            obs_label = dt.strftime("%H:%M UTC")
        except Exception:
            obs_label = str(obs_ts)

    return {
        "temperature_f":  temp_f,
        "humidity_pct":   humidity_pct,
        "pressure_hpa":   pressure_hpa,
        "windspeed_mph":  windspeed_mph,
        "_source":        "metar",
        "_metar_icao":    icao,
        "_metar_name":    name,
        "_metar_obs":     obs_label,
        "_metar_dist_km": round(best_dist, 1),
    }
