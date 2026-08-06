from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.urls import reverse
from datetime import datetime

from django.views.decorators.http import require_GET

from .forms import CustomUserCreationForm, CustomPasswordChangeForm, BottleReviewForm
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages
from .forms import ProfileUpdateForm
from .models import UsernameHistory, DistilleryAuditLog, BottleReview, CanonicalBottle, EventParticipant, EventBottle
from django.utils import timezone
from datetime import timedelta
from .forms import ChangeUsernameForm
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from .models import Follow
from .forms import BottleForm
from .models import Bottle, Distillery, Event
from django import forms as djforms
from django.db.models import Count, Avg, Sum, Q, When, IntegerField, Case
from django.shortcuts import  redirect
from django.db.models import F
from django.http import JsonResponse, HttpResponseForbidden, Http404
from django_ratelimit.decorators import ratelimit
from .climate_lookup import suggest_climate, CLIMATE_CHOICES, CLIMATE_LOOKUP
from .distillery_lookup import lookup_distillery_info
import csv
from django.http import HttpResponse
from django.db import models, transaction, IntegrityError
from .models import WHISKEY_TYPES
from .canonical import (
    canonical_identity_changed,
    fork_canonical, resolve_canonical,
)
from urllib.parse import urlencode

from django.utils.http import url_has_allowed_host_and_scheme
from . import palate
from .stats import site_stats



def normalize_int(value):
    if value in (None, "", "None"):
        return None
    return int(value)

