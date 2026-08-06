from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import palate
from .models import (
    BottleReview,
    CanonicalBottle,
    Event,
    EventParticipant,
)


def make_event(owner, name, start, end, visibility="public"):
    return Event.objects.create(
        owner=owner,
        name=name,
        start_time=start,
        end_time=end,
        visibility=visibility,
    )


class EventsListTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = User.objects.create_user("viewer", password="pw")
        self.other = User.objects.create_user("host", password="pw")
        self.client.force_login(self.user)

    def upcoming_window(self, offset_days=1):
        start = self.now + timedelta(days=offset_days)
        return start, start + timedelta(hours=2)

    def test_added_events_appear_before_other_events(self):
        """An event the viewer was added to floats above one they weren't."""
        # Two public upcoming events owned by someone else.
        s1, e1 = self.upcoming_window(2)  # later start
        added = make_event(self.other, "Added Tasting", s1, e1)
        EventParticipant.objects.create(event=added, user=self.user)

        s2, e2 = self.upcoming_window(1)  # earlier start, but not added
        make_event(self.other, "Open Tasting", s2, e2)

        resp = self.client.get(reverse("events_list"))
        upcoming = list(resp.context["upcoming_events"])

        self.assertEqual(upcoming[0].name, "Added Tasting")
        self.assertTrue(upcoming[0].is_participant)
        self.assertFalse(upcoming[1].is_participant)

    def test_page_caps_at_25_events(self):
        """No more than 25 events render across all buckets."""
        for i in range(30):
            s, e = self.upcoming_window(i + 1)
            make_event(self.other, f"Event {i}", s, e)

        resp = self.client.get(reverse("events_list"))
        total = (
            len(resp.context["active_events"])
            + len(resp.context["upcoming_events"])
            + len(resp.context["past_events"])
        )
        self.assertEqual(total, 25)

    def test_added_events_kept_within_cap(self):
        """Added events are ordered first, so they survive the 25 cap."""
        # 30 non-added upcoming events.
        for i in range(30):
            s, e = self.upcoming_window(i + 5)
            make_event(self.other, f"Filler {i}", s, e)

        # One added event with a late start (would sort last by time alone).
        s, e = self.upcoming_window(100)
        added = make_event(self.other, "VIP Tasting", s, e)
        EventParticipant.objects.create(event=added, user=self.user)

        resp = self.client.get(reverse("events_list"))
        upcoming = list(resp.context["upcoming_events"])

        self.assertEqual(upcoming[0].name, "VIP Tasting")
        self.assertIn("VIP Tasting", [ev.name for ev in upcoming])

    def test_only_visible_events_shown(self):
        """Friends-only events the viewer isn't part of stay hidden."""
        s, e = self.upcoming_window(1)
        make_event(self.other, "Private Tasting", s, e, visibility="friends")

        resp = self.client.get(reverse("events_list"))
        names = [ev.name for ev in resp.context["upcoming_events"]]
        self.assertNotIn("Private Tasting", names)


class SiteStatsTests(TestCase):
    def setUp(self):
        from .models import Bottle
        self.Bottle = Bottle
        self.owner = User.objects.create_user("owner", password="pw")

    def add_bottle(self, status, price):
        return self.Bottle.objects.create(
            user=self.owner, name="B", whiskey_type="bourbon",
            status=status, price=price,
        )

    def test_core_totals(self):
        from .stats import site_stats
        self.add_bottle("unopened", 30)
        self.add_bottle("opened", 50)
        self.add_bottle("finished", 20)

        s = site_stats()
        self.assertEqual(s["total_bottles"], 3)
        self.assertEqual(s["total_value"], 100.0)
        self.assertEqual(s["unopened"], 1)
        self.assertEqual(s["opened"], 1)
        self.assertEqual(s["finished"], 1)

    def test_consumed_counts_finished_full_and_opened_half(self):
        from .stats import site_stats
        self.add_bottle("finished", 20)   # 0.75 L
        self.add_bottle("opened", 20)     # 0.375 L
        self.add_bottle("unopened", 20)   # 0 L
        s = site_stats()
        self.assertAlmostEqual(s["consumed_l"], 0.75 + 0.375)

    def test_fund_projection_compounds(self):
        from .stats import site_stats, SP500_ANNUAL_RETURN, FUND_YEARS
        self.add_bottle("unopened", 100)
        s = site_stats()
        expected = 100.0 * (1 + SP500_ANNUAL_RETURN) ** FUND_YEARS
        self.assertAlmostEqual(s["fund_value"], expected)


