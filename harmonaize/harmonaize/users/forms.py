from allauth.account.forms import SignupForm
from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from django import forms
from django.contrib.auth import forms as admin_forms
from django.forms import EmailField
from django.utils.translation import gettext_lazy as _

from .models import User


class UserAdminChangeForm(admin_forms.UserChangeForm):
    class Meta(admin_forms.UserChangeForm.Meta):  # type: ignore[name-defined]
        model = User
        field_classes = {"email": EmailField}


class UserAdminCreationForm(admin_forms.UserCreationForm):
    """
    Form for User Creation in the Admin Area.
    To change user signup, see UserSignupForm and UserSocialSignupForm.
    """

    class Meta(admin_forms.UserCreationForm.Meta):  # type: ignore[name-defined]
        model = User
        fields = ("email",)
        field_classes = {"email": EmailField}
        error_messages = {
            "email": {"unique": _("This email has already been taken.")},
        }


class UserSignupForm(SignupForm):
    """
    Form that will be rendered on a user sign up section/screen.
    Default fields will be added automatically.
    Check UserSocialSignupForm for accounts created from social.
    """

    name = forms.CharField(
        max_length=255,
        label=_("Full Name"),
        widget=forms.TextInput(attrs={"placeholder": _("Your full name")}),
    )
    organization = forms.CharField(
        max_length=255,
        required=False,
        label=_("Organization"),
        widget=forms.TextInput(attrs={"placeholder": _("Your organization or institution (optional)")}),
    )

    def save(self, request):
        user = super().save(request)
        user.name = self.cleaned_data.get("name", "")
        user.organization = self.cleaned_data.get("organization", "")
        user.save()
        return user


class UserSocialSignupForm(SocialSignupForm):
    """
    Renders the form when user has signed up using social accounts.
    Default fields will be added automatically.
    See UserSignupForm otherwise.
    """

    name = forms.CharField(
        max_length=255,
        label=_("Full Name"),
        widget=forms.TextInput(attrs={"placeholder": _("Your full name")}),
    )
    organization = forms.CharField(
        max_length=255,
        required=False,
        label=_("Organization"),
        widget=forms.TextInput(attrs={"placeholder": _("Your organization or institution (optional)")}),
    )

    def save(self, request):
        user = super().save(request)
        user.name = self.cleaned_data.get("name", "")
        user.organization = self.cleaned_data.get("organization", "")
        user.save()
        return user
