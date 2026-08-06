from django.urls import path
from . import views


urlpatterns = [
    # Home + Auth
    path("", views.home_view, name="home"),
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # Profiles
    path("profile/", views.profile_view, name="my_profile"),  # logged-in user's profile
    path("profile/<str:username>/", views.profile_view, name="profile"),  # other users

    # Followers
    path("profile/<str:username>/followers/", views.followers_list_view, name="followers_list"),
    path("profile/<str:username>/following/", views.following_list_view, name="following_list"),

    # Settings
    path("settings/", views.settings_view, name="settings"),
    path("settings/profile/", views.profile_settings_view, name="profile_settings"),
    path("settings/email/", views.change_email_view, name="change_email"),
    path("settings/password/", views.change_password_view, name="change_password"),
    path("settings/username/", views.change_username_view, name="change_username"),

    # Social features
    path("feed/", views.feed_view, name="feed"),
    path("feed/friend-search/", views.feed_friend_search, name="feed_friend_search"),
    path("follow/<int:user_id>/", views.follow_user, name="follow_user"),
    path("unfollow/<int:user_id>/", views.unfollow_user, name="unfollow_user"),
    path("discover/", views.discover_view, name="discover"),
    path("discover/autocomplete/", views.discover_autocomplete, name="discover_autocomplete"),

    # Inventory
    path("inventory/", views.inventory_view, name="inventory"),
    path("inventory/add/", views.add_bottle_view, name="add_bottle"),
    path("inventory/edit/<int:bottle_id>/", views.edit_bottle_view, name="edit_bottle"),
    path("inventory/delete/<int:bottle_id>/", views.delete_bottle_view, name="delete_bottle"),
    path("distilleries/autocomplete/", views.distillery_autocomplete, name="distillery_autocomplete"),
    path("distilleries/add/", views.add_distillery, name="add_distillery"),
    path("inventory/", views.inventory_view, name="inventory"),
    path("inventory/export/", views.inventory_export_csv, name="inventory_export_csv"),
    path("inventory/export/", views.inventory_export, name="inventory_export"),

    # Distillery help
    path("distilleries/infer-climate/", views.infer_climate_view, name="infer_climate"),
    path("distilleries/autocomplete/", views.distillery_autocomplete, name="distillery_autocomplete"),
    path("distilleries/suggest-climate/", views.suggest_climate_view, name="suggest_climate"),
    path("distilleries/add/", views.add_distillery, name="add_distillery"),
    path("distilleries/review/",views.distillery_review_list,name="distillery_review_list"),
    path("distilleries/review/<int:pk>/",views.distillery_review_detail,name="distillery_review_detail"),
    path("distilleries/review/<int:pk>/autofill/",views.distillery_autofill,name="distillery_autofill"),
    path("distilleries/infer-climate/",views.infer_climate_view,name="infer_climate"),
    path("distilleries/audit/export/",views.export_distillery_audit_csv,name="export_distillery_audit_csv"),
    path("bottles/<int:pk>/review/", views.add_review, name="add_review"),

    # Bottle Reviews
    path("canonical-bottles/<int:pk>/", views.canonical_bottle_detail, name="canonical_bottle_detail"),
    path("reviews/", views.review_search, name="review_search"),
    path("canonical-bottles/<int:pk>/review/",views.add_canonical_review,name="add_canonical_review"),
    path("inventory-bottles/<int:pk>/reviews/",views.inventory_to_canonical,name="inventory_to_canonical"),
    path("bottles/add-and-review/",views.canonical_add_and_review,name="canonical_add_and_review",),

    # Events
    path("events/", views.events_list, name="events_list"),
    path("events/<int:pk>/", views.event_detail, name="event_detail"),
    path("events/create/", views.event_form, name="event_create"),
    path("events/<int:pk>/edit/", views.event_form, name="event_edit"),
    path("events/<int:pk>/delete/", views.event_delete, name="event_delete"),
    path("events/<int:pk>/add-bottle/", views.event_add_bottle, name="event_add_bottle"),
    path("events/<int:pk>/participants/add/", views.event_add_participant, name="event_add_participant"),
    path("events/<int:pk>/participants/<int:user_id>/remove/",views.event_remove_participant,name="event_remove_participant",),
    path("events/<int:pk>/user-search/",views.event_user_search,name="event_user_search",),
    path("notifications/<int:pk>/",views.notification_redirect,name="notification_redirect",),
    path("events/<int:event_id>/bottles/<int:pk>/delete/",views.event_delete_bottle,name="event_delete_bottle"),
    path("events/<int:event_id>/bottles/<int:pk>/edit/",views.event_edit_bottle,name="event_edit_bottle"),
    path("events/<int:event_id>/bottles/<int:event_bottle_id>/review/",views.add_event_review,name="add_event_review"),

]