def parse_score(raw):
    """Parse a review score (0–10) from raw POST input.

    Raises ValueError on anything that isn't a whole number in range, so callers
    can reject bad/malicious input instead of 500-ing or storing garbage
    (``.objects.create`` skips the model's field validators).
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("Scores must be whole numbers.")
    if not 0 <= value <= 10:
        raise ValueError("Scores must be between 0 and 10.")
    return value

def csv_safe(value):
    """Neutralize CSV formula injection for spreadsheet exports.

    A cell beginning with =, +, -, @ (or a leading control char) is treated as a
    formula by Excel/Sheets. Prefixing with an apostrophe forces it to text.
    """
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text

def parse_local_datetime(value):
    """
    Convert browser datetime-local input to UTC safely.
    """
    naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    return timezone.make_aware(naive, timezone.get_default_timezone())

@ratelimit(key="ip", rate="5/h", method="POST", block=False)
def register_view(request):
    if request.method == 'POST':
        # Throttle account-creation spam per source IP (see decorator above).
        if getattr(request, "limited", False):
            messages.error(
                request,
                "Too many sign-up attempts from your network. "
                "Please wait a while and try again.",
            )
            return redirect("register")

        form = CustomUserCreationForm(request.POST)
        add_bootstrap_classes(form)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('/')
    else:
        form = CustomUserCreationForm()
        add_bootstrap_classes(form)

    return render(request, 'accounts/register.html', {'form': form})

@ratelimit(key="ip", rate="10/m", method="POST", block=False)
def login_view(request):
    if request.method == 'POST':
        # Slow down password brute-forcing per source IP.
        if getattr(request, "limited", False):
            messages.error(
                request,
                "Too many login attempts. Please wait a minute and try again.",
            )
            return redirect("login")

        form = AuthenticationForm(request, data=request.POST)
        add_bootstrap_classes(form)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect('/')
    else:
        form = AuthenticationForm()
        add_bootstrap_classes(form)

    return render(request, 'accounts/login.html', {'form': form})

def logout_view(request):
    logout(request)
    return redirect('/')

from .models import Notification

from django.utils import timezone

@login_required
def home_view(request):
    notifications = request.user.notifications.filter(is_read=False)
    now = timezone.now()

    base_qs = (
        Event.objects
        .filter(
            Q(owner=request.user) |
            Q(participants__user=request.user),
            end_time__gte=now,   # exclude past events
        )
        .distinct()
    )

    active_events = base_qs.filter(
        start_time__lte=now,
        end_time__gte=now,
    ).order_by("end_time")

    upcoming_events = base_qs.filter(
        start_time__gt=now
    ).order_by("start_time")

    return render(
        request,
        "accounts/home.html",
        {
            "notifications": notifications,
            "active_events": active_events,
            "upcoming_events": upcoming_events,
        },
    )


def add_bootstrap_classes(form):
    for name, field in form.fields.items():
        # Booleans → checkbox style
        if isinstance(field, djforms.BooleanField):
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (existing + " form-check-input").strip()
        else:
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (existing + " form-control").strip()

@login_required
def profile_view(request, username=None):
    if username is None:
        profile_user = request.user
    else:
        profile_user = get_object_or_404(User, username=username)

    profile = profile_user.userprofile
    history = UsernameHistory.objects.filter(user=profile_user).order_by("-changed_at")

    # Followers / Following counts
    followers_count = Follow.objects.filter(following=profile_user).count()
    following_count = Follow.objects.filter(follower=profile_user).count()

    # Whether current viewer follows this profile
    is_following = False
    if request.user != profile_user:
        is_following = Follow.objects.filter(
            follower=request.user, following=profile_user
        ).exists()
    bottles = Bottle.objects.filter(user=profile_user)

    total = bottles.count()
    unopened = bottles.filter(status="unopened").count()
    opened = bottles.filter(status="opened").count()
    finished = bottles.filter(status="finished").count()

    total_value = bottles.aggregate(Sum("price"))["price__sum"] or 0
    avg_price = bottles.aggregate(Avg("price"))["price__avg"] or 0

    # Average age ignoring NAS
    real_ages = list(
        bottles.filter(age__isnull=False).exclude(age=0).values_list("age", flat=True)
    )
    avg_age = sum(real_ages) / len(real_ages) if real_ages else None

    nas_count = bottles.filter(Q(age__isnull=True) | Q(age=0)).count()

    common_type = (
        bottles.values("whiskey_type")
        .annotate(count=Count("id"))
        .order_by("-count")
        .first()
    )

    # "Back to Discover" link when arriving from the discover page.
    back_url = request.GET.get("next")
    if not (back_url and url_has_allowed_host_and_scheme(
        back_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )):
        back_url = None

    # Reviews written by this user (newest first).
    reviews = (
        BottleReview.objects
        .filter(reviewer=profile_user)
        .select_related("bottle", "bottle__distillery")
        .order_by("-created_at")[:200]
    )

    return render(request, "accounts/profile.html", {
        "profile_user": profile_user,
        "profile": profile,
        "history": history,
        "back_url": back_url,
        "reviews": reviews,
        "followers_count": followers_count,
        "following_count": following_count,
        "is_following": is_following,

        # NEW:
        "stats": {
            "total": total,
            "unopened": unopened,
            "opened": opened,
            "finished": finished,
            "total_value": total_value,
            "avg_price": avg_price,
            "avg_age": avg_age,
            "nas_count": nas_count,
            "common_type": common_type,
        }
    })

@login_required
def settings_view(request):
    user = request.user

    # Username change cooldown
    last_change = UsernameHistory.objects.filter(user=user).order_by("-changed_at").first()

    days_remaining = 0
    if last_change:
        days_remaining = max(0, 30 - (timezone.now() - last_change.changed_at).days)

    # Check if popup is needed
    blocked_days = request.session.pop("username_change_blocked", None)

    return render(request, "accounts/settings.html", {
        "days_remaining": days_remaining,
        "last_username_change": last_change,
        "blocked_days": blocked_days,
    })

@login_required
def change_password_view(request):
    if request.method == 'POST':
        form = CustomPasswordChangeForm(user=request.user, data=request.POST)
        add_bootstrap_classes(form)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            return redirect('/settings/')
    else:
        form = CustomPasswordChangeForm(user=request.user)
        add_bootstrap_classes(form)

    return render(request, "accounts/change_password.html", {"form": form})

from .forms import EmailChangeForm

@login_required
def change_email_view(request):
    if request.method == 'POST':
        form = EmailChangeForm(request.POST)
        add_bootstrap_classes(form)
        if form.is_valid():
            request.user.email = form.cleaned_data['email']
            request.user.save()
            messages.success(request, "Your email has been updated successfully.")
            return redirect('/settings/')
    else:
        form = EmailChangeForm(initial={'email': request.user.email})
        add_bootstrap_classes(form)

    return render(request, "accounts/change_email.html", {"form": form})


@login_required
def profile_settings_view(request):
    profile = request.user.userprofile

    if request.method == "POST":
        form = ProfileUpdateForm(request.POST, request.FILES, instance=profile)
        add_bootstrap_classes(form)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully.")
            return redirect('/profile/')
    else:
        form = ProfileUpdateForm(instance=profile)
        add_bootstrap_classes(form)

    return render(request, "accounts/profile_settings.html", {"form": form})


@login_required
def change_username_view(request):
    user = request.user

    # Check last change
    last_change = UsernameHistory.objects.filter(user=user).order_by("-changed_at").first()

    if last_change:
        days_since = (timezone.now() - last_change.changed_at).days

        if days_since < 30:
            remaining = 30 - days_since

            # Store remaining days in session so settings page can show popup
            request.session["username_change_blocked"] = remaining

            return redirect("/settings/")

    # --- If allowed, process normally ---
    if request.method == "POST":
        form = ChangeUsernameForm(request.POST)
        if form.is_valid():
            UsernameHistory.objects.create(
                user=user,
                old_username=user.username
            )

            user.username = form.cleaned_data["new_username"]
            user.save()

            messages.success(request, "Username updated successfully.")
            return redirect("/profile/")
    else:
        form = ChangeUsernameForm()

    return render(request, "accounts/change_username.html", {"form": form})

@login_required
def feed_view(request):
    # Get all users I follow
    following_ids = list(
        request.user.following.values_list("following_id", flat=True)
    )

    review_qs = (
        BottleReview.objects
        .select_related("bottle", "bottle__distillery", "reviewer")
        .order_by("-created_at")
    )

    # Recent reviews by people you follow.
    friend_reviews = review_qs.filter(reviewer_id__in=following_ids)[:15]

    # Recent reviews by everyone else (not you, not your friends).
    stranger_reviews = (
        review_qs
        .exclude(reviewer_id__in=following_ids)
        .exclude(reviewer=request.user)[:15]
    )

    # Optionally scope the stats to one friend's inventory.
    scoped_user = None
    friend_username = request.GET.get("friend", "").strip()
    if friend_username:
        scoped_user = (
            User.objects.filter(username=friend_username, id__in=following_ids).first()
        )

    return render(request, "accounts/feed.html", {
        "friend_reviews": friend_reviews,
        "stranger_reviews": stranger_reviews,
        "stats": site_stats(scoped_user),
        "scoped_user": scoped_user,
        "friend_query": friend_username,
    })


@login_required
def feed_friend_search(request):
    """Autocomplete over the people the current user follows."""
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse([], safe=False)

    following_ids = request.user.following.values_list("following_id", flat=True)
    friends = (
        User.objects.filter(id__in=following_ids, username__icontains=q)
        .order_by("username")[:10]
    )
    return JsonResponse(
        [{"username": u.username} for u in friends],
        safe=False,
    )

@login_required
def follow_user(request, user_id):
    target = get_object_or_404(User, id=user_id)
    if target != request.user:
        Follow.objects.get_or_create(follower=request.user, following=target)
        messages.success(request, f"You are now following {target.username}.")
    return _redirect_after_follow(request, target)

@login_required
def unfollow_user(request, user_id):
    target = get_object_or_404(User, id=user_id)
    Follow.objects.filter(follower=request.user, following=target).delete()
    messages.success(request, f"You unfollowed {target.username}.")
    return _redirect_after_follow(request, target)


def _redirect_after_follow(request, target):
    """Return to the page the follow came from when given a safe ?next=."""
    next_url = request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect(f"/profile/{target.username}/")

@login_required
def discover_autocomplete(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse([], safe=False)

    following_ids = set(
        Follow.objects.filter(follower=request.user)
        .values_list("following_id", flat=True)
    )

    users = (
        User.objects.exclude(id=request.user.id)
        .filter(username__icontains=q)
        .order_by("username")[:10]
    )

    return JsonResponse(
        [
            {
                "username": u.username,
                "is_following": u.id in following_ids,
            }
            for u in users
        ],
        safe=False,
    )


@login_required
def discover_view(request):
    q = request.GET.get("q", "").strip()
    match = request.GET.get("match", "")  # "", "similar", or "different"

    # Everyone except yourself, optionally filtered by the search bar.
    users = User.objects.exclude(id=request.user.id).select_related("userprofile")
    if q:
        users = users.filter(username__icontains=q)

    following_ids = set(
        Follow.objects.filter(follower=request.user).values_list("following_id", flat=True)
    )

    type_labels = dict(WHISKEY_TYPES)

    # --- Style signatures: review counts per whiskey type, per reviewer ---
    style_counts = {}  # reviewer_id -> {whiskey_type: count}
    for row in (
        BottleReview.objects
        .values("reviewer_id", "bottle__whiskey_type")
        .annotate(c=Count("id"))
    ):
        wtype = row["bottle__whiskey_type"]
        style_counts.setdefault(row["reviewer_id"], {})[wtype] = row["c"]

    my_vec = style_counts.get(request.user.id, {})

    users = list(users)
    for u in users:
        vec = style_counts.get(u.id, {})
        u.review_count = sum(vec.values())

        # Per-type breakdown, most-reviewed first.
        u.type_breakdown = [
            {"label": type_labels.get(t, t), "count": c}
            for t, c in sorted(vec.items(), key=lambda kv: kv[1], reverse=True)
        ]
        # Dominant style label (what we "tag" the person as).
        u.style_label = u.type_breakdown[0]["label"] if u.type_breakdown else None

        if my_vec and vec:
            sim = palate.cosine(my_vec, vec)
            u.style_match = palate.match_percent(sim)
            u._sim = sim
        else:
            u.style_match = None
            u._sim = None

    # --- Recommendation ordering by style similarity ---
    if match in ("similar", "different") and my_vec:
        rated = [u for u in users if u._sim is not None]
        unrated = [u for u in users if u._sim is None]
        # similar = highest cosine first; different = lowest first
        rated.sort(key=lambda u: u._sim, reverse=(match == "similar"))
        users = rated + unrated

    return render(request, "accounts/discover.html", {
        "users": users,
        "following_ids": following_ids,
        "q": q,
        "match": match,
        "has_palate": bool(my_vec),
    })

@login_required
def followers_list_view(request, username):
    profile_user = get_object_or_404(User, username=username)

    follower_ids = Follow.objects.filter(
        following=profile_user
    ).values_list("follower_id", flat=True)

    followers = User.objects.filter(
        id__in=follower_ids
    ).select_related("userprofile")

    return render(request, "accounts/follow_list.html", {
        "title": f"Followers of {profile_user.username}",
        "users": followers,
        "profile_user": profile_user,
    })


@login_required
def following_list_view(request, username):
    profile_user = get_object_or_404(User, username=username)

    following_ids = Follow.objects.filter(
        follower=profile_user
    ).values_list("following_id", flat=True)

    following = User.objects.filter(
        id__in=following_ids
    ).select_related("userprofile")

    return render(request, "accounts/follow_list.html", {
        "title": f"{profile_user.username} is Following",
        "users": following,
        "profile_user": profile_user,
    })

@login_required
def inventory_view(request):
    mode = request.GET.get("mode", "active")  # active | finished | lifetime

    # -------------------------------
    # BASE USER DATASET
    # -------------------------------
    user_qs = (
        Bottle.objects
        .select_related("distillery", "distillery__duplicate_of")
        .filter(user=request.user)
    )

    # -------------------------------
    # MODE FILTER (USED BY STATS + DISPLAY)
    # -------------------------------
    if mode == "active":
        mode_qs = user_qs.exclude(status="finished")
    elif mode == "finished":
        mode_qs = user_qs.filter(status="finished")
    else:
        mode_qs = user_qs  # lifetime

    # -------------------------------
    # STATS (MODE-AWARE, NEVER CAPPED)
    # -------------------------------
    stats_qs = mode_qs

    total = stats_qs.count()
    unopened = stats_qs.filter(status="unopened").count()
    opened = stats_qs.filter(status="opened").count()
    finished = stats_qs.filter(status="finished").count()

    total_value = stats_qs.aggregate(Sum("price"))["price__sum"] or 0
    avg_price = stats_qs.aggregate(Avg("price"))["price__avg"] or 0

    real_ages = list(
        stats_qs
        .filter(age__isnull=False)
        .exclude(age=0)
        .values_list("age", flat=True)
    )
    avg_age = sum(real_ages) / len(real_ages) if real_ages else None

    nas_count = stats_qs.filter(Q(age__isnull=True) | Q(age=0)).count()

    common_type = (
        stats_qs
        .values("whiskey_type")
        .annotate(count=Count("id"))
        .order_by("-count")
        .first()
    )

    # -------------------------------
    # DISPLAY QUERYSET (FILTER + SORT)
    # -------------------------------
    bottles = mode_qs

    price_param = request.GET.get("price")
    min_p = request.GET.get("min_price")
    max_p = request.GET.get("max_price")

    has_filters = any([price_param, min_p, max_p])

    if price_param == "0-50":
        bottles = bottles.filter(price__gte=0, price__lte=50)
    elif price_param == "50-100":
        bottles = bottles.filter(price__gte=50, price__lte=100)
    elif price_param == "100-250":
        bottles = bottles.filter(price__gte=100, price__lte=250)
    elif price_param == "250+":
        bottles = bottles.filter(price__gte=250)

    if min_p:
        bottles = bottles.filter(price__gte=min_p)
    if max_p:
        bottles = bottles.filter(price__lte=max_p)

    # -------------------------------
    # SORTING
    # -------------------------------
    sort = request.GET.get("sort", "created")

    if sort == "name":
        bottles = bottles.order_by("name")
    elif sort == "price":
        bottles = bottles.order_by(F("price").asc(nulls_last=True))
    elif sort == "age":
        bottles = bottles.order_by(F("age").asc(nulls_last=True))
    elif sort == "proof":
        bottles = bottles.order_by(F("proof").asc(nulls_last=True))
    elif sort == "status":
        bottles = bottles.order_by("status")
    else:
        bottles = bottles.order_by("-created_at")

    # -------------------------------
    # COUNT BEFORE UI CAP
    # -------------------------------
    total_matching = bottles.count()

    # -------------------------------
    # APPLY UI CAP (BROWSE MODE ONLY)
    # -------------------------------
    if not has_filters:
        bottles = bottles[:200]

    displayed_count = bottles.count()

    # Resolve duplicate distilleries (display only)
    for b in bottles:
        if b.distillery and b.distillery.duplicate_of:
            b.distillery = b.distillery.duplicate_of

    return render(
        request,
        "accounts/inventory.html",
        {
            "bottles": bottles,
            "form": BottleForm(),

            "mode": mode,
            "current_sort": sort,
            "current_price": price_param,
            "min_price": min_p,
            "max_price": max_p,

            "displayed_count": displayed_count,
            "total_matching": total_matching,

            "stats": {
                "total": total,
                "unopened": unopened,
                "opened": opened,
                "finished": finished,
                "total_value": total_value,
                "avg_price": avg_price,
                "avg_age": avg_age,
                "nas_count": nas_count,
                "common_type": common_type,
            },

            "CLIMATE_CHOICES": CLIMATE_CHOICES,
        },
    )




@login_required
def inventory_export_csv(request):
    """
    Full dataset export for the logged-in user (no cap, all statuses).
    Optional: support ?scope=current|finished|lifetime if you want later.
    """
    qs = (
        Bottle.objects
        .select_related("distillery")
        .filter(user=request.user)
        .order_by("-created_at")
    )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="whiskey_inventory.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Name", "Distillery", "Type", "Age", "Proof", "Price",
        "Status", "Store Pick", "Store Name", "Pick Details", "Added"
    ])

    for b in qs:
        writer.writerow([
            csv_safe(b.name),
            csv_safe(b.distillery.name if b.distillery else ""),
            b.get_whiskey_type_display(),
            b.age if b.age is not None else "",
            b.proof if b.proof is not None else "",
            b.price if b.price is not None else "",
            b.get_status_display(),
            "Yes" if b.is_store_pick else "No",
            csv_safe(b.store_name or ""),
            csv_safe((b.pick_details or "").replace("\n", " ").strip()),
            b.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        ])

    return response


@login_required
def inventory_export(request):
    mode = request.GET.get("mode", "lifetime")

    qs = Bottle.objects.filter(user=request.user)

    if mode == "active":
        qs = qs.exclude(status="finished")
    elif mode == "finished":
        qs = qs.filter(status="finished")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="whiskey_inventory.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Name",
        "Distillery",
        "Type",
        "Age",
        "Proof",
        "Price",
        "Status",
        "Store Pick",
        "Store Name",
        "Pick Details",
        "Added",
    ])

    for b in qs.order_by("-created_at"):
        writer.writerow([
            csv_safe(b.name),
            csv_safe(b.distillery.name if b.distillery else ""),
            b.get_whiskey_type_display(),
            b.age or "",
            b.proof or "",
            b.price or "",
            b.get_status_display(),
            "Yes" if b.is_store_pick else "No",
            csv_safe(b.store_name or ""),
            csv_safe((b.pick_details or "").replace("\n", " ").strip()),
            b.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        ])

    return response


@login_required
def add_bottle_view(request):
    if request.method == "POST":
        form = BottleForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please fix the errors in the form.")
            return redirect("inventory")

        bottle = form.save(commit=False)
        bottle.user = request.user

        # -----------------------------
        # Distillery handling (name-based)
        # -----------------------------
        dist_name = (form.cleaned_data.get("distillery_name") or "").strip()
        distillery = None

        if dist_name:
            distillery = Distillery.objects.filter(
                name__iexact=dist_name
            ).first()

            if not distillery:
                distillery = Distillery.objects.create(
                    name=dist_name,
                    added_by=request.user,
                    is_verified=False,
                )

        bottle.distillery = distillery

        # -----------------------------
        # Normalize identity fields
        # -----------------------------
        age = bottle.age if bottle.age not in ("", 0) else None
        proof = bottle.proof

        # -----------------------------
        # Canonical identity payload
        # -----------------------------
        incoming = {
            "name": bottle.name.strip(),
            "distillery": distillery,
            "whiskey_type": bottle.whiskey_type,
            "age": age,
            "proof": proof,
            "is_store_pick": bottle.is_store_pick,
            "store_name": bottle.store_name,
            "forked_from": None,  # inventory add is a root action
        }

        # -----------------------------
        # Resolve canonical
        # -----------------------------
        canonical, _created = resolve_canonical(incoming, request.user)

        bottle.canonical_bottle = canonical
        bottle.age = age
        bottle.proof = proof

        bottle.save()

        messages.success(request, "Bottle added to your inventory!")

    return redirect("inventory")

@login_required
def edit_bottle_view(request, bottle_id):
    bottle = get_object_or_404(Bottle, id=bottle_id, user=request.user)
    canonical = bottle.canonical_bottle

    def normalize_int(v):
        return None if v in (None, "", "None") else int(v)

    def normalize_float(v):
        return None if v in (None, "", "None") else float(v)

    if request.method == "POST":

        # =====================================================
        # 🔁 CONFIRMATION POST (rehydrate manually)
        # =====================================================
        if request.POST.get("confirm_identity_change"):
            name = request.POST.get("name", "").strip()
            whiskey_type = request.POST.get("whiskey_type")
            age = normalize_int(request.POST.get("age"))
            proof = normalize_float(request.POST.get("proof"))
            is_store_pick = request.POST.get("is_store_pick") == "on"
            store_name = request.POST.get("store_name") or None

            dist_name = (request.POST.get("distillery_name") or "").strip()
            distillery = None

            if dist_name:
                distillery = Distillery.objects.filter(name__iexact=dist_name).first()
                if not distillery:
                    distillery = Distillery.objects.create(
                        name=dist_name,
                        added_by=request.user,
                        is_verified=False,
                    )

        # =====================================================
        # 📝 NORMAL EDIT POST (use form validation)
        # =====================================================
        else:
            form = BottleForm(request.POST, instance=bottle)
            if not form.is_valid():
                messages.error(request, "Please fix the errors and try again.")
                return redirect("inventory")

            bottle = form.save(commit=False)
            bottle.user = request.user

            name = bottle.name.strip()
            whiskey_type = bottle.whiskey_type
            age = normalize_int(bottle.age)
            proof = normalize_float(bottle.proof)
            is_store_pick = bottle.is_store_pick
            store_name = bottle.store_name

            dist_name = (form.cleaned_data.get("distillery_name") or "").strip()
            distillery = None

            if dist_name:
                distillery = Distillery.objects.filter(name__iexact=dist_name).first()
                if not distillery:
                    distillery = Distillery.objects.create(
                        name=dist_name,
                        added_by=request.user,
                        is_verified=False,
                    )

        # =====================================================
        # 🧠 CANONICAL IDENTITY PAYLOAD
        # =====================================================
        incoming = {
            "name": name,
            "distillery": distillery,
            "whiskey_type": whiskey_type,
            "age": age,
            "proof": proof,
            "is_store_pick": is_store_pick,
            "store_name": store_name,
            "forked_from": canonical,
        }

        resolved, created = resolve_canonical(incoming, request.user)

        # =====================================================
        # ⛔ CONFIRMATION REQUIRED
        # =====================================================
        if (
                canonical is not None
                and resolved.id != canonical.id
                and not request.POST.get("confirm_identity_change")
        ):
            return render(
                request,
                "accounts/confirm_identity_change.html",
                {
                    "canonical": canonical,
                    "existing": None if created else resolved,
                    "incoming": {
                        "name": name,
                        "distillery_name": distillery.name if distillery else "",
                        "whiskey_type": whiskey_type,
                        "age": age or "",
                        "proof": proof or "",
                        "is_store_pick": "on" if is_store_pick else "",
                        "store_name": store_name or "",
                    },
                },
            )

        # =====================================================
        # ✅ APPLY TO INVENTORY + CANONICAL
        # =====================================================
        if bottle.canonical_bottle is None or bottle.canonical_bottle_id != resolved.id:
            bottle.canonical_bottle = resolved
        bottle.name = name
        bottle.distillery = distillery
        bottle.whiskey_type = whiskey_type
        bottle.age = age
        bottle.proof = proof
        bottle.is_store_pick = is_store_pick
        bottle.store_name = store_name

        bottle.save()

        messages.success(request, "Bottle updated.")
        return redirect("inventory")

    # Inventory edits only come from modal → no GET page
    return redirect("inventory")


@login_required
def delete_bottle_view(request, bottle_id):
    bottle = get_object_or_404(Bottle, id=bottle_id, user=request.user)

    # Warn user NOT to delete opened/finished bottles
    if bottle.status != "unopened":
        messages.warning(request, "You should change status instead of deleting bottles you've opened or finished.")
        return redirect("inventory")

    bottle.delete()
    messages.success(request, "Bottle deleted.")
    return redirect("inventory")


def infer_climate_view(request):
    country = request.GET.get("country")
    region = request.GET.get("region")

    climate = suggest_climate(country, region)

    return JsonResponse({
        "climate": climate
    })

def distillery_autocomplete(request):
    q = request.GET.get("q", "").strip()

    if len(q) < 2:
        return JsonResponse([], safe=False)

    results = (
        Distillery.objects
        .filter(name__icontains=q)
        .order_by(
            "-is_verified",  # verified first
            "name"
        )[:10]
    )

    return JsonResponse(
        [
            {
                "id": d.id,
                "name": d.name,
                "is_verified": d.is_verified,
            }
            for d in results
        ],
        safe=False
    )

@login_required
def add_distillery(request):
    if request.method == "POST":
        name = request.POST.get("name").strip()
        country = request.POST.get("country", "")
        region = request.POST.get("region", "")
        climate = request.POST.get("climate") or None

        distillery, created = Distillery.objects.get_or_create(
            name=name,
            defaults={
                "country": country,
                "region": region,
                "climate": climate,
                "added_by": request.user,
                "is_verified": False,
            },
        )

        messages.success(
            request,
            "Distillery submitted for approval."
            if created else
            "Distillery already exists."
        )

        # Only redirect back to a same-site URL to avoid an open redirect.
        return_to = request.POST.get("return_to")
        if return_to and url_has_allowed_host_and_scheme(
            return_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            # Append new_distillery to querystring
            separator = "&" if "?" in return_to else "?"
            query = urlencode({"new_distillery": name})
            return redirect(f"{return_to}{separator}{query}")

        return redirect("inventory")


@login_required
@permission_required("accounts.can_review_distillery")
def distillery_review_detail(request, pk):
    distillery = get_object_or_404(Distillery, pk=pk)

    canonical_choices = Distillery.objects.filter(
        is_verified=True,
        duplicate_of__isnull=True
    ).exclude(pk=distillery.pk)

    #Auto-suggest climate if missing
    suggested_climate = None
    if not distillery.climate:
        suggested_climate = suggest_climate(
            distillery.country,
            distillery.region
        )

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "approve":
            new_name = (request.POST.get("name") or "").strip()
            old_name = distillery.name
            if new_name:
                distillery.name = new_name
            distillery.country = request.POST.get("country")
            distillery.region = request.POST.get("region")
            distillery.climate = request.POST.get("climate")
            distillery.is_verified = True
            try:
                with transaction.atomic():
                    distillery.save()
            except IntegrityError:
                messages.error(
                    request,
                    f"A distillery named “{new_name}” already exists. "
                    "Consider marking this one as a duplicate instead.",
                )
                return redirect("distillery_review_detail", pk=distillery.pk)

            rename_note = (
                f" Renamed from “{old_name}”." if new_name and new_name != old_name else ""
            )
            DistilleryAuditLog.objects.create(
                distillery=distillery,
                action="approved",
                performed_by=request.user,
                notes="Approved via review screen." + rename_note,
            )

        elif action == "duplicate":
            canonical_id = request.POST.get("canonical")
            canonical = get_object_or_404(Distillery, pk=canonical_id)

            distillery.duplicate_of = canonical
            distillery.is_verified = False
            distillery.save()

            Bottle.objects.filter(distillery=distillery)\
                .update(distillery=canonical)

            DistilleryAuditLog.objects.create(
                distillery=distillery,
                action="duplicate",
                performed_by=request.user,
                notes=f"Duplicate of {canonical.name}"
            )

        elif action == "reject":
            # Junk distillery: remove it along with the canonical bottles created
            # for it and any reviews on those bottles (reviews cascade with the
            # canonical bottle). Inventory bottles keep their data but lose the
            # distillery/canonical link (SET_NULL).
            name = distillery.name
            canonicals = CanonicalBottle.objects.filter(distillery=distillery)
            reviews = BottleReview.objects.filter(bottle__in=canonicals)

            # Capture affected reviewers before the reviews are deleted, so we
            # can notify them their review was removed for bad data.
            affected_reviewer_ids = set(
                reviews.values_list("reviewer_id", flat=True)
            )
            review_count = reviews.count()
            canonical_count = canonicals.count()

            canonicals.delete()  # cascades to their reviews
            distillery.delete()

            notify_link = f"{reverse('review_search')}?removed_distillery={pk}"
            for reviewer_id in affected_reviewer_ids:
                Notification.objects.get_or_create(
                    user_id=reviewer_id,
                    link=notify_link,
                    defaults={
                        "message": (
                            f"A review of yours was removed because the distillery "
                            f"“{name}” was deleted for bad data."
                        )
                    },
                )

            messages.success(
                request,
                f"Removed “{name}” along with {canonical_count} bottle(s) "
                f"and {review_count} review(s). "
                f"Notified {len(affected_reviewer_ids)} reviewer(s).",
            )

        return redirect("distillery_review_list")

    # Build country + region suggestions from the climate lookup table and
    # any countries/regions already recorded on verified distilleries.
    countries = set()
    region_map = {}  # lowercase country -> set of region display names
    for c, regions in CLIMATE_LOOKUP.items():
        countries.add(c)
        region_map.setdefault(c, set()).update(
            r.title() for r in regions if r != "all"
        )
    for d in Distillery.objects.filter(is_verified=True).exclude(country__isnull=True):
        cl = (d.country or "").strip().lower()
        if not cl:
            continue
        countries.add(cl)
        if d.region:
            region_map.setdefault(cl, set()).add(d.region.strip().title())

    country_suggestions = sorted(c.title() for c in countries)
    region_map = {c: sorted(v) for c, v in region_map.items()}

    # Bottles that reference this distillery — context for the reviewer.
    canonical_bottles = (
        CanonicalBottle.objects.filter(distillery=distillery)
        .annotate(num_reviews=Count("reviews"))
        .order_by("name")
    )
    inventory_bottles = (
        Bottle.objects.filter(distillery=distillery)
        .select_related("user")
        .order_by("name")
    )

    return render(request, "accounts/review_detail.html", {
        "distillery": distillery,
        "canonical_choices": canonical_choices,
        "suggested_climate": suggested_climate,
        "CLIMATE_CHOICES": CLIMATE_CHOICES,
        "audit_logs": distillery.audit_logs.all(),
        "country_suggestions": country_suggestions,
        "region_map": region_map,
        "canonical_bottles": canonical_bottles,
        "inventory_bottles": inventory_bottles,
    })


@login_required
@permission_required("accounts.can_review_distillery")
def distillery_autofill(request, pk):
    """Look up a distillery's details from the web for the review screen."""
    distillery = get_object_or_404(Distillery, pk=pk)
    return JsonResponse(lookup_distillery_info(distillery.name))


