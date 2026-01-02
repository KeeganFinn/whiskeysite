
from accounts.models import CanonicalBottle

IDENTITY_FIELDS = [
    "name",
    "distillery_id",
    "whiskey_type",
    "age",
    "proof",
    "is_store_pick",
]

def canonical_identity_changed(canonical, incoming):
    """
    Returns True if the proposed values change the identity
    of the canonical bottle.
    """
    for field in IDENTITY_FIELDS:
        old = getattr(canonical, field)
        new = incoming.get(field)

        if old != new:
            return True

    return False


def fork_canonical(canonical, incoming, user):
    """
    Create a new canonical bottle when identity changes.
    Reviews remain on the old canonical.
    """
    return CanonicalBottle.objects.create(
        name=incoming["name"],
        distillery=incoming["distillery"],
        whiskey_type=incoming["whiskey_type"],
        age=incoming["age"],
        proof=incoming["proof"],
        is_store_pick=incoming["is_store_pick"],
        store_name=incoming.get("store_name"),
        created_by=user,
        forked_from=canonical,
    )

def resolve_canonical(incoming, user):
    """
    Return an existing canonical matching identity OR create a new one.
    """

    qs = CanonicalBottle.objects.filter(
        name=incoming["name"],
        distillery=incoming["distillery"],
        whiskey_type=incoming["whiskey_type"],
        age=incoming["age"],
        proof=incoming["proof"],
        is_store_pick=incoming["is_store_pick"],
        store_name=incoming["store_name"],
        duplicate_of__isnull=True,
    )

    existing = qs.first()
    if existing:
        return existing, False

    new = CanonicalBottle.objects.create(
        name=incoming["name"],
        distillery=incoming["distillery"],
        whiskey_type=incoming["whiskey_type"],
        age=incoming["age"],
        proof=incoming["proof"],
        is_store_pick=incoming["is_store_pick"],
        store_name=incoming["store_name"],
        created_by=user,
        forked_from=incoming.get("forked_from"),
    )

    return new, True