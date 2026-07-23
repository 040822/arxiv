import unittest

from source.value_coercion import as_bool, as_float, as_int


class ValueCoercionTests(unittest.TestCase):
    def test_as_int_accepts_numeric_values_and_uses_default_for_invalid_values(self):
        self.assertEqual(as_int("42", 7), 42)
        self.assertEqual(as_int(3.8, 7), 3)
        self.assertEqual(as_int(None, 7), 7)
        self.assertEqual(as_int("", 7), 7)
        self.assertEqual(as_int("not-a-number", 7), 7)

    def test_bool_and_float_coercion_preserve_existing_settings_semantics(self):
        self.assertTrue(as_bool("yes", False))
        self.assertFalse(as_bool("off", True))
        self.assertTrue(as_bool(1, False))
        self.assertEqual(as_bool(None, True), True)
        self.assertEqual(as_float("0.25", 1.0), 0.25)
        self.assertEqual(as_float("", 1.0), 1.0)
        self.assertEqual(as_float("invalid", 1.0), 1.0)


if __name__ == "__main__":
    unittest.main()
