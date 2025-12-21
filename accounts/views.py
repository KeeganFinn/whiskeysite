from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from .forms import CustomUserCreationForm, CustomPasswordChangeForm, BottleReviewForm
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages
from .forms import ProfileUpdateForm
from .models import UsernameHistory, DistilleryAuditLog, BottleReview
from django.utils import timezone
from datetime import timedelta
from .forms import ChangeUsernameForm
from .models import Post
from .forms import PostForm
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from .models import Follow
from .forms import BottleForm
from .models import Bottle, Distillery
from django import forms as djforms
from django.db.models import Count, Avg, Sum, Q
from django.shortcuts import  redirect
from django.db.models import F
from django.http import JsonResponse
from .climate_lookup import suggest_climate, CLIMATE_CHOICES
import csv
from django.http import HttpResponse
from django.core.paginator import Paginator


def register_view(request):
    if request.method == 'POST':
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

def login_view(request):
    if request.method == 'POST':
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

def home_view(request):
    return render(request, 'accounts/home.html')

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

    return render(request, "accounts/profile.html", {
        "profile_user": profile_user,
        "profile": profile,
        "history": history,
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
def create_post_view(request):
    if request.method == "POST":
        # === Rate Limit: 1 post every 30 seconds ===
        cooldown = timedelta(seconds=30)
        last_post = Post.objects.filter(user=request.user).order_by("-created_at").first()

        if last_post and timezone.now() - last_post.created_at < cooldown:
            remaining = cooldown - (timezone.now() - last_post.created_at)
            seconds_left = int(remaining.total_seconds())
            messages.error(request, f"You're posting too fast. Try again in {seconds_left} seconds.")
            return redirect("/feed/")

        # === Validate form ===
        form = PostForm(request.POST)
        if form.is_valid():
            post = form.save(commit=False)
            post.user = request.user
            post.save()
            messages.success(request, "Your whiskey thoughts have been shared!")
        else:
            # Send each validation error as a toast
            for field_errors in form.errors.values():
                for error_msg in field_errors:
                    messages.error(request, error_msg)

        return redirect("/feed/")

    # If someone hits the URL via GET, just send them to the feed
    return redirect("/feed/")

@login_required
def feed_view(request):
    # Get all users I follow
    following_users = request.user.following.values_list("following_id", flat=True)

    # Include my own posts too
    posts = Post.objects.filter(
        user__in=list(following_users) + [request.user.id]
    ).select_related("user").order_by("-created_at")

    form = PostForm()
    return render(request, "accounts/feed.html", {
        "posts": posts,
        "form": form,
    })

@login_required
def follow_user(request, user_id):
    target = get_object_or_404(User, id=user_id)
    if target != request.user:
        Follow.objects.get_or_create(follower=request.user, following=target)
        messages.success(request, f"You are now following {target.username}.")
    return redirect(f"/profile/{target.username}/")

@login_required
def unfollow_user(request, user_id):
    target = get_object_or_404(User, id=user_id)
    Follow.objects.filter(follower=request.user, following=target).delete()
    messages.success(request, f"You unfollowed {target.username}.")
    return redirect(f"/profile/{target.username}/")

@login_required
def discover_view(request):
    # Everyone except yourself
    users = User.objects.exclude(id=request.user.id).select_related("userprofile")

    # Mark which ones you're already following
    following_ids = set(
        Follow.objects.filter(follower=request.user).values_list("following_id", flat=True)
    )

    return render(request, "accounts/discover.html", {
        "users": users,
        "following_ids": following_ids
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
            b.name,
            b.distillery.name if b.distillery else "",
            b.get_whiskey_type_display(),
            b.age if b.age is not None else "",
            b.proof if b.proof is not None else "",
            b.price if b.price is not None else "",
            b.get_status_display(),
            "Yes" if b.is_store_pick else "No",
            b.store_name or "",
            (b.pick_details or "").replace("\n", " ").strip(),
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
            b.name,
            b.distillery.name if b.distillery else "",
            b.get_whiskey_type_display(),
            b.age or "",
            b.proof or "",
            b.price or "",
            b.get_status_display(),
            "Yes" if b.is_store_pick else "No",
            b.store_name or "",
            (b.pick_details or "").replace("\n", " ").strip(),
            b.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        ])

    return response


@login_required
def add_bottle_view(request):
    if request.method == "POST":
        form = BottleForm(request.POST)
        if form.is_valid():
            bottle = form.save(commit=False)
            bottle.user = request.user

            # ---- Distillery handling ----
            dist_name = form.cleaned_data.get("distillery_name", "").strip()
            dist_instance = None
            is_new_distillery = False

            if dist_name:
                # Try case-insensitive match
                dist_instance = Distillery.objects.filter(
                    name__iexact=dist_name
                ).first()

                # If not found, create a new one
                if not dist_instance:
                    dist_instance = Distillery.objects.create(
                        name=dist_name,
                        added_by=request.user,
                        is_verified=False,
                    )
                    is_new_distillery = True

            bottle.distillery = dist_instance
            bottle.save()

            # Optional: remember that we just created a new distillery
            if is_new_distillery:
                request.session["new_distillery_id"] = dist_instance.id

            messages.success(request, "Bottle added to your inventory!")
        else:
            messages.error(request, "Please fix the errors in the form.")

    return redirect("inventory")

@login_required
def edit_bottle_view(request, bottle_id):
    bottle = get_object_or_404(Bottle, id=bottle_id, user=request.user)

    if request.method == "POST":
        form = BottleForm(request.POST, instance=bottle)
        if form.is_valid():
            bottle = form.save(commit=False)
            bottle.user = request.user

            dist_name = form.cleaned_data.get("distillery_name", "").strip()
            dist_instance = None

            if dist_name:
                dist_instance = Distillery.objects.filter(
                    name__iexact=dist_name
                ).first()
                if not dist_instance:
                    dist_instance = Distillery.objects.create(
                        name=dist_name,
                        added_by=request.user,
                        is_verified=False,
                    )

            bottle.distillery = dist_instance
            bottle.save()

            messages.success(request, "Bottle updated.")
            return redirect("inventory")
    else:
        initial = {}
        if bottle.distillery:
            initial["distillery_name"] = bottle.distillery.name
        form = BottleForm(instance=bottle, initial=initial)

    return render(request, "accounts/edit_bottle.html", {
        "form": form,
        "bottle": bottle,
    })


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



@login_required
def add_distillery_view(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()

        if not name:
            messages.error(request, "Distillery name is required.")
            return redirect("inventory")

        dist, created = Distillery.objects.get_or_create(
            name__iexact=name,
            defaults={
                "name": name,
                "country": request.POST.get("country", ""),
                "region": request.POST.get("region", ""),
                "added_by": request.user,
                "is_verified": False,
            }
        )

        messages.success(
            request,
            "Distillery submitted for approval. You can still use it immediately."
        )

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
        .filter(is_verified=True, name__icontains=q)
        .order_by("name")[:10]
    )

    return JsonResponse(
        [{"id": d.id, "name": d.name} for d in results],
        safe=False
    )

def add_distillery(request):
    if request.method == "POST":
        name = request.POST.get("name").strip()
        country = request.POST.get("country")
        region = request.POST.get("region")

        climate = suggest_climate(country, region)

        Distillery.objects.create(
            name=name,
            country=country,
            region=region,
            climate=climate,
            added_by=request.user
        )

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
            distillery.country = request.POST.get("country")
            distillery.region = request.POST.get("region")
            distillery.climate = request.POST.get("climate")
            distillery.is_verified = True
            distillery.save()
            DistilleryAuditLog.objects.create(
                distillery=distillery,
                action="approved",
                performed_by=request.user,
                notes = "Approved via review screen"
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
        return redirect("distillery_review_list")

    return render(request, "accounts/review_detail.html", {
        "distillery": distillery,
        "canonical_choices": canonical_choices,
        "suggested_climate": suggested_climate,
        "CLIMATE_CHOICES": CLIMATE_CHOICES,
        "audit_logs": distillery.audit_logs.all(),
    })


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
            log.distillery.name,
            log.get_action_display(),
            log.performed_by.username if log.performed_by else "System",
            log.notes,
            log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        ])

    return response

@login_required
def submit_bottle_review(request, bottle_id):
    bottle = get_object_or_404(Bottle, pk=bottle_id)

    review, created = BottleReview.objects.get_or_create(
        bottle=bottle,
        reviewer=request.user
    )

    if request.method == "POST":
        form = BottleReviewForm(request.POST, instance=review)
        if form.is_valid():
            form.save()
            messages.success(request, "Review saved!")
            return redirect("inventory")
    else:
        form = BottleReviewForm(instance=review)

    return render(request, "accounts/reviews/review_modal.html", {
        "form": form,
        "bottle": bottle,
        "review": review,
    })

@login_required
def add_review(request, pk):
    bottle = get_object_or_404(Bottle, pk=pk)

    if request.method == "POST":
        BottleReview.objects.create(
            bottle=bottle,
            user=request.user,
            nose=request.POST["nose"],
            taste=request.POST["taste"],
            finish=request.POST["finish"],
            value=request.POST["value"],
            notes=request.POST.get("notes", ""),
        )

    return redirect("inventory")