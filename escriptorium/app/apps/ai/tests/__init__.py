import os
import unittest


def load_tests(loader, tests, pattern):
    """So `python3 -m unittest ai.tests` discovers test_*.py in this package."""
    this_dir = os.path.dirname(__file__)
    return loader.discover(start_dir=this_dir, pattern="test*.py")
