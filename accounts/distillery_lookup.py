"""
Look up basic facts about a distillery from the web (Wikipedia) so the review
screen can pre-fill country / region / climate.

If the name turns out to be a *brand* rather than a distillery (e.g. "Redbreast"
is produced at Midleton Distillery), we also try to surface the real distillery
as a "did you mean" suggestion.

Everything is best-effort: any failure returns ``found=False`` with no
suggestion, and the reviewer just fills the form manually.
"""

import difflib
import json
import re

import requests
from django.conf import settings

from .climate_lookup import CLIMATE_LOOKUP, suggest_climate

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
WIKI_API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "WhiskeyTracker/1.0 (distillery lookup)"}
TIMEOUT = 6

KNOWN_COUNTRIES = set(CLIMATE_LOOKUP.keys()) | {
    "england", "wales", "india", "australia", "france", "germany", "sweden",
}

REGION_TO_COUNTRY = {
    region: country
    for country, regions in CLIMATE_LOOKUP.items()
    for region in regions
    if region != "all"
}

# "... produced at the Midleton Distillery ..." -> "Midleton"
DISTILLERY_RE = re.compile(
    r"([A-Z][A-Za-z'’.&\-]+(?:\s+[A-Z][A-Za-z'’.&\-]+){0,3})\s+[Dd]istillery"
)