@login_required
@permission_required("accounts.can_review_distillery")
def distillery_review_list(request):
    distilleries = Distillery.objects.filter(
        is_verified=False,
        duplicate_of__isnull=True
    ).order_by("created_at")

    return render(request, "accounts/review_list.html", {
        "distilleries": distilleries
    })

@login_required
def canonical_add_and_review(request):
    prefill_name = request.GET.get("name", "").strip()
    prefill_distillery = request.GET.get("new_distillery", "").strip()

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        distillery_name = request.POST.get("distillery_name", "").strip()
        whiskey_type = request.POST.get("whiskey_type")
        age = request.POST.get("age") or None
        proof = request.POST.get("proof")
        is_store_pick = bool(request.POST.get("is_store_pick"))
        store_name = request.POST.get("store_name", "").strip()

        if not name or not whiskey_type or not proof:
            messages.error(request, "Bottle name, whiskey type, and proof are required.")
            return redirect(request.path)

        # --------------------------------------------------
        # Distillery (existing or new)
        # --------------------------------------------------
        distillery = None
        if distillery_name:
            distillery = Distillery.objects.filter(
                name__iexact=distillery_name
            ).first()

        # --------------------------------------------------
        # Create canonical bottle
        # --------------------------------------------------
        bottle = CanonicalBottle.objects.create(
            name=name,
            distillery=distillery,
            whiskey_type=whiskey_type,
            age=age,
            proof=proof,
            is_store_pick=is_store_pick,
            store_name=store_name if is_store_pick else "",
            created_by=request.user,
        )

        # --------------------------------------------------
        # Redirect immediately to canonical review
        # --------------------------------------------------
        return redirect("add_canonical_review", pk=bottle.id)

    return render(
        request,
        "accounts/canonical_add_and_review.html",
        {
            "prefill_name": prefill_name,
            "prefill_distillery": prefill_distillery,
            "WHISKEY_TYPES": WHISKEY_TYPES,
            "CLIMATE_CHOICES": CLIMATE_CHOICES,
        },
    )

