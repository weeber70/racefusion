"""
timeslip.py — RaceFusion timeslip image orientation and OCR scanning.

Exports:
  correct_image_orientation(img_bytes) → PIL Image or raw bytes
  scan_timeslip(image_bytes, media_type, api_key, car_number) → dict
  _normalize_slip_result(raw) → str
"""

import base64
import json
import re
import io as _io

# ── PIL (optional) ────────────────────────────────────────────────────────────
try:
    from PIL import Image as _PILImage, ExifTags as _ExifTags
    def correct_image_orientation(img_bytes: bytes):
        """Apply EXIF orientation so portrait phone photos display upright."""
        img = _PILImage.open(_io.BytesIO(img_bytes))
        try:
            exif = img._getexif()
            if exif:
                for tag, value in exif.items():
                    if _ExifTags.TAGS.get(tag) == "Orientation":
                        if value == 3:
                            img = img.rotate(180, expand=True)
                        elif value == 6:
                            img = img.rotate(270, expand=True)
                        elif value == 8:
                            img = img.rotate(90, expand=True)
                        break
        except (AttributeError, TypeError):
            pass
        return img
except ImportError:
    def correct_image_orientation(img_bytes: bytes):  # type: ignore
        return img_bytes


# ── Timeslip scanner ──────────────────────────────────────────────────────────
def scan_timeslip(image_bytes: bytes, media_type: str, api_key: str, car_number: str = "") -> dict:
    """Call Claude vision to extract timeslip fields."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(image_bytes).decode()

    car_num = car_number.strip()

    if car_num:
        _car_section = f"""The user's car number is "{car_num}".

STEP 1 — FIND THE USER'S CAR ON THE SLIP:
Search the entire timeslip for car number "{car_num}".
  • Found → set "car_found" to true. Note which lane (Left or Right) it is in and extract
    ALL timing data for that car only. Ignore the other car's timing rows entirely.
  • NOT found → set "car_found" to false. Set lane, car_number, dial_in, reaction_time,
    ft_60, ft_330, ft_660, mph_660, ft_1000, ft_1320, mph_1320, and result all to null.
    You may still extract date, time, track_name, track_location, and weather fields.

STEP 2 — DETERMINE WIN/LOSS (only when car_found is true):

Compulink timing system (most common): The winner's ET on the 1/4-mile line is followed
immediately by "<<WIN" (left-lane winner) or ">>WIN" (right-lane winner).
Example: "6.676  <<WIN" means the left-lane car won.
Example: "8.341  >>WIN" means the right-lane car won.
Cross-reference which lane the WIN marker applies to with the user's car lane to determine
Win or Loss for car "{car_num}".

Other timing systems: look for a printed "W" or "L", "WINNER"/"LOSER" text, a checked
WIN/LOSS box, or a "BYE" label. Map winner → "Win", loser → "Loss", bye run → "Bye".
If no result indicator is visible at all, use null."""
    else:
        _car_section = """No car number has been configured for this user.
Set "car_found" to false. Set "result" to null.
Do NOT attempt to determine Win or Loss.
Do NOT use any car number printed on the slip to make assumptions about the user.
You may still extract date, time, track_name, track_location, and weather fields."""

    prompt = f"""You are reading a drag racing timeslip. Extract every field you can see.
Return a JSON object with these keys (use null for anything not visible):
{{
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "track_name": "full track name as printed",
  "track_location": "City, State (or City, Country) — look for address or city/state text near the track name",
  "car_found": true or false,
  "lane": "left or right",
  "car_number": "...",
  "dial_in": float,
  "reaction_time": float,
  "ft_60": float,
  "ft_330": float,
  "ft_660": float,
  "mph_660": float,
  "ft_1000": float,
  "ft_1320": float,
  "mph_1320": float,
  "temp_f": float or null,
  "baro_inhg": float or null,
  "humidity_pct": float or null,
  "wind": "speed and direction as printed e.g. 14.25 SE" or null,
  "density_alt_ft": float or null,
  "result": "Win", "Loss", "Bye", or null,
  "round_number": "round label as printed e.g. E1, R1, Q2" or null,
  "issues": "any notes or issues printed on the slip" or null
}}

{_car_section}

ROUND NUMBER: Look for text like "Rnd # E1 9/10", "Round 1", "R2", "Elim 1", or similar
near the bottom of the slip. Extract just the round label (e.g. "E1", "R1", "Q2") for
"round_number", or null if not present.

Many timeslips print weather conditions (temp, barometric pressure, humidity, wind,
corrected/density altitude) — extract those too if present.
Return only the JSON object. No markdown, no explanation."""

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )

    text = response.content[0].text.strip()
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _normalize_slip_result(raw) -> str:
    """Map any scanner result value to 'Win', 'Loss', 'Bye', or '' (unknown/null)."""
    if not raw:
        return ""
    s = str(raw).strip().upper()
    if s in ("W", "WIN", "WINNER", "1"):
        return "Win"
    if s in ("L", "LOSS", "LOSER", "LOSE", "0"):
        return "Loss"
    if s in ("B", "BYE", "BYE RUN"):
        return "Bye"
    return ""
