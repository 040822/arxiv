import hashlib
import json
import multiprocessing
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from source.settings import store as settings_store
from source.settings import admin as admin_credentials


def _concurrent_bootstrap_worker(settings_path, results):
    settings_store.SETTINGS_PATH = settings_path
    original_new_password = admin_credentials._new_password
    try:
        admin_credentials._new_password = lambda: (
            time.sleep(0.15) or f"generated-password-{os.getpid()}"
        )
        result = admin_credentials.ensure_admin_password()
        results.put(result.generated_password)
    finally:
        admin_credentials._new_password = original_new_password


class AdminCredentialTests(unittest.TestCase):
    def setUp(self):
        self.original_path = settings_store.SETTINGS_PATH
        self.tmp = tempfile.TemporaryDirectory()
        settings_store.SETTINGS_PATH = os.path.join(self.tmp.name, "settings.json")

    def tearDown(self):
        settings_store.SETTINGS_PATH = self.original_path
        self.tmp.cleanup()

    def _write_settings(self, data):
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)

    def _read_settings(self):
        with open(settings_store.SETTINGS_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_bootstrap_generates_once_and_preserves_existing_fields(self):
        self._write_settings({
            "settings_schema_version": 3,
            "custom_field": {"keep": True},
            "admin_password": "",
        })

        first = admin_credentials.ensure_admin_password()
        persisted_after_first = self._read_settings()
        second = admin_credentials.ensure_admin_password()

        self.assertIsNotNone(first.generated_password)
        self.assertGreaterEqual(len(first.generated_password), 32)
        self.assertTrue(first.password_change_recommended)
        self.assertTrue(persisted_after_first["admin_password"].startswith("scrypt:"))
        self.assertTrue(persisted_after_first["admin_password_change_recommended"])
        self.assertEqual(persisted_after_first["custom_field"], {"keep": True})
        self.assertIsNone(second.generated_password)
        self.assertEqual(second.password_hash, persisted_after_first["admin_password"])
        self.assertEqual(self._read_settings(), persisted_after_first)

    def test_bootstrap_refuses_to_overwrite_invalid_json(self):
        with open(settings_store.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            handle.write("{invalid")

        with self.assertRaises(admin_credentials.AdminCredentialError):
            admin_credentials.ensure_admin_password()

        with open(settings_store.SETTINGS_PATH, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "{invalid")

    def test_concurrent_bootstrap_generates_only_one_password(self):
        self._write_settings({
            "settings_schema_version": 3,
            "admin_password": "",
        })
        context = multiprocessing.get_context("fork")
        results = context.Queue()
        workers = [
            context.Process(
                target=_concurrent_bootstrap_worker,
                args=(settings_store.SETTINGS_PATH, results),
            )
            for _ in range(3)
        ]

        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        self.assertTrue(all(worker.exitcode == 0 for worker in workers))
        generated = [results.get(timeout=1) for _ in workers]
        self.assertEqual(sum(password is not None for password in generated), 1)
        self.assertTrue(self._read_settings()["admin_password"].startswith("scrypt:"))

    def test_set_password_enforces_minimum_length_in_credentials_module(self):
        self._write_settings({
            "settings_schema_version": 3,
            "admin_password": "existing-hash",
        })

        with self.assertRaisesRegex(ValueError, "至少需要 12 个字符"):
            admin_credentials.set_admin_password("too-short")

        self.assertEqual(self._read_settings()["admin_password"], "existing-hash")

    def test_legacy_sha256_is_upgraded_after_successful_login(self):
        password = "legacy-password"
        legacy_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        self._write_settings({
            "settings_schema_version": 3,
            "admin_password": legacy_hash,
            "admin_password_change_recommended": False,
            "custom_field": "keep-me",
        })

        self.assertFalse(admin_credentials.verify_admin_password("wrong-password"))
        self.assertEqual(self._read_settings()["admin_password"], legacy_hash)
        self.assertTrue(admin_credentials.verify_admin_password(password))

        upgraded = self._read_settings()
        self.assertTrue(upgraded["admin_password"].startswith("scrypt:"))
        self.assertEqual(upgraded["custom_field"], "keep-me")
        self.assertTrue(admin_credentials.verify_admin_password(password))

    def test_reset_backs_up_settings_and_returns_new_generated_password(self):
        self._write_settings({
            "settings_schema_version": 3,
            "admin_password": "old-hash",
            "admin_password_change_recommended": False,
            "custom_field": "keep-me",
        })

        with patch.object(admin_credentials, "_new_password", return_value="new-random-password"):
            result = admin_credentials.reset_admin_password()

        persisted = self._read_settings()
        backup_dir = os.path.join(self.tmp.name, "settings-backup")
        backups = os.listdir(backup_dir)
        self.assertEqual(result.generated_password, "new-random-password")
        self.assertTrue(persisted["admin_password"].startswith("scrypt:"))
        self.assertTrue(persisted["admin_password_change_recommended"])
        self.assertEqual(persisted["custom_field"], "keep-me")
        self.assertEqual(len(backups), 1)
        with open(os.path.join(backup_dir, backups[0]), "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["admin_password"], "old-hash")


if __name__ == "__main__":
    unittest.main()
