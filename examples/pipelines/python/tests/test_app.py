import unittest

from app.__main__ import greet


class GreetingTest(unittest.TestCase):
    def test_greeting(self):
        self.assertEqual(greet("PipeForge"), "Hello PipeForge")
