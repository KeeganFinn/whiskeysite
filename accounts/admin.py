from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from .models import Distillery


@admin.register(Distillery)
class DistilleryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "country",
        "region",
        "climate",
        "is_verified",
        "is_pending",
        "duplicate_of",
        "added_by",
        "created_at",
    )

    list_filter = (
        "is_verified",
        "country",
        "region",
    )

    search_fields = (
        "name",
        "country",
        "region",
    )

    readonly_fields = (
        "added_by",
        "created_at",
    )

    ordering = ("name",)

    actions = [
        "approve_distilleries",
        "mark_as_duplicate",
    ]

    # ---------------------------
    # PERMISSION GATE
    # ---------------------------
    def _check_review_permission(self, request):
        if not request.user.has_perm("accounts.can_review_distillery"):
            raise PermissionDenied("You do not have permission to review distilleries.")

    # ---------------------------
    # ACTIONS
    # ---------------------------
    def approve_distilleries(self, request, queryset):
        self._check_review_permission(request)

        updated = queryset.update(is_verified=True, duplicate_of=None)
        messages.success(request, f"{updated} distilleries approved.")

    approve_distilleries.short_description = "Approve selected distilleries"

    def mark_as_duplicate(self, request, queryset):
        self._check_review_permission(request)

        if queryset.count() < 2:
            messages.warning(request, "Select at least two distilleries to mark duplicates.")
            return

        canonical = queryset.filter(is_verified=True).first()

        if not canonical:
            messages.error(
                request,
                "At least one selected distillery must be VERIFIED to act as the canonical entry."
            )
            return

        for d in queryset.exclude(id=canonical.id):
            d.duplicate_of = canonical
            d.is_verified = False
            d.save()

        messages.success(
            request,
            f"Marked {queryset.count() - 1} distilleries as duplicates of '{canonical.name}'."
        )

    mark_as_duplicate.short_description = "Mark selected as duplicates of verified distillery"