@login_required
def infer_climate_view(request):
    country = request.GET.get("country")
    region = request.GET.get("region")

    climate = suggest_climate(country, region)

    return JsonResponse({
        "climate": climate
    })

@login_required
@permission_required("accounts.can_review_distillery")
def export_distillery_audit_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="distillery_audit_log.csv"'

    writer = csv.writer(response)

    # Header row
    writer.writerow([
        "Distillery",
        "Action",
        "Performed By",
        "Notes",
        "Timestamp",
    ])

    logs = (
        DistilleryAuditLog.objects
        .select_related("distillery", "performed_by")
        .order_by("-created_at")
    )

    for log in logs:
        writer.writerow([
            csv_safe(log.distillery.name),
            log.get_action_display(),
            csv_safe(log.performed_by.username if log.performed_by else "System"),
            csv_safe(log.notes),
            log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        ])

    return response

@login_required
def add_review(request, pk):
    inventory_bottle = get_object_or_404(Bottle, pk=pk, user=request.user)
    source = request.POST.get("source", "inventory")
    if request.method == "POST":
        # 1) Ensure canonical exists/linked
        if inventory_bottle.canonical_bottle is None:
            canonical, _created = CanonicalBottle.objects.get_or_create(
                name=inventory_bottle.name,
                distillery=inventory_bottle.distillery,
                whiskey_type=inventory_bottle.whiskey_type,
                age=inventory_bottle.age,
                proof=inventory_bottle.proof,
                is_store_pick=inventory_bottle.is_store_pick,
                store_name=inventory_bottle.store_name if inventory_bottle.is_store_pick else None,
                defaults={"created_by": request.user},
            )
            inventory_bottle.canonical_bottle = canonical
            inventory_bottle.save(update_fields=["canonical_bottle"])
        else:
            canonical = inventory_bottle.canonical_bottle

        # 2) Create review linked to canonical
        try:
            nose = parse_score(request.POST.get("nose"))
            taste = parse_score(request.POST.get("taste"))
            finish = parse_score(request.POST.get("finish"))
            value = parse_score(request.POST.get("value"))
        except ValueError:
            messages.error(request, "Please provide valid scores (0–10).")
            return redirect("inventory")

        BottleReview.objects.create(
            bottle=canonical,
            reviewer=request.user,
            nose=nose,
            taste=taste,
            finish=finish,
            value=value,
            notes=request.POST.get("notes", "")[:1000],
        )

        return redirect(f"{reverse('canonical_bottle_detail', args=[inventory_bottle.canonical_bottle.id])}?from={source}")

    return redirect("inventory")

@login_required
def canonical_bottle_detail(request, pk):
    canonical = get_object_or_404(CanonicalBottle, pk=pk)


    show = request.GET.get("show", "all")

    qs = BottleReview.objects.filter(bottle=canonical).select_related("reviewer")

    if show == "mine":
        reviews = qs.filter(reviewer=request.user)
    else:
        reviews = qs

    agg = qs.aggregate(
        avg_score=Avg("final_score"),
        review_count=Count("id"),
    )

    source = request.GET.get("from", "review_search")

    # Optional explicit "go back where I came from" link.
    back_url = request.GET.get("next")
    if not (back_url and url_has_allowed_host_and_scheme(
        back_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )):
        back_url = None

    return render(
        request,
        "accounts/canonical_bottle_detail.html",
        {
            "canonical": canonical,
            "reviews": reviews,
            "avg_score": agg["avg_score"] or 0,
            "review_count": agg["review_count"] or 0,
            "show": show,
            "source": source,
            "back_url": back_url,
        },
    )

@login_required
def review_search(request):
    q = (request.GET.get("q") or "").strip()

    bottles = CanonicalBottle.objects.select_related("distillery")

    if q:
        bottles = bottles.filter(
            Q(name__icontains=q) |
            Q(distillery__name__icontains=q)
        )

    # Add aggregate info per bottle for display (avg + count)
    bottles = bottles.annotate(
        avg_score=Avg("reviews__final_score"),
        review_count=Count("reviews")
    ).order_by("name")[:200]  # cap results for now

    return render(
        request,
        "accounts/review_search.html",
        {
            "q": q,
            "bottles": bottles,
        }
    )

@login_required
def add_canonical_review(request, pk):
    canonical = get_object_or_404(CanonicalBottle, pk=pk)

    # IMPORTANT: preserve where the user came from
    source = request.POST.get("source") or request.GET.get("source") or "review_search"

    if request.method == "POST":
        try:
            nose = parse_score(request.POST.get("nose"))
            taste = parse_score(request.POST.get("taste"))
            finish = parse_score(request.POST.get("finish"))
            value = parse_score(request.POST.get("value"))
        except ValueError:
            messages.error(request, "Please provide valid scores (0–10).")
            return redirect("add_canonical_review", pk=canonical.id)

        BottleReview.objects.create(
            bottle=canonical,
            reviewer=request.user,
            nose=nose,
            taste=taste,
            finish=finish,
            value=value,
            notes=request.POST.get("notes", "")[:1000],  # HARD CAP
        )

        # Redirect back to canonical WITH source
        url = reverse("canonical_bottle_detail", args=[canonical.id])
        return redirect(f"{url}?source={source}")

    return render(
        request,
        "accounts/add_canonical_review.html",
        {
            "canonical": canonical,
            "source": source,  # pass to template
        },
    )

@login_required
def inventory_to_canonical(request, pk):
    bottle = get_object_or_404(Bottle, pk=pk, user=request.user)

    # Ensure canonical exists
    if not bottle.canonical_bottle:
        canonical, _ = CanonicalBottle.objects.get_or_create(
            name=bottle.name,
            defaults={
                "distillery": bottle.distillery,
                "whiskey_type": bottle.whiskey_type,
                "age": bottle.age,
                "proof": bottle.proof,
                "is_store_pick": bottle.is_store_pick,
                "store_name": bottle.store_name,
                "created_by": request.user,
            },
        )
        bottle.canonical_bottle = canonical
        bottle.save(update_fields=["canonical_bottle"])
    else:
        canonical = bottle.canonical_bottle

    return redirect(
        reverse("canonical_bottle_detail", args=[canonical.id])
        + "?from=inventory"
    )

@login_required
def event_create(request):
    if request.method == "POST":
        Event.objects.create(
            owner=request.user,
            name=request.POST["name"],
            description=request.POST.get("description", ""),
            location=request.POST.get("location", ""),
            visibility=request.POST.get("visibility", "friends"),
            start_time=parse_local_datetime(request.POST["start_time"]),
            end_time=parse_local_datetime(request.POST["end_time"]),
        )

        messages.success(request, "Event created.")
        return redirect("events_list")

    return render(request, "accounts/event_form.html", {"mode": "create"})


@login_required
def add_event_review(request, event_id, event_bottle_id):
    event = get_object_or_404(Event, pk=event_id)
    event_bottle = get_object_or_404(EventBottle, pk=event_bottle_id, event=event)

    # 🔒 Must be participant or owner
    if not (
        event.owner_id == request.user.id or
        EventParticipant.objects.filter(event=event, user=request.user).exists()
    ):
        return HttpResponseForbidden("You are not part of this event.")

    # 🔒 Reviews only while event is active
    if event.is_past:
        messages.error(request, "This event is closed for reviews.")
        return redirect("event_detail", event.id)

    # ✅ GET existing review ONLY (no creation here)
    review = BottleReview.objects.filter(
        event=event,
        event_bottle=event_bottle,
        reviewer=request.user,
    ).first()

    if request.method == "POST":
        review = BottleReview.objects.filter(
            event=event,
            event_bottle=event_bottle,
            reviewer=request.user,
        ).first()

        if not review:
            review = BottleReview(
                reviewer=request.user,
                event=event,
                event_bottle=event_bottle,
                bottle=event_bottle.canonical_bottle,
            )

        # Assign fields EXPLICITLY
        try:
            review.nose = parse_score(request.POST.get("nose"))
            review.taste = parse_score(request.POST.get("taste"))
            review.finish = parse_score(request.POST.get("finish"))
            review.value = parse_score(request.POST.get("value"))
        except ValueError:
            messages.error(request, "Please provide valid scores (0–10).")
            return redirect("event_detail", event.id)
        review.notes = request.POST.get("notes", "")[:1000]

        review.save()

        messages.success(request, "Your review has been saved.")
        return redirect("event_detail", event.id)

    return render(
        request,
        "accounts/add_event_review.html",
        {
            "event": event,
            "event_bottle": event_bottle,
            "review": review,
        },
    )


EVENTS_LIST_LIMIT = 25


@login_required
def events_list(request):
    now = timezone.now()

    # Whether the current user has been added to (is a participant of) the event.
    is_participant = models.Exists(
        EventParticipant.objects.filter(
            event=models.OuterRef("pk"),
            user=request.user,
        )
    )

    # Events the user can see, annotated so added-to events can float to the top.
    visible_events = (
        Event.objects.filter(
            Q(visibility="public") |
            Q(visibility="friends", participants__user=request.user) |
            Q(owner=request.user)
        )
        .distinct()
        .annotate(is_participant=is_participant)
    )

    # Split into buckets. Within Active/Upcoming, events the user was added to
    # come first; then cap the whole page at EVENTS_LIST_LIMIT events.
    active_events = list(
        visible_events.filter(
            start_time__lte=now,
            end_time__gte=now,
        ).order_by("-is_participant", "end_time")
    )

    upcoming_events = list(
        visible_events.filter(start_time__gt=now)
        .order_by("-is_participant", "start_time")
    )

    past_events = list(
        visible_events.filter(end_time__lt=now).order_by("-end_time")
    )

    # Cap at 25 total, filling Active → Upcoming → Past in that order.
    remaining = EVENTS_LIST_LIMIT
    active_events = active_events[:remaining]
    remaining -= len(active_events)
    upcoming_events = upcoming_events[:remaining]
    remaining -= len(upcoming_events)
    past_events = past_events[:remaining]

    return render(
        request,
        "accounts/events_list.html",
        {
            "upcoming_events": upcoming_events,
            "active_events": active_events,
            "past_events": past_events,
        },
    )

@login_required
def event_detail(request, pk):
    event = get_object_or_404(Event, pk=pk)

    is_owner = (event.owner_id == request.user.id)
    is_attendee = (
        is_owner or
        EventParticipant.objects.filter(event=event, user=request.user).exists()
    )

    # Access control: "Friends Only" events are visible only to the owner and
    # invited participants. 404 (not 403) so we don't confirm the event exists.
    if event.visibility != "public" and not is_attendee:
        raise Http404("Event not found.")

    show_location = (not event.is_past) or is_attendee

    bottles = (
        EventBottle.objects
        .filter(event=event)
        .select_related(
            "canonical_bottle",
            "canonical_bottle__distillery",
            "added_by",
        )
        .order_by("added_at")
    )

    participants = (
        EventParticipant.objects
        .filter(event=event)
        .select_related("user")
        .order_by("added_at")
    )

    # =====================================================
    # REVIEWS: user review, aggregates, all comments
    # =====================================================

    user_reviews_by_event_bottle = {}
    agg_by_event_bottle = {}
    all_reviews_by_event_bottle = {}

    # (A) Current user's reviews
    if is_attendee:
        my_reviews = (
            BottleReview.objects
            .filter(
                event=event,
                reviewer=request.user,
                event_bottle__in=bottles,
            )
        )
        user_reviews_by_event_bottle = {
            r.event_bottle_id: r for r in my_reviews
        }

    # (B) Aggregates per bottle
    aggs = (
        BottleReview.objects
        .filter(event=event, event_bottle__in=bottles)
        .values("event_bottle_id")
        .annotate(
            review_count=Count("id"),
            avg_nose=Avg("nose"),
            avg_taste=Avg("taste"),
            avg_finish=Avg("finish"),
            avg_value=Avg("value"),
        )
    )

    for row in aggs:
        parts = [
            row.get("avg_nose"),
            row.get("avg_taste"),
            row.get("avg_finish"),
            row.get("avg_value"),
        ]
        parts = [p for p in parts if p is not None]
        overall = (sum(parts) / len(parts)) if parts else None

        agg_by_event_bottle[row["event_bottle_id"]] = {
            "review_count": row["review_count"],
            "avg_nose": row.get("avg_nose"),
            "avg_taste": row.get("avg_taste"),
            "avg_finish": row.get("avg_finish"),
            "avg_value": row.get("avg_value"),
            "overall": overall,
        }

    # (C) All reviews + comments
    all_reviews = (
        BottleReview.objects
        .filter(event=event, event_bottle__in=bottles)
        .select_related("reviewer")
        .order_by("created_at")
    )

    for r in all_reviews:
        # overall_10 is now a model property (final_score / 10).
        all_reviews_by_event_bottle.setdefault(
            r.event_bottle_id, []
        ).append(r)

    # =====================================================
    # ATTACH TO EACH EventBottle (for template)
    # =====================================================
    for eb in bottles:
        user_review = user_reviews_by_event_bottle.get(eb.id)

        eb.user_review = user_review
        eb.all_reviews = all_reviews_by_event_bottle.get(eb.id, [])

        if user_review and user_review.final_score is not None:
            # Always derive from canonical weighted score
            eb.user_overall = user_review.final_score / 10.0
        else:
            eb.user_overall = None

        eb.agg = agg_by_event_bottle.get(
            eb.id,
            {
                "review_count": 0,
                "avg_nose": None,
                "avg_taste": None,
                "avg_finish": None,
                "avg_value": None,
                "overall": None,
            },
        )

    # =====================================================

    # =====================================================
    # EVENT LEADERBOARD
    # =====================================================

    leaderboard = []

    for eb in bottles:
        agg = agg_by_event_bottle.get(eb.id)
        if not agg or agg["overall"] is None:
            continue

        leaderboard.append({
            "event_bottle": eb,
            "score": agg["overall"],  # true /10
        })

    # Sort strictly by score (highest first)
    leaderboard.sort(key=lambda x: x["score"], reverse=True)

    # Assign ranks WITH TIE HANDLING
    last_score = None
    current_rank = 0

    for idx, row in enumerate(leaderboard, start=1):
        if last_score is None or row["score"] < last_score:
            current_rank = idx
        row["rank"] = current_rank
        last_score = row["score"]

    # Map ranks back to bottles
    rank_by_event_bottle = {
        row["event_bottle"].id: row["rank"]
        for row in leaderboard
    }

    for eb in bottles:
        eb.rank = rank_by_event_bottle.get(eb.id)

    my_leaderboard = []

    for eb in bottles:
        if eb.user_overall is not None:
            my_leaderboard.append({
                "event_bottle": eb,
                "score": eb.user_overall,  # already /10
            })

    # Sort by my score
    my_leaderboard.sort(key=lambda x: x["score"], reverse=True)

    # Assign ranks (ties handled)
    last_score = None
    current_rank = 0

    for idx, row in enumerate(my_leaderboard, start=1):
        if last_score is None or row["score"] < last_score:
            current_rank = idx
        row["rank"] = current_rank
        last_score = row["score"]

    # Attach my_rank to bottles
    my_rank_by_bottle = {
        row["event_bottle"].id: row["rank"]
        for row in my_leaderboard
    }

    for eb in bottles:
        eb.my_rank = my_rank_by_bottle.get(eb.id)

    return render(
        request,
        "accounts/event_detail.html",
        {
            "event": event,
            "is_owner": is_owner,
            "is_attendee": is_attendee,
            "show_location": show_location,
            "bottles": bottles,
            "participants": participants,
            "WHISKEY_TYPES": WHISKEY_TYPES,
            "CLIMATE_CHOICES": CLIMATE_CHOICES,
            "leaderboard": leaderboard,
            "my_leaderboard": my_leaderboard,
        },
    )

@login_required
def event_form(request, pk=None):
    event = None
    mode = "create"

    if pk:
        event = get_object_or_404(Event, pk=pk, owner=request.user)
        mode = "edit"

    if request.method == "POST":
        if event:
            obj = event
        else:
            obj = Event(owner=request.user)

        obj.name = request.POST["name"]
        obj.description = request.POST.get("description", "")
        obj.location = request.POST.get("location", "")
        obj.start_time = request.POST["start_time"]
        obj.end_time = request.POST["end_time"]
        obj.visibility = request.POST.get("visibility", "friends")

        obj.save()

        # owner automatically becomes participant (only on create)
        if not event:
            EventParticipant.objects.get_or_create(
                event=obj,
                user=request.user,
            )

        messages.success(request, "Event saved.")
        return redirect("event_detail", pk=obj.id)

    return render(
        request,
        "accounts/event_form.html",
        {
            "event": event,
            "mode": mode,
        },
    )

@login_required
def event_edit(request, pk):
    event = get_object_or_404(Event, pk=pk, owner=request.user)

    if request.method == "POST":
        event.name = request.POST["name"]
        event.description = request.POST.get("description", "")
        event.location = request.POST.get("location", "")
        event.visibility = request.POST.get("visibility", "friends")

        event.start_time = parse_local_datetime(request.POST["start_time"])
        event.end_time = parse_local_datetime(request.POST["end_time"])

        event.save()

        messages.success(request, "Event updated.")
        return redirect("event_detail", pk=event.id)

    return render(
        request,
        "accounts/event_form.html",
        {
            "event": event,
            "mode": "edit",
        },
    )

@login_required
def event_delete(request, pk):
    event = get_object_or_404(Event, pk=pk, owner=request.user)

    if request.method == "POST":
        event.delete()
        messages.success(request, "Event deleted.")
        return redirect("events_list")

    return render(
        request,
        "accounts/event_confirm_delete.html",
        {"event": event},
    )

