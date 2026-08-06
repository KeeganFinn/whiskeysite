"""
Site-wide whiskey statistics for the feed page.

`site_stats()` returns a dict of "core" numbers (totals across every user's
inventory and all reviews) plus some "fun" derived figures (barrels stored,
volume consumed, a tongue-in-cheek investment projection). Kept separate from
the view so the math is easy to unit test.
"""

from django.db.models import Sum

from .models import Bottle, BottleReview, CanonicalBottle

# Assumptions behind the fun stats.
BOTTLE_VOLUME_L = 0.75          # a standard 750ml bottle
BARREL_VOLUME_L = 200.0         # ~a standard whiskey barrel (53 US gal)
OPENED_CONSUMED_FRACTION = 0.5  # treat an "opened" bottle as half gone
# S&P 500 long-run average nominal annual return (~10%).
SP500_ANNUAL_RETURN = 0.10
FUND_YEARS = 30


def site_stats(user=None):
    """Stats across the whole site, or scoped to one user's inventory/reviews."""
    bottles = Bottle.objects.all()
    if user is not None:
        bottles = bottles.filter(user=user)

    total_bottles = bottles.count()
    total_value = float(bottles.aggregate(s=Sum("price"))["s"] or 0)
    finished = bottles.filter(status="finished").count()
    opened = bottles.filter(status="opened").count()
    unopened = bottles.filter(status="unopened").count()

    if user is not None:
        reviews_count = BottleReview.objects.filter(reviewer=user).count()
        # Distinct bottles in this user's inventory.
        unique_bottles = bottles.values("name").distinct().count()
    else:
        reviews_count = BottleReview.objects.count()
        unique_bottles = CanonicalBottle.objects.count()

    total_volume_l = total_bottles * BOTTLE_VOLUME_L
    consumed_l = (
        finished * BOTTLE_VOLUME_L
        + opened * BOTTLE_VOLUME_L * OPENED_CONSUMED_FRACTION
    )

    return {
        # --- Core ---
        "reviews_count": reviews_count,
        "unique_bottles": unique_bottles,
        "total_bottles": total_bottles,
        "total_value": total_value,
        "unopened": unopened,
        "opened": opened,
        "finished": finished,
        # --- Fun ---
        "barrels": total_volume_l / BARREL_VOLUME_L,
        "total_volume_l": total_volume_l,
        "consumed_l": consumed_l,
        "consumed_bottles_equiv": consumed_l / BOTTLE_VOLUME_L,
        "fund_value": total_value * (1 + SP500_ANNUAL_RETURN) ** FUND_YEARS,
        "fund_years": FUND_YEARS,
        "fund_return_pct": int(SP500_ANNUAL_RETURN * 100),
    }
