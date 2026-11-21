import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main


class PublicBrandingTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, follow_redirects=False)

    def test_public_pages_need_no_session_or_database(self):
        pages = {
            "/about": "AI-assisted sales outreach",
            "/privacy": "gmail.readonly",
            "/terms": "case-study demonstration",
        }
        with patch.object(main, "supabase", new_callable=AsyncMock) as db:
            for route, expected in pages.items():
                with self.subTest(route=route):
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(expected, response.text)
                    self.assertIn('href="/privacy"', response.text)
                    self.assertIn('href="/terms"', response.text)
                    self.assertNotIn("set-cookie", response.headers)
            db.assert_not_awaited()

    def test_login_footer_and_about_link_to_public_pages(self):
        for route in ("/login", "/about"):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                for path, label in (
                    ("/about", "About"),
                    ("/privacy", "Privacy Policy"),
                    ("/terms", "Terms of Service"),
                ):
                    self.assertIn(f'<a href="{path}">{label}</a>', response.text)
                if route == "/login":
                    self.assertIn("<footer>", response.text)

    def test_search_console_tag_from_root_and_about(self):
        token = "example-public_tag"
        with patch.dict(os.environ, {"GOOGLE_SITE_VERIFICATION": token}):
            expected = f'<meta name="google-site-verification" content="{token}">'
            self.assertIn(expected, self.client.get("/about").text)
            response = self.client.get("/", follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.url.path, "/login")
            self.assertIn(expected, response.text.split("</head>", 1)[0])
            for route in ("/privacy", "/terms"):
                self.assertNotIn(token, self.client.get(route).text)
        for value in ("", '<script>alert(1)</script>'):
            with patch.dict(os.environ, {"GOOGLE_SITE_VERIFICATION": value}):
                for route in ("/about", "/"):
                    self.assertNotIn("google-site-verification", self.client.get(route, follow_redirects=True).text)

    def test_dashboard_stays_protected(self):
        with patch.object(main, "supabase", new_callable=AsyncMock) as db:
            home = self.client.get("/")
            self.assertEqual(home.status_code, 303)
            self.assertEqual(home.headers["location"], "/dashboard/overview")
            for section in main.SECTIONS:
                with self.subTest(section=section):
                    response = self.client.get(f"/dashboard/{section}")
                    self.assertEqual(response.status_code, 303)
                    self.assertEqual(response.headers["location"], "/login")
            db.assert_not_awaited()