def _fetch_summary(title):
    try:
        resp = requests.get(
            WIKI_SUMMARY.format(requests.utils.quote(title)),
            headers=HEADERS, timeout=TIMEOUT,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data.get("type") == "disambiguation":
        return None
    return data


def _fetch_extract(title, chars=1500):
    """Return a longer plain-text extract for a title (beyond the intro)."""
    try:
        resp = requests.get(
            WIKI_API,
            headers=HEADERS, timeout=TIMEOUT,
            params={
                "action": "query", "prop": "extracts", "explaintext": 1,
                "exchars": chars, "redirects": 1, "titles": title,
                "format": "json",
            },
        )
    except requests.RequestException:
        return ""
    if resp.status_code != 200:
        return ""
    pages = resp.json().get("query", {}).get("pages", {})
    for page in pages.values():
        if page.get("extract"):
            return page["extract"]
    return ""


def _search_raw(query, limit=5):
    """Return (titles, spelling_suggestion) for a search query."""
    try:
        resp = requests.get(
            WIKI_API,
            headers=HEADERS, timeout=TIMEOUT,
            params={
                "action": "query", "list": "search", "srsearch": query,
                "format": "json", "srlimit": limit, "srinfo": "suggestion",
            },
        )
    except requests.RequestException:
        return [], ""
    if resp.status_code != 200:
        return [], ""
    q = resp.json().get("query", {})
    titles = [r["title"] for r in q.get("search", [])]
    suggestion = q.get("searchinfo", {}).get("suggestion", "")
    return titles, suggestion


def _search_titles(query, limit=5):
    return _search_raw(query, limit)[0]


def _clean_title(title):
    """Turn a Wikipedia title into a plain distillery name."""
    t = re.sub(r"\s*\([^)]*\)", "", title)          # drop "(distillery)" etc.
    t = re.sub(r"\s+distillery$", "", t, flags=re.I)  # drop trailing "Distillery"
    return t.strip()


def _best_distillery_match(name):
    """
    Fuzzy-find the closest distillery article to a (possibly misspelled) name.
    Returns (summary_data, clean_name) or (None, "").
    """
    titles, suggestion = _search_raw(f"{name} distillery", limit=5)

    # Fall back to Wikipedia's own spelling suggestion (e.g. "Buffalo Trce"
    # -> "buffalo trace") when the typo returns nothing.
    if not titles and suggestion:
        titles = _search_titles(f"{suggestion} distillery", limit=5)

    best_title = best_clean = ""
    best_ratio = 0.0
    for title in titles:
        clean = _clean_title(title)
        ratio = difflib.SequenceMatcher(None, name.lower(), clean.lower()).ratio()
        if ratio > best_ratio:
            best_ratio, best_title, best_clean = ratio, title, clean

    if best_title and best_ratio >= 0.55:
        data = _fetch_summary(best_title)
        if data:
            return data, best_clean
    return None, ""


def _name_is_relevant(name, text):
    """True if the queried name's significant tokens appear in the text."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", name.lower()) if len(t) > 2]
    if not tokens:
        return False
    hay = text.lower()
    return all(t in hay for t in tokens)


def _detect_region_country(text):
    """Return (region_title, country_title) from free text; either may be ''."""
    haystack = text.lower()
    region = country = ""
    for reg, ctry in REGION_TO_COUNTRY.items():
        if reg in haystack:
            return reg.title(), ctry.title()
    for ctry in KNOWN_COUNTRIES:
        if ctry in haystack:
            country = ctry.title()
            break
    return region, country


def _climate_for(country, region):
    """Climate from country+region, or a country-only 'all' entry if present."""
    climate = suggest_climate(country, region)
    if climate:
        return climate
    if country:
        country_map = CLIMATE_LOOKUP.get(country.strip().lower(), {})
        if "all" in country_map:
            return country_map["all"]
    return ""


def _info_from_summary(data):
    text = " ".join(filter(None, [data.get("extract", ""), data.get("description", "")]))
    region, country = _detect_region_country(text)
    return {
        "country": country,
        "region": region,
        "climate": _climate_for(country, region),
        "summary": data.get("extract", ""),
        "source_url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
    }


def _find_suggestion(name, direct_data):
    """
    Try to find the real distillery behind a brand name.

    Looks for an "X Distillery" mention in the brand's article (or a search
    result), then resolves that distillery's own page for country/region.
    """
    candidate_name = ""

    # 1) Parse a distillery name out of the brand's own article, if we have one.
    if direct_data:
        m = DISTILLERY_RE.search(direct_data.get("extract", ""))
        if m:
            candidate_name = m.group(1).strip()

    # 2) Otherwise search Wikipedia for the brand and scan the top results,
    #    using a longer extract so a "produced at X Distillery" line deeper in
    #    the article is still found. Only trust a result that actually mentions
    #    the queried name — otherwise a junk name fuzzy-matches a real article.
    if not candidate_name:
        for title in _search_titles(f"{name} whiskey")[:3]:
            extract = _fetch_extract(title)
            if not _name_is_relevant(name, title + " " + extract):
                continue
            m = DISTILLERY_RE.search(extract)
            if m:
                candidate_name = m.group(1).strip()
                break

    if not candidate_name:
        return None

    # Resolve the candidate distillery's own page (direct, then via search).
    data = _fetch_summary(f"{candidate_name} distillery") or _fetch_summary(candidate_name)
    if not data:
        for title in _search_titles(f"{candidate_name} distillery")[:2]:
            data = _fetch_summary(title)
            if data:
                break
    if not data:
        return None
    info = _info_from_summary(data)
    if not (info["country"] or info["region"]):
        return None

    return {
        "name": candidate_name,
        "country": info["country"],
        "region": info["region"],
        "climate": info["climate"],
        "source_url": info["source_url"],
    }


def _gemini_lookup(name):
    """
    Ask the Gemini API for a distillery's details. Returns a result dict
    (source='gemini') or None when no key is configured or the call fails.
    """
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        return None

    model = getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash")
    prompt = (
        "You are a whiskey reference. For the distillery or whiskey brand named "
        f"\"{name}\", reply ONLY with minified JSON of the form "
        '{"found": true/false, "name": "<corrected distillery name>", '
        '"country": "<country>", "region": "<region or state>"}. '
        "Use the actual distillery (for a brand, the distillery that produces it). "
        "If you are not confident it is a real distillery, set found to false."
    )

    try:
        resp = requests.post(
            GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json"},
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None

    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, ValueError):
        return None

    if not parsed.get("found"):
        return None

    country = (parsed.get("country") or "").strip()
    region = (parsed.get("region") or "").strip()
    return {
        "found": True,
        "name": (parsed.get("name") or name).strip(),
        "country": country,
        "region": region,
        "climate": _climate_for(country, region),
        "summary": "",
        "source_url": "",
        "source": "gemini",
        "suggestion": None,
    }


def lookup_distillery_info(name):
    """
    Returns:
      {found, name, country, region, climate, summary, source_url, source, suggestion}
    where ``suggestion`` is None or a dict with the real distillery's details.

    Tries Wikipedia first; if that finds nothing useful, falls back to the
    Gemini API (only when GEMINI_API_KEY is configured).
    """
    name = (name or "").strip()
    empty = {
        "found": False, "name": "", "country": "", "region": "", "climate": "",
        "summary": "", "source_url": "", "source": "", "suggestion": None,
    }
    if not name:
        return empty

    direct_data = _fetch_summary(f"{name} distillery") or _fetch_summary(name)

    direct_relevant = direct_data and _name_is_relevant(
        name, direct_data.get("title", "") + " " + direct_data.get("extract", "")
    )

    if direct_relevant:
        info = _info_from_summary(direct_data)
        if info["country"] or info["region"]:
            # Found a real distillery directly.
            info["found"] = True
            info["name"] = _clean_title(direct_data.get("title", "")) or name
            info["source"] = "wikipedia"
            info["suggestion"] = None
            return info

    # Fuzzy match (handles typos / partial names like "Ardbg" -> "Ardbeg").
    fz_data, fz_name = _best_distillery_match(name)
    if fz_data:
        info = _info_from_summary(fz_data)
        if info["country"] or info["region"]:
            info["found"] = True
            info["name"] = fz_name or name
            info["source"] = "wikipedia"
            info["suggestion"] = None
            return info

    # No direct hit (or it's a brand) -> try to suggest the real distillery.
    suggestion = _find_suggestion(name, direct_data)

    # Last resort: ask Gemini (only if a key is configured).
    if not suggestion:
        gemini = _gemini_lookup(name)
        if gemini:
            return gemini

    result = dict(empty)
    result["suggestion"] = suggestion
    if direct_data:
        result["summary"] = direct_data.get("extract", "")
        result["source_url"] = (
            direct_data.get("content_urls", {}).get("desktop", {}).get("page", "")
        )
    return result
