import re
import unicodedata

TOPICS = [
    ("agriculture", "Agriculture", "agriculture",
     ["agricultur", "crop", "livestock", "cattle", "farm", "fisher", "forestry"]),
    ("crime", "Crime", "gavel", ["crime", "prison", "justice", "police", "offence"]),
    ("demographics", "Demographics", "family_restroom",
     ["demograph", "population", "census", "birth", "death", "vital", "migra", "household", "fertility"]),
    ("economy", "Economy", "account_balance",
     ["econom", "nationalaccounts", "gdp", "grossdomestic", "price", "inflation", "trade", "export", "import",
      "poverty", "industr", "manufactur", "finance", "tourism", "business"]),
    ("education", "Education", "school", ["educat", "school", "literacy", "enrol"]),
    ("employment", "Employment", "work", ["labour", "labor", "employ", "wage", "workforce", "occupation"]),
    ("energy", "Energy", "bolt", ["energy", "electric", "fuel", "petroleum"]),
    ("environment", "Environment", "eco",
     ["environment", "climate", "co2", "emission", "rainfall", "forest", "water", "weather"]),
    ("health", "Health", "health_and_safety",
     ["health", "ebola", "disease", "hospital", "mortality", "immuni", "hiv", "malaria", "nutrition"]),
    ("housing", "Housing", "home", ["housing", "dwelling"]),
]
OTHER = ("other", "Other", "category", [])
TITLES = {slug: title for slug, title, _, _ in TOPICS + [OTHER]}
ICONS = {slug: icon for slug, _, icon, _ in TOPICS + [OTHER]}
ORDER = [slug for slug, _, _, _ in TOPICS + [OTHER]]


def norm(text):
    text = unicodedata.normalize("NFD", str(text or "")).lower()
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in text if unicodedata.category(c) != "Mn"))


def _hits(text):
    text = norm(text)
    return {slug: sum(text.count(term) for term in terms) for slug, _, _, terms in TOPICS}


def classify(labels, text=""):
    for label in labels:
        hits = _hits(label)
        found = [slug for slug in ORDER[:-1] if hits[slug]]
        if found:
            return found
    hits = _hits(text)
    best = max(hits.values()) if hits else 0
    return [next(slug for slug in ORDER if hits.get(slug) == best)] if best else [OTHER[0]]
