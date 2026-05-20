"""
core/helpers/city_timezones.py — City-name to IANA timezone resolver.

Supports fuzzy matching so users can type partial or Arabic/English city names.
The lookup is fully offline — no external API calls.

Usage:
    from core.helpers.city_timezones import resolve_city

    result = resolve_city("aden")
    # CityMatch(city_label="Aden", timezone="Asia/Aden", country="Yemen")

    result = resolve_city("riyadh")
    # CityMatch(city_label="Riyadh", timezone="Asia/Riyadh", country="Saudi Arabia")

    result = resolve_city("london")
    # CityMatch(city_label="London", timezone="Europe/London", country="United Kingdom")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytz


@dataclass(frozen=True)
class CityMatch:
    """Result of a successful city lookup."""
    city_label: str
    timezone: str
    country: str

    def is_valid_tz(self) -> bool:
        try:
            pytz.timezone(self.timezone)
            return True
        except pytz.UnknownTimeZoneError:
            return False


# ---------------------------------------------------------------------------
# City database
# Keys stored lowercase for case-insensitive matching.
# Aliases (Arabic names, common misspellings) map to the same entry.
# ---------------------------------------------------------------------------

_CITIES: dict[str, CityMatch] = {
    # ─── Arabian Peninsula ───────────────────────────────────────────────────
    "aden": CityMatch("Aden", "Asia/Aden", "Yemen"),
    "عدن": CityMatch("Aden", "Asia/Aden", "Yemen"),
    "sanaa": CityMatch("Sanaa", "Asia/Aden", "Yemen"),
    "sana'a": CityMatch("Sanaa", "Asia/Aden", "Yemen"),
    "صنعاء": CityMatch("Sanaa", "Asia/Aden", "Yemen"),
    "taiz": CityMatch("Taiz", "Asia/Aden", "Yemen"),
    "تعز": CityMatch("Taiz", "Asia/Aden", "Yemen"),
    "hodeidah": CityMatch("Hodeidah", "Asia/Aden", "Yemen"),
    "الحديدة": CityMatch("Hodeidah", "Asia/Aden", "Yemen"),
    "mukalla": CityMatch("Mukalla", "Asia/Aden", "Yemen"),
    "المكلا": CityMatch("Mukalla", "Asia/Aden", "Yemen"),

    "riyadh": CityMatch("Riyadh", "Asia/Riyadh", "Saudi Arabia"),
    "الرياض": CityMatch("Riyadh", "Asia/Riyadh", "Saudi Arabia"),
    "jeddah": CityMatch("Jeddah", "Asia/Riyadh", "Saudi Arabia"),
    "جدة": CityMatch("Jeddah", "Asia/Riyadh", "Saudi Arabia"),
    "mecca": CityMatch("Mecca", "Asia/Riyadh", "Saudi Arabia"),
    "makkah": CityMatch("Mecca", "Asia/Riyadh", "Saudi Arabia"),
    "مكة": CityMatch("Mecca", "Asia/Riyadh", "Saudi Arabia"),
    "medina": CityMatch("Medina", "Asia/Riyadh", "Saudi Arabia"),
    "المدينة": CityMatch("Medina", "Asia/Riyadh", "Saudi Arabia"),
    "dammam": CityMatch("Dammam", "Asia/Riyadh", "Saudi Arabia"),
    "الدمام": CityMatch("Dammam", "Asia/Riyadh", "Saudi Arabia"),
    "tabuk": CityMatch("Tabuk", "Asia/Riyadh", "Saudi Arabia"),
    "تبوك": CityMatch("Tabuk", "Asia/Riyadh", "Saudi Arabia"),
    "abha": CityMatch("Abha", "Asia/Riyadh", "Saudi Arabia"),
    "أبها": CityMatch("Abha", "Asia/Riyadh", "Saudi Arabia"),
    "khobar": CityMatch("Al-Khobar", "Asia/Riyadh", "Saudi Arabia"),
    "الخبر": CityMatch("Al-Khobar", "Asia/Riyadh", "Saudi Arabia"),

    "dubai": CityMatch("Dubai", "Asia/Dubai", "UAE"),
    "دبي": CityMatch("Dubai", "Asia/Dubai", "UAE"),
    "abu dhabi": CityMatch("Abu Dhabi", "Asia/Dubai", "UAE"),
    "أبوظبي": CityMatch("Abu Dhabi", "Asia/Dubai", "UAE"),
    "abudhabi": CityMatch("Abu Dhabi", "Asia/Dubai", "UAE"),
    "sharjah": CityMatch("Sharjah", "Asia/Dubai", "UAE"),
    "الشارقة": CityMatch("Sharjah", "Asia/Dubai", "UAE"),
    "ajman": CityMatch("Ajman", "Asia/Dubai", "UAE"),
    "عجمان": CityMatch("Ajman", "Asia/Dubai", "UAE"),

    "muscat": CityMatch("Muscat", "Asia/Muscat", "Oman"),
    "مسقط": CityMatch("Muscat", "Asia/Muscat", "Oman"),
    "salalah": CityMatch("Salalah", "Asia/Muscat", "Oman"),
    "صلالة": CityMatch("Salalah", "Asia/Muscat", "Oman"),

    "doha": CityMatch("Doha", "Asia/Qatar", "Qatar"),
    "الدوحة": CityMatch("Doha", "Asia/Qatar", "Qatar"),

    "manama": CityMatch("Manama", "Asia/Bahrain", "Bahrain"),
    "المنامة": CityMatch("Manama", "Asia/Bahrain", "Bahrain"),

    "kuwait": CityMatch("Kuwait City", "Asia/Kuwait", "Kuwait"),
    "kuwait city": CityMatch("Kuwait City", "Asia/Kuwait", "Kuwait"),
    "الكويت": CityMatch("Kuwait City", "Asia/Kuwait", "Kuwait"),

    # ─── Levant & Iraq ───────────────────────────────────────────────────────
    "baghdad": CityMatch("Baghdad", "Asia/Baghdad", "Iraq"),
    "بغداد": CityMatch("Baghdad", "Asia/Baghdad", "Iraq"),
    "basra": CityMatch("Basra", "Asia/Baghdad", "Iraq"),
    "البصرة": CityMatch("Basra", "Asia/Baghdad", "Iraq"),
    "mosul": CityMatch("Mosul", "Asia/Baghdad", "Iraq"),
    "الموصل": CityMatch("Mosul", "Asia/Baghdad", "Iraq"),
    "erbil": CityMatch("Erbil", "Asia/Baghdad", "Iraq"),
    "أربيل": CityMatch("Erbil", "Asia/Baghdad", "Iraq"),

    "damascus": CityMatch("Damascus", "Asia/Damascus", "Syria"),
    "دمشق": CityMatch("Damascus", "Asia/Damascus", "Syria"),
    "aleppo": CityMatch("Aleppo", "Asia/Damascus", "Syria"),
    "حلب": CityMatch("Aleppo", "Asia/Damascus", "Syria"),

    "beirut": CityMatch("Beirut", "Asia/Beirut", "Lebanon"),
    "بيروت": CityMatch("Beirut", "Asia/Beirut", "Lebanon"),

    "amman": CityMatch("Amman", "Asia/Amman", "Jordan"),
    "عمّان": CityMatch("Amman", "Asia/Amman", "Jordan"),
    "عمان": CityMatch("Amman", "Asia/Amman", "Jordan"),

    "jerusalem": CityMatch("Jerusalem", "Asia/Jerusalem", "Palestine"),
    "القدس": CityMatch("Jerusalem", "Asia/Jerusalem", "Palestine"),
    "gaza": CityMatch("Gaza", "Asia/Gaza", "Palestine"),
    "غزة": CityMatch("Gaza", "Asia/Gaza", "Palestine"),

    # ─── North Africa ─────────────────────────────────────────────────────────
    "cairo": CityMatch("Cairo", "Africa/Cairo", "Egypt"),
    "القاهرة": CityMatch("Cairo", "Africa/Cairo", "Egypt"),
    "alexandria": CityMatch("Alexandria", "Africa/Cairo", "Egypt"),
    "الإسكندرية": CityMatch("Alexandria", "Africa/Cairo", "Egypt"),

    "tripoli": CityMatch("Tripoli", "Africa/Tripoli", "Libya"),
    "طرابلس": CityMatch("Tripoli", "Africa/Tripoli", "Libya"),
    "benghazi": CityMatch("Benghazi", "Africa/Tripoli", "Libya"),
    "بنغازي": CityMatch("Benghazi", "Africa/Tripoli", "Libya"),

    "tunis": CityMatch("Tunis", "Africa/Tunis", "Tunisia"),
    "تونس": CityMatch("Tunis", "Africa/Tunis", "Tunisia"),

    "algiers": CityMatch("Algiers", "Africa/Algiers", "Algeria"),
    "الجزائر": CityMatch("Algiers", "Africa/Algiers", "Algeria"),

    "casablanca": CityMatch("Casablanca", "Africa/Casablanca", "Morocco"),
    "الدار البيضاء": CityMatch("Casablanca", "Africa/Casablanca", "Morocco"),
    "rabat": CityMatch("Rabat", "Africa/Casablanca", "Morocco"),
    "الرباط": CityMatch("Rabat", "Africa/Casablanca", "Morocco"),

    "khartoum": CityMatch("Khartoum", "Africa/Khartoum", "Sudan"),
    "الخرطوم": CityMatch("Khartoum", "Africa/Khartoum", "Sudan"),

    "mogadishu": CityMatch("Mogadishu", "Africa/Mogadishu", "Somalia"),
    "مقديشو": CityMatch("Mogadishu", "Africa/Mogadishu", "Somalia"),

    "djibouti": CityMatch("Djibouti", "Africa/Djibouti", "Djibouti"),
    "جيبوتي": CityMatch("Djibouti", "Africa/Djibouti", "Djibouti"),

    # ─── Africa (major) ──────────────────────────────────────────────────────
    "nairobi": CityMatch("Nairobi", "Africa/Nairobi", "Kenya"),
    "addis ababa": CityMatch("Addis Ababa", "Africa/Addis_Ababa", "Ethiopia"),
    "lagos": CityMatch("Lagos", "Africa/Lagos", "Nigeria"),
    "accra": CityMatch("Accra", "Africa/Accra", "Ghana"),
    "johannesburg": CityMatch("Johannesburg", "Africa/Johannesburg", "South Africa"),
    "cape town": CityMatch("Cape Town", "Africa/Johannesburg", "South Africa"),

    # ─── Asia ────────────────────────────────────────────────────────────────
    "tehran": CityMatch("Tehran", "Asia/Tehran", "Iran"),
    "طهران": CityMatch("Tehran", "Asia/Tehran", "Iran"),

    "istanbul": CityMatch("Istanbul", "Europe/Istanbul", "Turkey"),
    "اسطنبول": CityMatch("Istanbul", "Europe/Istanbul", "Turkey"),
    "ankara": CityMatch("Ankara", "Europe/Istanbul", "Turkey"),
    "أنقرة": CityMatch("Ankara", "Europe/Istanbul", "Turkey"),

    "karachi": CityMatch("Karachi", "Asia/Karachi", "Pakistan"),
    "lahore": CityMatch("Lahore", "Asia/Karachi", "Pakistan"),
    "islamabad": CityMatch("Islamabad", "Asia/Karachi", "Pakistan"),

    "mumbai": CityMatch("Mumbai", "Asia/Kolkata", "India"),
    "delhi": CityMatch("New Delhi", "Asia/Kolkata", "India"),
    "kolkata": CityMatch("Kolkata", "Asia/Kolkata", "India"),
    "bangalore": CityMatch("Bangalore", "Asia/Kolkata", "India"),

    "dhaka": CityMatch("Dhaka", "Asia/Dhaka", "Bangladesh"),

    "kathmandu": CityMatch("Kathmandu", "Asia/Kathmandu", "Nepal"),

    "colombo": CityMatch("Colombo", "Asia/Colombo", "Sri Lanka"),

    "beijing": CityMatch("Beijing", "Asia/Shanghai", "China"),
    "shanghai": CityMatch("Shanghai", "Asia/Shanghai", "China"),
    "hong kong": CityMatch("Hong Kong", "Asia/Hong_Kong", "Hong Kong"),
    "taipei": CityMatch("Taipei", "Asia/Taipei", "Taiwan"),

    "tokyo": CityMatch("Tokyo", "Asia/Tokyo", "Japan"),
    "osaka": CityMatch("Osaka", "Asia/Tokyo", "Japan"),

    "seoul": CityMatch("Seoul", "Asia/Seoul", "South Korea"),

    "singapore": CityMatch("Singapore", "Asia/Singapore", "Singapore"),

    "bangkok": CityMatch("Bangkok", "Asia/Bangkok", "Thailand"),

    "jakarta": CityMatch("Jakarta", "Asia/Jakarta", "Indonesia"),

    "kuala lumpur": CityMatch("Kuala Lumpur", "Asia/Kuala_Lumpur", "Malaysia"),
    "kl": CityMatch("Kuala Lumpur", "Asia/Kuala_Lumpur", "Malaysia"),

    "manila": CityMatch("Manila", "Asia/Manila", "Philippines"),

    "tashkent": CityMatch("Tashkent", "Asia/Tashkent", "Uzbekistan"),
    "almaty": CityMatch("Almaty", "Asia/Almaty", "Kazakhstan"),
    "baku": CityMatch("Baku", "Asia/Baku", "Azerbaijan"),
    "yerevan": CityMatch("Yerevan", "Asia/Yerevan", "Armenia"),
    "tbilisi": CityMatch("Tbilisi", "Asia/Tbilisi", "Georgia"),

    # ─── Europe ──────────────────────────────────────────────────────────────
    "london": CityMatch("London", "Europe/London", "United Kingdom"),
    "manchester": CityMatch("Manchester", "Europe/London", "United Kingdom"),
    "birmingham": CityMatch("Birmingham", "Europe/London", "United Kingdom"),
    "edinburgh": CityMatch("Edinburgh", "Europe/London", "United Kingdom"),

    "paris": CityMatch("Paris", "Europe/Paris", "France"),
    "berlin": CityMatch("Berlin", "Europe/Berlin", "Germany"),
    "frankfurt": CityMatch("Frankfurt", "Europe/Berlin", "Germany"),
    "munich": CityMatch("Munich", "Europe/Berlin", "Germany"),

    "madrid": CityMatch("Madrid", "Europe/Madrid", "Spain"),
    "barcelona": CityMatch("Barcelona", "Europe/Madrid", "Spain"),

    "rome": CityMatch("Rome", "Europe/Rome", "Italy"),
    "milan": CityMatch("Milan", "Europe/Rome", "Italy"),

    "amsterdam": CityMatch("Amsterdam", "Europe/Amsterdam", "Netherlands"),
    "brussels": CityMatch("Brussels", "Europe/Brussels", "Belgium"),
    "vienna": CityMatch("Vienna", "Europe/Vienna", "Austria"),
    "zurich": CityMatch("Zurich", "Europe/Zurich", "Switzerland"),

    "stockholm": CityMatch("Stockholm", "Europe/Stockholm", "Sweden"),
    "oslo": CityMatch("Oslo", "Europe/Oslo", "Norway"),
    "copenhagen": CityMatch("Copenhagen", "Europe/Copenhagen", "Denmark"),
    "helsinki": CityMatch("Helsinki", "Europe/Helsinki", "Finland"),

    "warsaw": CityMatch("Warsaw", "Europe/Warsaw", "Poland"),
    "prague": CityMatch("Prague", "Europe/Prague", "Czech Republic"),
    "budapest": CityMatch("Budapest", "Europe/Budapest", "Hungary"),
    "bucharest": CityMatch("Bucharest", "Europe/Bucharest", "Romania"),
    "athens": CityMatch("Athens", "Europe/Athens", "Greece"),

    "moscow": CityMatch("Moscow", "Europe/Moscow", "Russia"),
    "موسكو": CityMatch("Moscow", "Europe/Moscow", "Russia"),
    "saint petersburg": CityMatch("Saint Petersburg", "Europe/Moscow", "Russia"),

    "kyiv": CityMatch("Kyiv", "Europe/Kyiv", "Ukraine"),
    "kiev": CityMatch("Kyiv", "Europe/Kyiv", "Ukraine"),

    "lisbon": CityMatch("Lisbon", "Europe/Lisbon", "Portugal"),

    # ─── Americas ────────────────────────────────────────────────────────────
    "new york": CityMatch("New York", "America/New_York", "USA"),
    "newyork": CityMatch("New York", "America/New_York", "USA"),
    "washington": CityMatch("Washington DC", "America/New_York", "USA"),
    "boston": CityMatch("Boston", "America/New_York", "USA"),
    "miami": CityMatch("Miami", "America/New_York", "USA"),
    "atlanta": CityMatch("Atlanta", "America/New_York", "USA"),

    "chicago": CityMatch("Chicago", "America/Chicago", "USA"),
    "houston": CityMatch("Houston", "America/Chicago", "USA"),
    "dallas": CityMatch("Dallas", "America/Chicago", "USA"),

    "denver": CityMatch("Denver", "America/Denver", "USA"),

    "los angeles": CityMatch("Los Angeles", "America/Los_Angeles", "USA"),
    "la": CityMatch("Los Angeles", "America/Los_Angeles", "USA"),
    "san francisco": CityMatch("San Francisco", "America/Los_Angeles", "USA"),
    "seattle": CityMatch("Seattle", "America/Los_Angeles", "USA"),
    "las vegas": CityMatch("Las Vegas", "America/Los_Angeles", "USA"),

    "toronto": CityMatch("Toronto", "America/Toronto", "Canada"),
    "montreal": CityMatch("Montreal", "America/Toronto", "Canada"),
    "vancouver": CityMatch("Vancouver", "America/Vancouver", "Canada"),

    "mexico city": CityMatch("Mexico City", "America/Mexico_City", "Mexico"),

    "sao paulo": CityMatch("São Paulo", "America/Sao_Paulo", "Brazil"),
    "rio de janeiro": CityMatch("Rio de Janeiro", "America/Sao_Paulo", "Brazil"),
    "brasilia": CityMatch("Brasília", "America/Sao_Paulo", "Brazil"),

    "buenos aires": CityMatch("Buenos Aires", "America/Argentina/Buenos_Aires", "Argentina"),
    "bogota": CityMatch("Bogotá", "America/Bogota", "Colombia"),
    "lima": CityMatch("Lima", "America/Lima", "Peru"),
    "santiago": CityMatch("Santiago", "America/Santiago", "Chile"),
    "caracas": CityMatch("Caracas", "America/Caracas", "Venezuela"),

    # ─── Oceania ─────────────────────────────────────────────────────────────
    "sydney": CityMatch("Sydney", "Australia/Sydney", "Australia"),
    "melbourne": CityMatch("Melbourne", "Australia/Melbourne", "Australia"),
    "brisbane": CityMatch("Brisbane", "Australia/Brisbane", "Australia"),
    "perth": CityMatch("Perth", "Australia/Perth", "Australia"),
    "auckland": CityMatch("Auckland", "Pacific/Auckland", "New Zealand"),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_city(query: str) -> Optional[CityMatch]:
    """
    Resolve a city name query to a CityMatch (IANA timezone + label).

    Matching strategy (in order):
    1. Exact lowercase match.
    2. Prefix match (query is a prefix of a known city key).
    3. Substring match (query appears anywhere in a known city key).

    Returns None if no match is found.

    Args:
        query: City name typed by the user (any case, any language).

    Returns:
        CityMatch or None.
    """
    q = query.strip().lower()
    if not q:
        return None

    # 1. Exact match.
    if q in _CITIES:
        return _CITIES[q]

    # 2. Prefix match — find all keys that start with the query.
    prefix_hits = [_CITIES[k] for k in _CITIES if k.startswith(q)]
    if len(prefix_hits) == 1:
        return prefix_hits[0]

    # 3. Substring match — find all keys that contain the query.
    sub_hits = [_CITIES[k] for k in _CITIES if q in k]
    if len(sub_hits) == 1:
        return sub_hits[0]
    if sub_hits:
        # Return the first hit when multiple match (e.g. "san" matches San Francisco etc.)
        return sub_hits[0]

    return None


def resolve_timezone_name(tz_string: str) -> Optional[CityMatch]:
    """
    Accept a raw IANA timezone string (e.g. 'Asia/Aden') and return a CityMatch.

    Used when users type the timezone directly instead of a city name.

    Args:
        tz_string: IANA timezone identifier.

    Returns:
        CityMatch with the timezone_name set, or None if invalid.
    """
    try:
        pytz.timezone(tz_string)
    except pytz.UnknownTimeZoneError:
        return None

    # Try to find a human label from the database.
    tz_lower = tz_string.lower()
    for match in _CITIES.values():
        if match.timezone.lower() == tz_lower:
            return match

    # Fallback: derive label from the IANA string itself.
    label = tz_string.split("/")[-1].replace("_", " ")
    return CityMatch(city_label=label, timezone=tz_string, country="")


def all_suggestions(query: str, limit: int = 5) -> list[CityMatch]:
    """
    Return up to *limit* CityMatch suggestions for the given query.

    Used to build a "did you mean?" list when the query is ambiguous.

    Args:
        query:  Partial city name.
        limit:  Maximum number of suggestions to return.

    Returns:
        List of CityMatch objects (may be empty).
    """
    q = query.strip().lower()
    seen: set[str] = set()
    hits: list[CityMatch] = []

    for k, v in _CITIES.items():
        if q in k and v.timezone not in seen:
            seen.add(v.timezone)
            hits.append(v)
            if len(hits) >= limit:
                break

    return hits
