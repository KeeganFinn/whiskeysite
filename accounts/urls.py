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
    path("post/create/", views.create_post_view, name="create_post"),
    path("follow/<int:user_id>/", views.follow_user, name="follow_user"),
    path("unfollow/<int:user_id>/", views.unfollow_user, name="unfollow_user"),
    path("discover/", views.discover_view, name="discover"),

    # Inventory
    path("inventory/", views.inventory_view, name="inventory"),
    path("inventory/add/", views.add_bottle_view, name="add_bottle"),
    path("inventory/edit/<int:bottle_id>/", views.edit_bottle_view, name="edit_bottle"),
    path("inventory/delete/<int:bottle_id>/", views.delete_bottle_view, name="delete_bottle"),
    path("distilleries/autocomplete/", views.distillery_autocomplete, name="distillery_autocomplete"),
    path("distilleries/add/", views.add_distillery_view, name="add_distillery"),
    path("inventory/", views.inventory_view, name="inventory"),
    path("inventory/export/", views.inventory_export_csv, name="inventory_export_csv"),
    path("inventory/export/", views.inventory_export, name="inventory_export"),

    # Distillery help
    path("distilleries/infer-climate/", views.infer_climate_view, name="infer_climate"),
    path("distilleries/autocomplete/", views.distillery_autocomplete, name="distillery_autocomplete"),
    path("distilleries/add/", views.add_distillery, name="add_distillery"),
    path("distilleries/review/",views.distillery_review_list,name="distillery_review_list"),
    path("distilleries/review/<int:pk>/",views.distillery_review_detail,name="distillery_review_detail"),
    path("distilleries/infer-climate/",views.infer_climate_view,name="infer_climate"),
    path("distilleries/audit/export/",views.export_distillery_audit_csv,name="export_distillery_audit_csv"),
    path("reviews/bottle/<int:bottle_id>/",views.submit_bottle_review,name="submit_bottle_review"),
    path("bottles/<int:pk>/review/", views.add_review, name="add_review"),

]
