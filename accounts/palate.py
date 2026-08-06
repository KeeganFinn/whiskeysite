"""
Style-similarity helpers.

A user is characterized by the mix of whiskey *styles* they review: a vector of
review counts keyed by whiskey type (bourbon, rye, scotch single malt, ...).
Two users are "similar" when their style mixes point the same direction, which
we measure with cosine similarity (insensitive to how many reviews each has —
only the proportions matter).

The math is pure (no DB access) so it is easy to unit test; the discover view
does the aggregation and feeds plain dicts in.
"""

import math


def cosine(vec_a, vec_b):
    """Cosine similarity (0..1) between two {whiskey_type: count} dicts."""
    keys = set(vec_a) | set(vec_b)
    dot = sum(vec_a.get(k, 0) * vec_b.get(k, 0) for k in keys)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def match_percent(cos):
    """Map a cosine similarity (0..1) to a 0–100 'style match' percentage."""
    return max(0, min(100, round(cos * 100)))
