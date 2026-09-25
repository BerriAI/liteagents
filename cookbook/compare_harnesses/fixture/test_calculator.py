import unittest

from calculator import subtract


class SubtractionTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(subtract(9, 4), 5)

    def test_negative(self):
        self.assertEqual(subtract(-3, 7), -10)

    def test_zero(self):
        self.assertEqual(subtract(6, 0), 6)
