import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import garmin_auth


class TestTokenStore(unittest.TestCase):
    def test_token_store_is_expanded_home_path(self):
        self.assertEqual(garmin_auth.TOKEN_STORE, os.path.expanduser("~/.garminconnect"))


class TestLoadDotenvDefaults(unittest.TestCase):
    def setUp(self):
        self._env_backup = {}
        for key in ("GARMIN_EMAIL", "GARMIN_PASSWORD"):
            self._env_backup[key] = os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _write_dotenv(self, content):
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False, encoding="utf-8"
        )
        handle.write(content)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_loads_keys_not_already_set(self):
        path = self._write_dotenv("GARMIN_EMAIL=you@example.com\nGARMIN_PASSWORD=secret\n")
        garmin_auth._load_dotenv_defaults(path)
        self.assertEqual(os.environ.get("GARMIN_EMAIL"), "you@example.com")
        self.assertEqual(os.environ.get("GARMIN_PASSWORD"), "secret")

    def test_does_not_override_existing_env_var(self):
        os.environ["GARMIN_EMAIL"] = "already-set@example.com"
        path = self._write_dotenv("GARMIN_EMAIL=from-dotenv@example.com\n")
        garmin_auth._load_dotenv_defaults(path)
        self.assertEqual(os.environ.get("GARMIN_EMAIL"), "already-set@example.com")

    def test_ignores_blank_lines_and_comments(self):
        path = self._write_dotenv("# comment\n\nGARMIN_EMAIL=you@example.com\n")
        garmin_auth._load_dotenv_defaults(path)
        self.assertEqual(os.environ.get("GARMIN_EMAIL"), "you@example.com")

    def test_strips_quotes_from_value(self):
        path = self._write_dotenv('GARMIN_PASSWORD="quoted secret"\n')
        garmin_auth._load_dotenv_defaults(path)
        self.assertEqual(os.environ.get("GARMIN_PASSWORD"), "quoted secret")

    def test_missing_file_is_a_silent_noop(self):
        garmin_auth._load_dotenv_defaults("/no/such/path/.env")
        self.assertIsNone(os.environ.get("GARMIN_EMAIL"))


class TestGetClientWithoutGarminconnect(unittest.TestCase):
    def test_exits_with_helpful_message_when_garminconnect_missing(self):
        original = garmin_auth.garminconnect
        garmin_auth.garminconnect = None
        try:
            with self.assertRaises(SystemExit) as ctx:
                garmin_auth.get_client()
            self.assertIn("garminconnect", str(ctx.exception))
        finally:
            garmin_auth.garminconnect = original


if __name__ == "__main__":
    unittest.main()
