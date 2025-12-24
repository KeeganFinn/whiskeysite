from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from django.conf import settings

WHISKEY_TYPES = [
    ("bourbon", "Bourbon"),
    ("rye", "Rye"),
    ("single_malt", "Single Malt"),
    ("blended", "Blended Whiskey"),
    ("scotch_single_malt", "Scotch – Single Malt"),
    ("scotch_blended", "Scotch – Blended"),
    ("irish", "Irish"),
    ("japanese", "Japanese"),
    ("canadian", "Canadian"),
    ("corn", "Corn"),
    ("wheat", "Wheat Whiskey"),
    ("american_single_malt", "American Single Malt"),
    ("other", "Other"),
]

def user_profile_upload_path(instance, filename):
    return f"profile_pics/user_{instance.user.id}/{filename}"

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    avatar = models.ImageField(upload_to=user_profile_upload_path, default="default/avatar.png")
    bio = models.TextField(blank=True, null=True)
    share_stats = models.BooleanField(default=False)

    def __str__(self):
        return self.user.username


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        profile = UserProfile.objects.create(user=instance)
        profile.avatar = "default/avatar.png"   # ensure default always applies
        profile.save()

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.userprofile.save()

class UsernameHistory(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    old_username = models.CharField(max_length=150)
    changed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.old_username} -> {self.user.username}"

class Post(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="posts")
    content = models.TextField(max_length=5000)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.user.username}: {self.content[:30]}"

class Follow(models.Model):
    follower = models.ForeignKey(User, on_delete=models.CASCADE, related_name="following")
    following = models.ForeignKey(User, on_delete=models.CASCADE, related_name="followers")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("follower", "following")

    def __str__(self):
        return f"{self.follower} → {self.following}"

from django.db import models
from django.contrib.auth.models import User


class Distillery(models.Model):
    name = models.CharField(max_length=255, unique=True)

    country = models.CharField(max_length=100, blank=True, null=True)
    region = models.CharField(max_length=150, blank=True, null=True)

    # climate code (from CLIMATE_CHOICES)
    climate = models.CharField(max_length=50, blank=True, null=True)

    # approval / review workflow
    is_verified = models.BooleanField(default=False)
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicates",
        help_text="If this distillery is a duplicate, point to the canonical distillery."
    )

    added_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    created_at = models.DateTimeField(auto_now_add=True)

    # -----------------------
    # DERIVED / HELPERS
    # -----------------------

    @property
    def is_pending(self):
        """
        True if awaiting review (not verified and not marked as duplicate).
        """
        return not self.is_verified and self.duplicate_of is None

    def canonical(self):
        """
        Always return the canonical distillery.
        Safe to call everywhere (views, templates, admin).
        """
        return self.duplicate_of or self

    def get_climate_display_label(self):
        from .climate_lookup import CLIMATE_CHOICES
        mapping = dict(CLIMATE_CHOICES)
        return mapping.get(self.climate, self.climate or "")

    # -----------------------
    # SAFETY
    # -----------------------

    def clean(self):
        """
        Prevent invalid states:
        - A distillery cannot duplicate itself
        """
        if self.duplicate_of and self.duplicate_of_id == self.id:
            raise ValueError("A distillery cannot be a duplicate of itself.")

    class Meta:
        permissions = [
            ("can_review_distillery", "Can review and approve distilleries"),
        ]

    def __str__(self):
        return self.name


class Bottle(models.Model):
    STATUS_CHOICES = [
        ("unopened", "Unopened"),
        ("opened", "Opened"),
        ("finished", "Finished"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bottles")

    name = models.CharField(max_length=255)

    distillery = models.ForeignKey(
        Distillery,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bottles"
    )

    whiskey_type = models.CharField(max_length=50, choices=WHISKEY_TYPES)
    age = models.IntegerField(blank=True, null=True)
    proof = models.FloatField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_store_pick = models.BooleanField(default=False)
    store_name = models.CharField(max_length=255, blank=True, null=True)
    pick_details = models.TextField(blank=True, null=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="unopened"
    )

    canonical_bottle = models.ForeignKey(
        "CanonicalBottle",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inventory_bottles",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.user.username})"

class BottleReview(models.Model):
    bottle = models.ForeignKey(
        "CanonicalBottle",
        on_delete=models.CASCADE,
        related_name="reviews"
    )

    reviewer = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="bottle_reviews"
    )

    # 1–10 scores
    nose = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(10)]
    )
    taste = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(10)]
    )
    finish = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(10)]
    )
    value = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(10)]
    )

    final_score = models.FloatField(editable=False)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.final_score = (
            self.nose * 2.5 +
            self.taste * 4.0 +
            self.finish * 2.0 +
            self.value * 1.5
        )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.bottle} – {self.final_score:.1f}"

class DistilleryAuditLog(models.Model):
    ACTION_CHOICES = [
        ("created", "Created"),
        ("approved", "Approved"),
        ("duplicate", "Marked Duplicate"),
        ("updated", "Updated"),
    ]

    distillery = models.ForeignKey(
        Distillery,
        on_delete=models.CASCADE,
        related_name="audit_logs"
    )

    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    performed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )
    notes = models.TextField(
        blank=True,
        max_length=1000)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["distillery", "created_at"]),
        ]

    def __str__(self):
        return f"{self.distillery} - {self.action}"

class CanonicalBottle(models.Model):
    name = models.CharField(max_length=255)

    distillery = models.ForeignKey(
        Distillery,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="canonical_bottles",
    )

    whiskey_type = models.CharField(
        max_length=50,
        choices=WHISKEY_TYPES,
    )

    age = models.IntegerField(null=True, blank=True)
    proof = models.FloatField(null=True, blank=True)

    # Store Pick
    is_store_pick = models.BooleanField(default=False)

    store_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Only set if this is a store pick",
    )

    # Who created this canonical bottle
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_canonical_bottles",
    )

    # Future-proofing / deduping
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicates",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["whiskey_type"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "name",
                    "distillery",
                    "whiskey_type",
                    "is_store_pick",
                    "store_name",
                ],
                name="unique_canonical_bottle_identity",
            )
        ]

    def __str__(self):
        return self.name
