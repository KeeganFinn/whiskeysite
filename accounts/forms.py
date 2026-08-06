import djforms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordChangeForm
from django import forms
from .models import UserProfile, BottleReview
from .models import Bottle
from .climate_lookup import CLIMATE_CHOICES, suggest_climate
from .models import Distillery
from django import forms as djforms

class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True, label="Email")

    # Honeypot: hidden from real users via CSS/aria in the template. Bots that
    # auto-fill every field will populate it, and we reject those submissions.
    website = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(attrs={
            "autocomplete": "off",
            "tabindex": "-1",
        }),
    )

    class Meta:
        model = User
        fields = ("email", "username", "password1", "password2")

    def clean_website(self):
        if self.cleaned_data.get("website"):
            # Don't reveal the honeypot; a generic error is enough.
            raise forms.ValidationError("Registration could not be completed.")
        return ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['email'].widget.attrs['autofocus'] = True

        self.fields['password1'].help_text = "Password must be at least 8 characters."
        self.fields['password2'].help_text = "Re-enter your password for confirmation."
        self.fields['username'].help_text = ""

        self.fields['username'].widget.attrs['maxlength'] = 20

    # Ensure email is saved to the user model
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class CustomPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Clean modern help text, matching account creation page
        self.fields['new_password1'].help_text = "Password must be at least 8 characters."
        self.fields['new_password2'].help_text = "Re-enter your password for confirmation."

        # Remove old password help text entirely
        self.fields['old_password'].help_text = ""

class EmailChangeForm(forms.Form):
    email = forms.EmailField(
        required=True,
        label="Email",
        widget=forms.EmailInput(attrs={"placeholder": "Enter new email"})
    )



class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("avatar", "bio", "share_stats")
        widgets = {
            "avatar": forms.FileInput(attrs={
                "class": "form-control",
                "accept": "image/*",
            }),
            "bio": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 4,
                "maxlength": "300",
                "id": "bio-input",
            }),
            "share_stats": forms.CheckboxInput(attrs={
                "class": "form-check-input",
                "id": "id_share_stats",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Just in case Django adds anything, kill help text
        self.fields["avatar"].help_text = None

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        # Only newly uploaded files carry a .size; existing paths won't.
        max_bytes = 5 * 1024 * 1024  # 5 MB
        if avatar and hasattr(avatar, "size") and avatar.size > max_bytes:
            raise forms.ValidationError("Image is too large (5 MB maximum).")
        return avatar

class ChangeUsernameForm(forms.Form):
    new_username = forms.CharField(
        max_length=20,
        label="New Username",
        widget=forms.TextInput(attrs={"class": "form-control"})
    )

    def clean_new_username(self):
        username = self.cleaned_data["new_username"]

        # Ensure username is unique
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("That username is already taken.")

        return username

class BottleForm(forms.ModelForm):
    # Free-text field that we control manually
    distillery_name = forms.CharField(
        label="Distillery",
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. Buffalo Trace, Ardbeg, Redbreast"
        })
    )

    class Meta:
        model = Bottle
        fields = (
            "name",
            "distillery_name",   # <— NOT the FK field
            "whiskey_type",
            "age",
            "proof",
            "price",
            "is_store_pick",
            "store_name",
            "pick_details",
            "status",
        )
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Bottle name"
            }),
            "whiskey_type": forms.Select(attrs={
                "class": "form-select"
            }),
            "age": forms.NumberInput(attrs={
                "class": "form-control",
                "placeholder": "Leave blank for NAS"
            }),
            "proof": forms.NumberInput(attrs={
                "class": "form-control",
                "min": 0,
                "step": "0.1",
                "placeholder": "e.g. 94"
            }),
            "price": forms.NumberInput(attrs={
                "class": "form-control",
                "min": 0,
                "step": "0.01",
                "placeholder": "e.g. 59.99"
            }),
            "is_store_pick": forms.CheckboxInput(attrs={
                "class": "form-check-input"
            }),
            "store_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Store name (if a pick)"
            }),
            "pick_details": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Details about the pick (optional)"
            }),
            "status": forms.Select(attrs={
                "class": "form-select"
            }),
        }

class EditBottleForm(BottleForm):
    pass

class DistilleryForm(forms.ModelForm):
    climate = forms.ChoiceField(
        choices=[("", "Select climate (optional)")] + CLIMATE_CHOICES,
        required=False,
        label="Climate",
    )

    class Meta:
        model = Distillery
        fields = ("name", "country", "region", "climate")

    def clean(self):
        cleaned = super().clean()

        country = cleaned.get("country")
        region = cleaned.get("region")
        climate = cleaned.get("climate")

        # If user didn't pick a climate, try to auto-suggest it
        if not climate and country and region:
            suggestion = suggest_climate(country, region)
            if suggestion:
                cleaned["climate"] = suggestion

        return cleaned


class BottleReviewForm(djforms.ModelForm):
    class Meta:
        model = BottleReview
        fields = ["nose", "taste", "finish", "value", "notes"]

        widgets = {
            "nose": djforms.NumberInput(attrs={"min": 1, "max": 10, "class": "form-control"}),
            "taste": djforms.NumberInput(attrs={"min": 1, "max": 10, "class": "form-control"}),
            "finish": djforms.NumberInput(attrs={"min": 1, "max": 10, "class": "form-control"}),
            "value": djforms.NumberInput(attrs={"min": 1, "max": 10, "class": "form-control"}),
            "notes": djforms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }