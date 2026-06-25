# -*- coding: utf-8 -*-
import unittest
import os
import sys
from dotenv import load_dotenv

# Ensure parent directory is in path if run directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from frappe_client import FrappeRestClient

class TestFrappeConnection(unittest.TestCase):
    def setUp(self):
        # Load environment variables from the parent directory's .env file
        env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.env"))
        load_dotenv(dotenv_path=env_path)
        
        self.site_url = os.environ.get("FRAPPE_SITE_URL")
        self.api_key = os.environ.get("FRAPPE_API_KEY")
        self.api_secret = os.environ.get("FRAPPE_API_SECRET")
        
        # ponytail: verify env variables exist before calling rest client
        self.assertIsNotNone(self.site_url, "FRAPPE_SITE_URL is not set in .env")
        self.assertIsNotNone(self.api_key, "FRAPPE_API_KEY is not set in .env")
        self.assertIsNotNone(self.api_secret, "FRAPPE_API_SECRET is not set in .env")
        
        self.client = FrappeRestClient(
            base_url=self.site_url,
            api_key=self.api_key,
            api_secret=self.api_secret
        )

    def test_frappe_connectivity(self):
        """Test that we can authenticate and get a response from Frappe."""
        print(f"\nAttempting to connect to Frappe site: {self.site_url}")
        
        try:
            # api/method/frappe.auth.get_logged_user is a standard Frappe endpoint
            res = self.client._get("api/method/frappe.auth.get_logged_user")
            logged_user = res.get("message")
            print(f"Successfully connected! Authenticated as user: {logged_user}")
            self.assertIsNotNone(logged_user, "Response message (user) should not be None")
        except Exception as e:
            self.fail(f"Failed to connect to Frappe rest API: {e}")

if __name__ == "__main__":
    unittest.main()
