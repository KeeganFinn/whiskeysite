"""
Seed fake users with bottles and reviews so the Discover page's style
recommendations have data to work with.

There are no archetypes: each fake user simply concentrates their reviews on
one primary whiskey style (with a few stray reviews of other styles). The
Discover page then labels each person by the style they review most and matches
people by how similar their style mix is.

Usage:
    python manage.py seed_fake_users
    python manage.py seed_fake_users --count 36 --reviews 10
    python manage.py seed_fake_users --clear        # remove previously seeded users
"""

import random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import BottleReview, CanonicalBottle, Distillery

FAKE_PREFIX = "taster_"

# Shared bottle pool: (name, whiskey_type, proof)
# Each style has at least 3 bottles so a primary-style user gets a clear
# majority and their dominant label matches their bio.
BOTTLE_POOL = [
    ("Buffalo Trace",         "bourbon",            90),
    ("Maker's Mark",          "bourbon",            90),
    ("Eagle Rare 10",         "bourbon",            90),
    ("Booker's",              "bourbon",           124),
    ("Russell's Reserve Rye", "rye",                90),
    ("Pikesville Rye",        "rye",               110),
    ("Sazerac Rye",           "rye",                90),
    ("Lagavulin 16",          "scotch_single_malt", 86),
    ("Ardbeg 10",             "scotch_single_malt", 92),
    ("Glenfiddich 12",        "scotch_single_malt", 80),
    ("Redbreast 12",          "irish",              80),
    ("Jameson",               "irish",              80),
    ("Green Spot",            "irish",              80),
    ("Nikka Coffey Grain",    "japanese",           90),
    ("Suntory Toki",          "japanese",           86),
    ("Hibiki Harmony",        "japanese",           86),
    ("Crown Royal",           "canadian",           80),
    ("Lot No. 40",            "canadian",           86),
    ("Forty Creek",           "canadian",           80),
]

# Styles we deliberately cluster fake users around (must exist in the pool).
PRIMARY_STYLES = [
    "bourbon",
    "rye",
    "scotch_single_malt",
    "irish",
    "japanese",
    "canadian",
]


class Command(BaseCommand):
    help = "Create fake users whose reviews concentrate on a primary whiskey style."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=36,
                            help="Number of fake users to create.")
        parser.add_argument("--reviews", type=int, default=8,
                            help="Max reviews per fake user.")
        parser.add_argument("--clear", action="store_true",
                            help="Delete previously seeded fake users and exit.")
        parser.add_argument("--seed", type=int, default=42,
                            help="RNG seed for reproducibility.")

    @transaction.atomic
    def handle(self, *args, **opts):
        if opts["clear"]:
            deleted, _ = User.objects.filter(username__startswith=FAKE_PREFIX).delete()
            self.stdout.write(self.style.SUCCESS(f"Removed {deleted} fake-user objects."))
            return

        rng = random.Random(opts["seed"])

        # Build the shared bottle pool, grouped by whiskey type.
        bottles_by_type = {}
        for name, wtype, proof in BOTTLE_POOL:
            distillery, _ = Distillery.objects.get_or_create(
                name=f"{name} Distillery",
                defaults={"is_verified": True},
            )
            bottle, _ = CanonicalBottle.objects.get_or_create(
                name=name,
                distillery=distillery,
                whiskey_type=wtype,
                is_store_pick=False,
                store_name=None,
                defaults={"proof": proof},
            )
            bottles_by_type.setdefault(wtype, []).append(bottle)

        all_bottles = [b for group in bottles_by_type.values() for b in group]

        created = 0
        for i in range(opts["count"]):
            primary = PRIMARY_STYLES[i % len(PRIMARY_STYLES)]
            username = f"{FAKE_PREFIX}{i + 1:03d}"

            user, was_created = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@example.com"},
            )
            if was_created:
                user.set_password("password123")
                user.save()
                created += 1

            profile = user.userprofile
            profile.bio = f"Mostly drinks {primary.replace('_', ' ')}."
            profile.save()

            # Mostly bottles of the primary style, plus a couple of strays.
            primary_bottles = list(bottles_by_type.get(primary, []))
            stray_pool = [b for b in all_bottles if b not in primary_bottles]
            n_stray = min(2, len(stray_pool))

            chosen = list(primary_bottles)
            chosen += rng.sample(stray_pool, k=n_stray)
            chosen = chosen[: opts["reviews"]]

            for bottle in chosen:
                # Scores are incidental now; criteria is style, not score.
                BottleReview.objects.update_or_create(
                    bottle=bottle,
                    reviewer=user,
                    event=None,
                    event_bottle=None,
                    defaults={
                        "nose": rng.randint(5, 9),
                        "taste": rng.randint(5, 9),
                        "finish": rng.randint(5, 9),
                        "value": rng.randint(5, 9),
                    },
                )

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {opts['count']} fake users ({created} newly created) "
            f"across {len(PRIMARY_STYLES)} whiskey styles."
        ))
