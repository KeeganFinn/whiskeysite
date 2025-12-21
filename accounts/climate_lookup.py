CLIMATE_CHOICES = [
    ("cold_dry",       "Cold / Dry"),
    ("cold_humid",     "Cold / Humid"),
    ("cool_dry",       "Cool / Dry"),
    ("cool_humid",     "Cool / Humid"),
    ("cool_mild",      "Cool / Mild"),
    ("moderate_dry",   "Moderate / Dry"),
    ("moderate_humid", "Moderate / Humid"),
    ("warm_dry",       "Warm / Dry"),
    ("warm_humid",     "Warm / Humid"),
    ("hot_dry",        "Hot / Dry"),
    ("hot_humid",      "Hot / Humid"),
    ("tropical_humid", "Tropical / Humid"),
]

# Main lookup: Country -> Region/State -> climate code from above
CLIMATE_LOOKUP = {
    "united states": {
        "alabama":        "warm_humid",
        "alaska":         "cold_dry",
        "arizona":        "hot_dry",
        "arkansas":       "warm_humid",
        "california":     "warm_dry",
        "colorado":       "cool_dry",
        "connecticut":    "cool_humid",
        "delaware":       "cool_humid",
        "florida":        "tropical_humid",
        "georgia":        "warm_humid",
        "hawaii":         "tropical_humid",
        "idaho":          "cool_dry",
        "illinois":       "cool_humid",
        "indiana":        "cool_humid",
        "iowa":           "cool_humid",
        "kansas":         "warm_dry",
        "kentucky":       "moderate_humid",
        "louisiana":      "warm_humid",
        "maine":          "cold_humid",
        "maryland":       "cool_humid",
        "massachusetts":  "cool_humid",
        "michigan":       "cold_humid",
        "minnesota":      "cold_humid",
        "mississippi":    "warm_humid",
        "missouri":       "moderate_humid",
        "montana":        "cold_dry",
        "nebraska":       "cool_dry",
        "nevada":         "hot_dry",
        "new hampshire":  "cold_humid",
        "new jersey":     "cool_humid",
        "new mexico":     "warm_dry",
        "new york":       "cool_humid",
        "north carolina": "moderate_humid",
        "north dakota":   "cold_dry",
        "ohio":           "cool_humid",
        "oklahoma":       "warm_dry",
        "oregon":         "cool_mild",
        "pennsylvania":   "cool_humid",
        "rhode island":   "cool_humid",
        "south carolina": "warm_humid",
        "south dakota":   "cold_dry",
        "tennessee":      "moderate_humid",
        "texas":          "hot_dry",
        "utah":           "cool_dry",
        "vermont":        "cold_humid",
        "virginia":       "moderate_humid",
        "washington":     "cool_mild",
        "west virginia":  "cool_humid",
        "wisconsin":      "cold_humid",
        "wyoming":        "cold_dry",
    },

    "canada": {
        "ontario":          "cold_humid",
        "quebec":           "cold_humid",
        "british columbia": "cool_mild",
        "alberta":          "cold_dry",
        "manitoba":         "cold_dry",
        "saskatchewan":     "cold_dry",
        "nova scotia":      "cool_humid",
        "new brunswick":    "cool_humid",
        "newfoundland":     "cold_humid",
        "prince edward island": "cool_humid",
    },

    "scotland": {
        "islay":        "cool_humid",
        "highland":     "cool_mild",
        "speyside":     "cool_humid",
        "lowland":      "cool_mild",
        "campbeltown":  "cool_humid",
        "islands":      "cool_humid",
    },

    "ireland": {
        "all": "cool_humid",
    },

    "japan": {
        "hokkaido": "cool_humid",
        "honshu":   "moderate_humid",
        "kyushu":   "warm_humid",
    },

    "taiwan": {
        "all": "tropical_humid",
    },
}


def suggest_climate(country: str | None, region: str | None) -> str | None:
    """
    Given a country + region (state/area), return a climate code like 'warm_humid',
    or None if we don't know.
    """
    if not country or not region:
        return None

    c = country.strip().lower()
    r = region.strip().lower()

    country_map = CLIMATE_LOOKUP.get(c)
    if not country_map:
        return None

    # Exact match
    if r in country_map:
        return country_map[r]

    # Fallbacks like "all" for Ireland/Taiwan
    if "all" in country_map:
        return country_map["all"]

    return None
