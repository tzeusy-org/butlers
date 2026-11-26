"""A pinned airport and city to IANA zone table for the projected owner zone (bu-5ii4gj.5).

Read locally and never sent to an outside service: no geocoder. It is deliberately small and
conservative. A place the table does not name has no zone here, and the caller reads that as
unknown rather than guessing. Cities that span zones are mapped only by their airport or the city
name the airport serves, so each entry names exactly one zone.
"""

from __future__ import annotations

import unicodedata

#: IATA airport code to zone.
AIRPORT_ZONES: dict[str, str] = {
    "SIN": "Asia/Singapore",
    "KUL": "Asia/Kuala_Lumpur",
    "BKK": "Asia/Bangkok",
    "DMK": "Asia/Bangkok",
    "CGK": "Asia/Jakarta",
    "MNL": "Asia/Manila",
    "HKG": "Asia/Hong_Kong",
    "TPE": "Asia/Taipei",
    "ICN": "Asia/Seoul",
    "GMP": "Asia/Seoul",
    "NRT": "Asia/Tokyo",
    "HND": "Asia/Tokyo",
    "KIX": "Asia/Tokyo",
    "PEK": "Asia/Shanghai",
    "PVG": "Asia/Shanghai",
    "SHA": "Asia/Shanghai",
    "DEL": "Asia/Kolkata",
    "BOM": "Asia/Kolkata",
    "DXB": "Asia/Dubai",
    "DOH": "Asia/Qatar",
    "IST": "Europe/Istanbul",
    "LHR": "Europe/London",
    "LGW": "Europe/London",
    "STN": "Europe/London",
    "DUB": "Europe/Dublin",
    "CDG": "Europe/Paris",
    "ORY": "Europe/Paris",
    "AMS": "Europe/Amsterdam",
    "FRA": "Europe/Berlin",
    "MUC": "Europe/Berlin",
    "BER": "Europe/Berlin",
    "ZRH": "Europe/Zurich",
    "MAD": "Europe/Madrid",
    "BCN": "Europe/Madrid",
    "FCO": "Europe/Rome",
    "LIS": "Europe/Lisbon",
    "ATH": "Europe/Athens",
    "JFK": "America/New_York",
    "EWR": "America/New_York",
    "LGA": "America/New_York",
    "BOS": "America/New_York",
    "IAD": "America/New_York",
    "ATL": "America/New_York",
    "MIA": "America/New_York",
    "YYZ": "America/Toronto",
    "ORD": "America/Chicago",
    "DFW": "America/Chicago",
    "DEN": "America/Denver",
    "SFO": "America/Los_Angeles",
    "LAX": "America/Los_Angeles",
    "SEA": "America/Los_Angeles",
    "YVR": "America/Vancouver",
    "HNL": "Pacific/Honolulu",
    "MEX": "America/Mexico_City",
    "GRU": "America/Sao_Paulo",
    "EZE": "America/Argentina/Buenos_Aires",
    "SYD": "Australia/Sydney",
    "MEL": "Australia/Melbourne",
    "BNE": "Australia/Brisbane",
    "PER": "Australia/Perth",
    "AKL": "Pacific/Auckland",
    "JNB": "Africa/Johannesburg",
    "CPT": "Africa/Johannesburg",
    "CAI": "Africa/Cairo",
    "NBO": "Africa/Nairobi",
}

#: City name (lower case, accents removed) to zone, for cities that sit in one zone.
CITY_ZONES: dict[str, str] = {
    "singapore": "Asia/Singapore",
    "kuala lumpur": "Asia/Kuala_Lumpur",
    "bangkok": "Asia/Bangkok",
    "jakarta": "Asia/Jakarta",
    "manila": "Asia/Manila",
    "hong kong": "Asia/Hong_Kong",
    "taipei": "Asia/Taipei",
    "seoul": "Asia/Seoul",
    "tokyo": "Asia/Tokyo",
    "osaka": "Asia/Tokyo",
    "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "delhi": "Asia/Kolkata",
    "new delhi": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "dubai": "Asia/Dubai",
    "doha": "Asia/Qatar",
    "istanbul": "Europe/Istanbul",
    "london": "Europe/London",
    "dublin": "Europe/Dublin",
    "paris": "Europe/Paris",
    "amsterdam": "Europe/Amsterdam",
    "frankfurt": "Europe/Berlin",
    "munich": "Europe/Berlin",
    "berlin": "Europe/Berlin",
    "zurich": "Europe/Zurich",
    "madrid": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "rome": "Europe/Rome",
    "lisbon": "Europe/Lisbon",
    "athens": "Europe/Athens",
    "new york": "America/New_York",
    "boston": "America/New_York",
    "washington": "America/New_York",
    "atlanta": "America/New_York",
    "miami": "America/New_York",
    "toronto": "America/Toronto",
    "chicago": "America/Chicago",
    "dallas": "America/Chicago",
    "denver": "America/Denver",
    "san francisco": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "vancouver": "America/Vancouver",
    "honolulu": "Pacific/Honolulu",
    "mexico city": "America/Mexico_City",
    "sao paulo": "America/Sao_Paulo",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "brisbane": "Australia/Brisbane",
    "perth": "Australia/Perth",
    "auckland": "Pacific/Auckland",
    "johannesburg": "Africa/Johannesburg",
    "cape town": "Africa/Johannesburg",
    "cairo": "Africa/Cairo",
    "nairobi": "Africa/Nairobi",
}


def normalise_place(value: str | None) -> str:
    """Lower-case, accent-free, single-spaced: the key a city name is looked up by."""
    if not value:
        return ""
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(folded.casefold().replace(",", " ").split())


def zone_for_place(airport: str | None, city: str | None) -> str | None:
    """The zone of a leg's arrival, by airport code first, then city; ``None`` when unnamed."""
    code = (airport or "").strip().upper()
    if code in AIRPORT_ZONES:
        return AIRPORT_ZONES[code]
    return CITY_ZONES.get(normalise_place(city))