@login_required
def event_add_bottle(request, pk):
    event = get_object_or_404(Event, pk=pk, owner=request.user)

    if event.is_past:
        messages.error(request, "You can’t add bottles after the event ends.")
        return redirect("event_detail", pk=pk)

    if request.method == "POST":
        name = request.POST["name"].strip()
        whiskey_type = request.POST["whiskey_type"]
        proof = request.POST.get("proof")
        age = request.POST.get("age") or None
        distillery_name = request.POST.get("distillery_name") or None
        is_store_pick = bool(request.POST.get("is_store_pick"))
        store_name = request.POST.get("store_name") or None

        if not proof:
            messages.error(request, "Proof is required.")
            return redirect("event_detail", pk=pk)

        # ---------------------------
        # Distillery (name-based)
        # ---------------------------
        distillery = None
        if distillery_name:
            distillery = Distillery.objects.filter(
                name__iexact=distillery_name.strip()
            ).first()

            if not distillery:
                distillery = Distillery.objects.create(
                    name=distillery_name.strip(),
                    added_by=request.user,
                    is_verified=False,
                )

        # Normalize values
        age = int(age) if age not in (None, "", "None") else None
        proof = float(proof)

        # ---------------------------
        # Canonical resolution (FIX)
        # ---------------------------
        incoming = {
            "name": name,
            "distillery": distillery,
            "whiskey_type": whiskey_type,
            "age": age,
            "proof": proof,
            "is_store_pick": is_store_pick,
            "store_name": store_name,
        }

        normalize_store_pick(incoming)

        canonical, _created = resolve_canonical(incoming, request.user)

        # ---------------------------
        # Event bottle
        # ---------------------------
        EventBottle.objects.get_or_create(
            event=event,
            canonical_bottle=canonical,
            defaults={"added_by": request.user},
        )

        messages.success(request, "Bottle added to event.")
        return redirect("event_detail", pk=pk)

    return redirect("event_detail", pk=pk)


@login_required
def event_add_participant(request, pk):
    event = get_object_or_404(Event, pk=pk, owner=request.user)

    if event.is_past:
        messages.error(request, "This event has ended. You can no longer add participants.")
        return redirect("event_detail", pk=event.id)

    if request.method == "POST":
        username = request.POST.get("username")

        try:
            user = User.objects.get(username=username)
            EventParticipant.objects.get_or_create(
                event=event,
                user=user,
            )
            messages.success(request, f"{user.username} added to event.")
        except User.DoesNotExist:
            messages.error(request, "User not found.")

    return redirect("event_detail", pk=event.id)

@login_required
def event_remove_participant(request, pk, user_id):
    event = get_object_or_404(Event, pk=pk, owner=request.user)

    EventParticipant.objects.filter(
        event=event,
        user_id=user_id,
    ).delete()

    messages.success(request, "Participant removed.")
    return redirect("event_detail", pk=event.id)

@login_required
def event_user_search(request, pk):
    # Only the owner adds participants, so only the owner may search users.
    event = get_object_or_404(Event, pk=pk, owner=request.user)

    if event.is_past:
        return JsonResponse([], safe=False)

    q = request.GET.get("q", "").strip()
    if not q:
        return JsonResponse([], safe=False)

    existing_ids = EventParticipant.objects.filter(
        event=event
    ).values_list("user_id", flat=True)

    following_ids = Follow.objects.filter(
        follower=request.user
    ).values_list("following_id", flat=True)

    users = (
        User.objects
        .exclude(id=request.user.id)
        .exclude(id__in=existing_ids)
        .filter(username__icontains=q)
        .annotate(
            is_following=models.Case(
                models.When(id__in=following_ids, then=models.Value(1)),
                default=models.Value(0),
                output_field=models.IntegerField(),
            )
        )
        .order_by("-is_following", "username")[:20]
    )

    return JsonResponse([
        {
            "id": u.id,
            "username": u.username,
            "is_following": bool(u.is_following),
        }
        for u in users
    ], safe=False)

@login_required
def notification_redirect(request, pk):
    notification = get_object_or_404(
        Notification,
        pk=pk,
        user=request.user
    )

    # Mark as read
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])

    return redirect(notification.link)

@login_required
def event_delete_bottle(request, event_id, pk):
    event = get_object_or_404(Event, pk=event_id, owner=request.user)
    bottle = get_object_or_404(EventBottle, pk=pk, event=event)

    if event.is_past:
        messages.error(request, "Event is closed.")
        return redirect("event_detail", pk=event_id)

    bottle.delete()
    messages.success(request, "Bottle removed from event.")
    return redirect("event_detail", pk=event_id)

@login_required
def event_edit_bottle(request, event_id, pk):
    event = get_object_or_404(Event, pk=event_id, owner=request.user)
    event_bottle = get_object_or_404(EventBottle, pk=pk, event=event)
    canonical = event_bottle.canonical_bottle

    if event.is_past:
        messages.error(request, "Event is closed.")
        return redirect("event_detail", pk=event_id)

    if request.method == "POST":

        def normalize_int(v):
            return None if v in (None, "", "None") else int(v)

        def normalize_float(v):
            return None if v in (None, "", "None") else float(v)

        # ---- Incoming values ----
        name = request.POST["name"].strip()
        whiskey_type = request.POST["whiskey_type"]
        proof = normalize_float(request.POST.get("proof"))
        age = normalize_int(request.POST.get("age"))
        is_store_pick = bool(request.POST.get("is_store_pick"))
        store_name = request.POST.get("store_name") or None

        # ---- Distillery (inventory-style) ----
        distillery = None
        dist_name = (request.POST.get("distillery_name") or "").strip()

        if dist_name:
            distillery = Distillery.objects.filter(
                name__iexact=dist_name
            ).first()

            if not distillery:
                distillery = Distillery.objects.create(
                    name=dist_name,
                    added_by=request.user,
                    is_verified=False,
                )

        incoming = {
            "name": name,
            "distillery": distillery,
            "whiskey_type": whiskey_type,
            "age": age,
            "proof": proof,
            "is_store_pick": is_store_pick,
            "store_name": store_name,
            "forked_from": canonical,
        }

        normalize_store_pick(incoming)

        # ---- Identity check ----
        resolved, created = resolve_canonical(incoming, request.user)

        if resolved.id != canonical.id:
            # Identity changed OR reverted → reattach
            event_bottle.canonical_bottle = resolved
            event_bottle.save()
        else:
            # Same canonical → safe in-place update
            canonical.name = name
            canonical.distillery = distillery
            canonical.whiskey_type = whiskey_type
            canonical.age = age
            canonical.proof = proof
            canonical.is_store_pick = is_store_pick
            canonical.store_name = store_name
            canonical.save()

        messages.success(request, "Event bottle updated.")
        return redirect("event_detail", pk=event_id)

    return render(
        request,
        "accounts/event_edit_bottle.html",
        {
            "event": event,
            "event_bottle": event_bottle,
            "canonical": canonical,
            "WHISKEY_TYPES": WHISKEY_TYPES,
            "CLIMATE_CHOICES": CLIMATE_CHOICES,
        },
    )

@require_GET
def suggest_climate_view(request):
    country = (request.GET.get("country") or "").strip()
    region = (request.GET.get("region") or "").strip()

    code = suggest_climate(country, region)
    return JsonResponse({"climate": code})


def normalize_store_pick(data: dict):
    """
    Canonical rule:
    - Store pick only matters if is_store_pick == True
    - Otherwise store_name MUST be None
    """
    if not data.get("is_store_pick"):
        data["is_store_pick"] = False
        data["store_name"] = None
