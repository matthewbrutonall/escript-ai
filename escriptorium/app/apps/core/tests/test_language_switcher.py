from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.translation import get_language_info


class LanguageSwitcherTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="lang",
            password="test",
            email="lang@example.com",
            legacy_mode=True,
        )
        self.client.force_login(self.user)

    def test_projects_page_keeps_the_language_menu_closed(self):
        response = self.client.get(reverse("projects-list"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("My Projects", html)
        self.assertIn('class="esai-langs-toggle"', html)
        self.assertIn('aria-haspopup="menu"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('class="esai-langs-list" role="menu" hidden', html)
        self.assertIn(">English<", html)
        for code, _name in settings.LANGUAGES:
            self.assertIn('value="%s"' % code, html)
            self.assertIn(get_language_info(code)["name_local"], html)
        self.assertEqual(html.count('aria-current="true"'), 1)
        self.assertIn('a[href$="/projects/create/"]', html)

    def test_selecting_a_language_keeps_every_language_available(self):
        response = self.client.post(
            reverse("set_language"),
            {"language": "ar", "next": reverse("projects-list")},
        )
        self.assertRedirects(response, reverse("projects-list"))
        self.assertEqual(self.client.cookies["django_language"].value, "ar")

        follow = self.client.get(reverse("projects-list"))
        html = follow.content.decode()
        self.assertIn('dir="rtl"', html)
        self.assertIn('lang="ar"', html)
        arabic_name = get_language_info("ar")["name_local"]
        self.assertIn(">%s<" % arabic_name, html)
        self.assertIn('value="ar"', html)
        self.assertIn('aria-current="true"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertEqual(html.count('class="esai-langs-toggle"'), 1)
        for code, _name in settings.LANGUAGES:
            self.assertIn('value="%s"' % code, html)