class FeedViewTests(TestCase):
    def setUp(self):
        self.me = User.objects.create_user("me", password="pw")
        self.friend = User.objects.create_user("friend", password="pw")
        self.stranger = User.objects.create_user("stranger", password="pw")
        from .models import Follow
        Follow.objects.create(follower=self.me, following=self.friend)
        self.client.force_login(self.me)
        self.bottle = CanonicalBottle.objects.create(
            name="Test Pour", whiskey_type="bourbon", proof=90
        )

    def review(self, user):
        BottleReview.objects.create(
            bottle=self.bottle, reviewer=user, nose=8, taste=8, finish=8, value=8,
        )

    def test_friend_and_stranger_reviews_split(self):
        self.review(self.friend)
        self.review(self.stranger)

        resp = self.client.get(reverse("feed"))
        friend_ids = [r.reviewer_id for r in resp.context["friend_reviews"]]
        stranger_ids = [r.reviewer_id for r in resp.context["stranger_reviews"]]

        self.assertIn(self.friend.id, friend_ids)
        self.assertNotIn(self.stranger.id, friend_ids)
        self.assertIn(self.stranger.id, stranger_ids)
        self.assertNotIn(self.friend.id, stranger_ids)

    def test_own_reviews_excluded_from_strangers(self):
        self.review(self.me)
        resp = self.client.get(reverse("feed"))
        stranger_ids = [r.reviewer_id for r in resp.context["stranger_reviews"]]
        self.assertNotIn(self.me.id, stranger_ids)

    def add_bottle(self, owner, price, status="unopened"):
        from .models import Bottle
        return Bottle.objects.create(
            user=owner, name="B", whiskey_type="bourbon", status=status, price=price,
        )

    def test_stats_scope_to_friend(self):
        # Friend owns 2 bottles; a stranger owns 1 that must not be counted.
        self.add_bottle(self.friend, 40)
        self.add_bottle(self.friend, 60)
        self.add_bottle(self.stranger, 999)

        resp = self.client.get(reverse("feed"), {"friend": "friend"})
        self.assertEqual(resp.context["scoped_user"], self.friend)
        self.assertEqual(resp.context["stats"]["total_bottles"], 2)
        self.assertEqual(resp.context["stats"]["total_value"], 100.0)

    def test_cannot_scope_to_non_friend(self):
        self.add_bottle(self.stranger, 999)
        resp = self.client.get(reverse("feed"), {"friend": "stranger"})
        # Not followed -> no scoping; stats stay site-wide.
        self.assertIsNone(resp.context["scoped_user"])

    def test_friend_search_only_returns_followed(self):
        resp = self.client.get(reverse("feed_friend_search"), {"q": "fr"})
        names = [r["username"] for r in resp.json()]
        self.assertIn("friend", names)
        resp2 = self.client.get(reverse("feed_friend_search"), {"q": "stranger"})
        self.assertEqual(resp2.json(), [])


class DistilleryAutofillTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Permission
        from .models import Distillery
        self.Distillery = Distillery
        self.reviewer = User.objects.create_user("reviewer", password="pw")
        self.reviewer.user_permissions.add(
            Permission.objects.get(codename="can_review_distillery")
        )
        self.distillery = Distillery.objects.create(name="Ardbeg")

    def test_autofill_returns_lookup_json(self):
        from unittest.mock import patch
        self.client.force_login(self.reviewer)
        fake = {
            "found": True, "country": "Scotland", "region": "Islay",
            "climate": "cool_humid", "summary": "An Islay distillery.",
            "source_url": "https://en.wikipedia.org/wiki/Ardbeg",
        }
        with patch("accounts.views.lookup_distillery_info", return_value=fake) as m:
            resp = self.client.get(
                reverse("distillery_autofill", args=[self.distillery.pk])
            )
        m.assert_called_once_with("Ardbeg")
        self.assertEqual(resp.json()["region"], "Islay")

    def test_reject_deletes_distillery_bottles_and_reviews(self):
        self.client.force_login(self.reviewer)
        cb = CanonicalBottle.objects.create(
            name="Junk Pour", whiskey_type="bourbon", distillery=self.distillery,
        )
        BottleReview.objects.create(
            bottle=cb, reviewer=self.reviewer, nose=5, taste=5, finish=5, value=5,
        )
        resp = self.client.post(
            reverse("distillery_review_detail", args=[self.distillery.pk]),
            {"action": "reject"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(self.Distillery.objects.filter(pk=self.distillery.pk).exists())
        self.assertFalse(CanonicalBottle.objects.filter(pk=cb.pk).exists())
        self.assertEqual(BottleReview.objects.count(), 0)

    def test_reject_notifies_affected_reviewers(self):
        from .models import Notification
        self.client.force_login(self.reviewer)
        author = User.objects.create_user("author", password="pw")
        cb = CanonicalBottle.objects.create(
            name="Junk Pour", whiskey_type="bourbon", distillery=self.distillery,
        )
        BottleReview.objects.create(
            bottle=cb, reviewer=author, nose=5, taste=5, finish=5, value=5,
        )
        self.client.post(
            reverse("distillery_review_detail", args=[self.distillery.pk]),
            {"action": "reject"},
        )
        notes = Notification.objects.filter(user=author)
        self.assertEqual(notes.count(), 1)
        self.assertIn("removed", notes.first().message.lower())

    def test_approve_can_rename_distillery(self):
        self.client.force_login(self.reviewer)
        resp = self.client.post(
            reverse("distillery_review_detail", args=[self.distillery.pk]),
            {"action": "approve", "name": "Ardbeg Distillery",
             "country": "Scotland", "region": "Islay", "climate": "cool_humid"},
        )
        self.assertEqual(resp.status_code, 302)
        self.distillery.refresh_from_db()
        self.assertEqual(self.distillery.name, "Ardbeg Distillery")
        self.assertTrue(self.distillery.is_verified)

    def test_approve_rename_conflict_is_handled(self):
        self.client.force_login(self.reviewer)
        self.Distillery.objects.create(name="Taken Name", is_verified=True)
        resp = self.client.post(
            reverse("distillery_review_detail", args=[self.distillery.pk]),
            {"action": "approve", "name": "Taken Name", "country": "Scotland"},
        )
        # Redirects back without crashing; original distillery stays unverified.
        self.assertEqual(resp.status_code, 302)
        self.distillery.refresh_from_db()
        self.assertEqual(self.distillery.name, "Ardbeg")
        self.assertFalse(self.distillery.is_verified)

    def test_review_page_lists_related_bottles(self):
        self.client.force_login(self.reviewer)
        CanonicalBottle.objects.create(
            name="Context Pour", whiskey_type="bourbon", distillery=self.distillery,
        )
        resp = self.client.get(
            reverse("distillery_review_detail", args=[self.distillery.pk])
        )
        self.assertContains(resp, "Context Pour")

    def test_autofill_requires_permission(self):
        nobody = User.objects.create_user("nobody", password="pw")
        self.client.force_login(nobody)
        resp = self.client.get(
            reverse("distillery_autofill", args=[self.distillery.pk])
        )
        # Permission denied -> redirect to login, not a 200 JSON response.
        self.assertNotEqual(resp.status_code, 200)


class DistilleryLookupParseTests(TestCase):
    def test_region_infers_country(self):
        from unittest.mock import patch
        from . import distillery_lookup
        summary = {
            "extract": "Ardbeg is a distillery on Islay.",
            "content_urls": {"desktop": {"page": "http://x"}},
        }
        with patch.object(distillery_lookup, "_fetch_summary", return_value=summary):
            info = distillery_lookup.lookup_distillery_info("Ardbeg")
        self.assertEqual(info["region"], "Islay")
        self.assertEqual(info["country"], "Scotland")
        self.assertEqual(info["climate"], "cool_humid")

    def test_fuzzy_match_corrects_name(self):
        from unittest.mock import patch
        from . import distillery_lookup
        ardbeg = {"title": "Ardbeg distillery",
                  "extract": "Ardbeg is a distillery on Islay, Scotland.",
                  "content_urls": {"desktop": {"page": "http://x"}}}

        with patch.object(distillery_lookup, "_fetch_summary",
                          side_effect=lambda t: ardbeg if "ardbeg" in t.lower() else None), \
             patch.object(distillery_lookup, "_search_raw",
                          return_value=(["Ardbeg distillery"], "")):
            info = distillery_lookup.lookup_distillery_info("Ardbg")

        self.assertTrue(info["found"])
        self.assertEqual(info["name"], "Ardbeg")
        self.assertEqual(info["region"], "Islay")

    def test_irrelevant_search_result_is_rejected(self):
        from unittest.mock import patch
        from . import distillery_lookup
        # Junk name; search returns a real-but-unrelated article.
        jack = "Jack Daniel's is a distillery in Tennessee."
        with patch.object(distillery_lookup, "_fetch_summary", return_value=None), \
             patch.object(distillery_lookup, "_search_titles", return_value=["Jack Daniel's"]), \
             patch.object(distillery_lookup, "_fetch_extract", return_value=jack):
            info = distillery_lookup.lookup_distillery_info("testDistillery")
        self.assertFalse(info["found"])
        self.assertIsNone(info["suggestion"])

    def test_not_found_returns_empty(self):
        from unittest.mock import patch
        from . import distillery_lookup
        with patch.object(distillery_lookup, "_fetch_summary", return_value=None), \
             patch.object(distillery_lookup, "_search_raw", return_value=([], "")), \
             patch.object(distillery_lookup, "_search_titles", return_value=[]), \
             patch.object(distillery_lookup, "_gemini_lookup", return_value=None):
            info = distillery_lookup.lookup_distillery_info("Nope")
        self.assertFalse(info["found"])
        self.assertIsNone(info["suggestion"])

    def test_gemini_fallback_used_when_wikipedia_empty(self):
        from unittest.mock import patch
        from . import distillery_lookup
        gemini_result = {
            "found": True, "name": "Midleton", "country": "Ireland",
            "region": "", "climate": "cool_humid", "summary": "",
            "source_url": "", "source": "gemini", "suggestion": None,
        }
        with patch.object(distillery_lookup, "_fetch_summary", return_value=None), \
             patch.object(distillery_lookup, "_search_raw", return_value=([], "")), \
             patch.object(distillery_lookup, "_search_titles", return_value=[]), \
             patch.object(distillery_lookup, "_gemini_lookup", return_value=gemini_result):
            info = distillery_lookup.lookup_distillery_info("Obscure Brand")
        self.assertTrue(info["found"])
        self.assertEqual(info["source"], "gemini")
        self.assertEqual(info["name"], "Midleton")

    def test_gemini_disabled_without_key(self):
        from django.test import override_settings
        from . import distillery_lookup
        with override_settings(GEMINI_API_KEY=""):
            self.assertIsNone(distillery_lookup._gemini_lookup("Anything"))

    def test_brand_suggests_real_distillery(self):
        from unittest.mock import patch
        from . import distillery_lookup

        brand = {"extract": "Redbreast is produced at the Midleton Distillery.",
                 "content_urls": {"desktop": {"page": "http://brand"}}}
        midleton = {"extract": "Midleton is a town in Ireland.",
                    "content_urls": {"desktop": {"page": "http://midleton"}}}

        def fake_summary(title):
            t = title.lower()
            if "redbreast" in t and "distillery" not in t:
                return brand
            if "midleton" in t:
                return midleton
            return None

        with patch.object(distillery_lookup, "_fetch_summary", side_effect=fake_summary), \
             patch.object(distillery_lookup, "_search_titles", return_value=[]):
            info = distillery_lookup.lookup_distillery_info("Redbreast")

        self.assertFalse(info["found"])
        self.assertIsNotNone(info["suggestion"])
        self.assertEqual(info["suggestion"]["name"], "Midleton")
        self.assertEqual(info["suggestion"]["country"], "Ireland")


class StyleMathTests(TestCase):
    def test_identical_style_mix_is_100_percent(self):
        v = {"bourbon": 5, "rye": 2}
        self.assertEqual(palate.match_percent(palate.cosine(v, v)), 100)

    def test_disjoint_styles_are_zero_percent(self):
        a = {"bourbon": 4}
        b = {"scotch_single_malt": 4}
        self.assertEqual(palate.match_percent(palate.cosine(a, b)), 0)

    def test_proportions_matter_not_volume(self):
        # Same ratio, different totals -> still a perfect match.
        a = {"bourbon": 2, "rye": 1}
        b = {"bourbon": 8, "rye": 4}
        self.assertEqual(palate.match_percent(palate.cosine(a, b)), 100)

    def test_empty_vector_is_zero(self):
        self.assertEqual(palate.cosine({}, {"bourbon": 3}), 0.0)


class DiscoverViewTests(TestCase):
    def setUp(self):
        self.me = User.objects.create_user("me", password="pw")
        self.client.force_login(self.me)

    def bottle(self, name, wtype):
        return CanonicalBottle.objects.create(name=name, whiskey_type=wtype, proof=90)

    def review(self, user, bottle):
        BottleReview.objects.create(
            bottle=bottle, reviewer=user, nose=7, taste=7, finish=7, value=7,
        )

    def test_search_filters_users(self):
        User.objects.create_user("whiskeyfan", password="pw")
        User.objects.create_user("rumlover", password="pw")

        resp = self.client.get(reverse("discover"), {"q": "whiskey"})
        names = [u.username for u in resp.context["users"]]
        self.assertIn("whiskeyfan", names)
        self.assertNotIn("rumlover", names)

    def test_style_label_is_dominant_type(self):
        other = User.objects.create_user("bourbonfan", password="pw")
        b1 = self.bottle("Pour A", "bourbon")
        b2 = self.bottle("Pour B", "bourbon")
        b3 = self.bottle("Pour C", "rye")
        self.review(other, b1)
        self.review(other, b2)
        self.review(other, b3)

        resp = self.client.get(reverse("discover"))
        ctx = next(u for u in resp.context["users"] if u.username == "bourbonfan")
        self.assertEqual(ctx.style_label, "Bourbon")
        self.assertEqual(ctx.review_count, 3)
        # Breakdown is sorted, bourbon first.
        self.assertEqual(ctx.type_breakdown[0], {"label": "Bourbon", "count": 2})

    def test_similar_style_ranked_first(self):
        twin = User.objects.create_user("twin", password="pw")
        opposite = User.objects.create_user("opposite", password="pw")
        bourbon = self.bottle("Bourbon Pour", "bourbon")
        scotch = self.bottle("Scotch Pour", "scotch_single_malt")

        self.review(self.me, bourbon)     # I'm a bourbon drinker
        self.review(twin, bourbon)        # twin too
        self.review(opposite, scotch)     # opposite drinks scotch

        resp = self.client.get(reverse("discover"), {"match": "similar"})
        users = resp.context["users"]
        self.assertEqual(users[0].username, "twin")
        self.assertEqual(users[0].style_match, 100)

    def test_follow_returns_to_discover_with_next(self):
        target = User.objects.create_user("followme", password="pw")
        resp = self.client.get(
            reverse("follow_user", args=[target.id]),
            {"next": "/discover/?match=similar"},
        )
        self.assertRedirects(resp, "/discover/?match=similar", fetch_redirect_response=False)

    def test_follow_without_next_goes_to_profile(self):
        target = User.objects.create_user("followme2", password="pw")
        resp = self.client.get(reverse("follow_user", args=[target.id]))
        self.assertRedirects(resp, "/profile/followme2/", fetch_redirect_response=False)

    def test_follow_rejects_offsite_next(self):
        target = User.objects.create_user("followme3", password="pw")
        resp = self.client.get(
            reverse("follow_user", args=[target.id]),
            {"next": "https://evil.example.com/"},
        )
        self.assertRedirects(resp, "/profile/followme3/", fetch_redirect_response=False)

    def test_profile_shows_back_to_discover_with_next(self):
        target = User.objects.create_user("profileuser", password="pw")
        resp = self.client.get(
            "/profile/profileuser/", {"next": "/discover/?match=similar"}
        )
        self.assertEqual(resp.context["back_url"], "/discover/?match=similar")

    def test_profile_includes_user_reviews(self):
        target = User.objects.create_user("reviewer", password="pw")
        bottle = CanonicalBottle.objects.create(
            name="History Pour", whiskey_type="bourbon", proof=90
        )
        BottleReview.objects.create(
            bottle=bottle, reviewer=target, nose=7, taste=8, finish=6, value=7,
        )
        # A review by someone else must not appear on this profile.
        other = User.objects.create_user("notme", password="pw")
        BottleReview.objects.create(
            bottle=bottle, reviewer=other, nose=1, taste=1, finish=1, value=1,
        )

        resp = self.client.get("/profile/reviewer/")
        reviews = resp.context["reviews"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].reviewer_id, target.id)
        self.assertContains(resp, "History Pour")

    def test_profile_rejects_offsite_next(self):
        User.objects.create_user("profileuser2", password="pw")
        resp = self.client.get(
            "/profile/profileuser2/", {"next": "https://evil.example.com/"}
        )
        self.assertIsNone(resp.context["back_url"])

    def test_different_style_ranked_first(self):
        twin = User.objects.create_user("twin", password="pw")
        opposite = User.objects.create_user("opposite", password="pw")
        bourbon = self.bottle("Bourbon Pour", "bourbon")
        scotch = self.bottle("Scotch Pour", "scotch_single_malt")

        self.review(self.me, bourbon)
        self.review(twin, bourbon)
        self.review(opposite, scotch)

        resp = self.client.get(reverse("discover"), {"match": "different"})
        users = resp.context["users"]
        self.assertEqual(users[0].username, "opposite")


# ---------------------------------------------------------------------------
# Security regression tests (added alongside the security review).
# ---------------------------------------------------------------------------

from django.core.cache import cache


class LoginRequiredTests(TestCase):
    """Account-modifying views must not be reachable while logged out."""

    def _assert_redirects_to_login(self, url_name):
        resp = self.client.get(reverse(url_name))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_settings_requires_login(self):
        self._assert_redirects_to_login("settings")

    def test_change_password_requires_login(self):
        self._assert_redirects_to_login("change_password")

    def test_change_email_requires_login(self):
        self._assert_redirects_to_login("change_email")

    def test_change_username_requires_login(self):
        self._assert_redirects_to_login("change_username")

    def test_profile_settings_requires_login(self):
        self._assert_redirects_to_login("profile_settings")


class EventVisibilityTests(TestCase):
    """Friends-only events must not be viewable by outsiders via direct URL."""

    def setUp(self):
        self.now = timezone.now()
        self.owner = User.objects.create_user("owner", password="pw")
        self.outsider = User.objects.create_user("outsider", password="pw")
        self.event = Event.objects.create(
            owner=self.owner,
            name="Secret Tasting",
            visibility="friends",
            start_time=self.now,
            end_time=self.now + timedelta(hours=2),
        )

    def test_friends_event_hidden_from_non_participant(self):
        self.client.force_login(self.outsider)
        resp = self.client.get(reverse("event_detail", args=[self.event.id]))
        self.assertEqual(resp.status_code, 404)

    def test_friends_event_visible_to_participant(self):
        EventParticipant.objects.create(event=self.event, user=self.outsider)
        self.client.force_login(self.outsider)
        resp = self.client.get(reverse("event_detail", args=[self.event.id]))
        self.assertEqual(resp.status_code, 200)

    def test_friends_event_visible_to_owner(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("event_detail", args=[self.event.id]))
        self.assertEqual(resp.status_code, 200)

    def test_public_event_visible_to_anyone(self):
        self.event.visibility = "public"
        self.event.save()
        self.client.force_login(self.outsider)
        resp = self.client.get(reverse("event_detail", args=[self.event.id]))
        self.assertEqual(resp.status_code, 200)


class RegistrationSpamTests(TestCase):
    """Honeypot + per-IP rate limiting on the signup form."""

    def setUp(self):
        cache.clear()  # rate-limit counters live in the cache

    def tearDown(self):
        cache.clear()

    def _payload(self, i, website=""):
        return {
            "email": f"user{i}@example.com",
            "username": f"newuser{i}",
            "password1": "S3cureP@ssw0rd",
            "password2": "S3cureP@ssw0rd",
            "website": website,
        }

    def test_honeypot_blocks_bot_signup(self):
        resp = self.client.post(
            reverse("register"), self._payload(1, website="http://spam.example")
        )
        self.assertEqual(resp.status_code, 200)  # re-renders form, no redirect
        self.assertFalse(User.objects.filter(username="newuser1").exists())

    def test_clean_signup_succeeds(self):
        resp = self.client.post(reverse("register"), self._payload(2))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(username="newuser2").exists())

    def test_registration_is_rate_limited(self):
        # 5 signups/hour/IP are allowed; the 6th must be turned away.
        for i in range(5):
            self.client.post(reverse("register"), self._payload(i))
        before = User.objects.count()

        self.client.post(reverse("register"), self._payload(99))
        self.assertEqual(User.objects.count(), before)
        self.assertFalse(User.objects.filter(username="newuser99").exists())


class ReviewInputValidationTests(TestCase):
    """Out-of-range / non-numeric scores are rejected, not stored or 500'd."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("reviewer", password="pw")
        self.client.force_login(self.user)
        self.bottle = CanonicalBottle.objects.create(
            name="Test Bottle", whiskey_type="bourbon", proof=90,
        )

    def test_non_numeric_score_rejected(self):
        resp = self.client.post(
            reverse("add_canonical_review", args=[self.bottle.id]),
            {"nose": "abc", "taste": "5", "finish": "5", "value": "5"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(BottleReview.objects.count(), 0)

    def test_out_of_range_score_rejected(self):
        resp = self.client.post(
            reverse("add_canonical_review", args=[self.bottle.id]),
            {"nose": "99", "taste": "5", "finish": "5", "value": "5"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(BottleReview.objects.count(), 0)

    def test_valid_scores_accepted(self):
        resp = self.client.post(
            reverse("add_canonical_review", args=[self.bottle.id]),
            {"nose": "8", "taste": "7", "finish": "6", "value": "9"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(BottleReview.objects.count(), 1)
