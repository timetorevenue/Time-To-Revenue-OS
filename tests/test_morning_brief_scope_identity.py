"""A ClientScopeError from either import spelling is a scope refusal to the morning-brief detector
(the 2026-11-08 nightly failure: `python -m` raised tools.business_brain_scope.ClientScopeError,
the detector caught only the bare-named class)."""

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ScopeErrorIdentityTest(unittest.TestCase):
    def test_both_spellings_match_and_other_errors_do_not(self):
        if str(ROOT / "tools") not in sys.path:
            sys.path.insert(0, str(ROOT / "tools"))
        from tools import morning_brief_detector as detector
        bare = importlib.import_module("business_brain_scope")
        qualified = importlib.import_module("tools.business_brain_scope")
        self.assertIsNot(bare.ClientScopeError, qualified.ClientScopeError)
        self.assertTrue(detector._is_scope_error(bare.ClientScopeError("x")))
        self.assertTrue(detector._is_scope_error(qualified.ClientScopeError("x")))
        self.assertFalse(detector._is_scope_error(ValueError("pointer must start with business_brain:")))


if __name__ == "__main__":
    unittest.main()
