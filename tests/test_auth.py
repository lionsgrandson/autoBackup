import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autobackup import auth

class KeyringStub:
    def __init__(self):
        self.values = {}
        self.errors = types.SimpleNamespace(PasswordDeleteError=KeyError)
    def set_password(self, s, u, value):
        self.values[s, u] = value
    def get_password(self, s, u):
        return self.values.get((s, u))
    def delete_password(self, s, u):
        del self.values[s, u]

class AuthTests(unittest.TestCase):
    def setUp(self):
        self.keys = KeyringStub()
        self.k = patch.dict(sys.modules, {"keyring": self.keys})
        self.k.start()
        self.cfg = {"url": "https://example.supabase.co", "key": "sb_publishable_example"}
        self.p = patch.object(auth, "public_config", return_value=self.cfg)
        self.p.start()
    def tearDown(self):
        self.k.stop()
        self.p.stop()

    def test_username(self):
        self.assertTrue(auth.USERNAME_RE.fullmatch("customer_01"))
        self.assertFalse(auth.USERNAME_RE.fullmatch("evil@example.com"))

    def test_login(self):
        uid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        def request(url, body, headers=None, timeout=45):
            if "/token" in url:
                self.assertEqual(body["email"], "customer1@" + auth.LOGIN_DOMAIN)
                return {"user": {"id": uid, "app_metadata": {"autobackup_enabled": True}},
                        "access_token": "token", "refresh_token": "refresh"}
            self.assertEqual(body["action"], "register_device")
            return {"ok": True}
        with patch.object(auth, "request", side_effect=request):
            result = auth.sign_in({}, "customer1", "strong_password_here")
        self.assertEqual(result["user_id"], uid)
        self.assertTrue(result["device_id"])
        self.assertIn("refresh", self.keys.values.values())

    def test_refresh_rotation(self):
        uid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.keys.set_password(auth.SERVICE, auth._stored_key(self.cfg, uid), "old")
        def result(url, body, headers):
            self.assertEqual(body["refresh_token"], "old")
            return {"user": {"id": uid}, "access_token": "new", "refresh_token": "new-refresh"}
        with patch.object(auth, "request", side_effect=result):
            self.assertEqual(auth.access_token({"user_id": uid}), "new")
        self.assertEqual(self.keys.get_password(auth.SERVICE, auth._stored_key(self.cfg, uid)), "new-refresh")

    def test_missing_session(self):
        with self.assertRaises(auth.AuthError):
            auth.access_token({"user_id": "a"*36})

    def test_refuse_incomplete_sts(self):
        with patch.object(auth, "access_token", return_value="jwt"), \
             patch.object(auth, "_broker", return_value={
                 "repository": "s3:https://example.r2.cloudflarestorage.com/bucket",
                 "access_key_id": "x", "secret_access_key": "y", "password": "z"}):
            with self.assertRaises(auth.AuthError):
                auth.grant({"device_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"})

if __name__ == "__main__":
    unittest.main()
