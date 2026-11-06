"""catalog/fill.py: the committed entry keeps its placeholders; the filled copy has none and refuses bad values."""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("catalog_fill", os.path.join(ROOT, "catalog", "fill.py"))
fill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fill)
TEXT = open(os.path.join(ROOT, "catalog", "falsify.yaml")).read()


class Fill(unittest.TestCase):
    def test_the_committed_entry_keeps_the_placeholders_on_the_owners_account(self):
        self.assertIn('repo: "https://github.com/CodexOperator/{REPO_NAME}"', TEXT)
        self.assertIn('sha: "{SHA}"', TEXT)
        self.assertIn("maintainer: CodexOperator", TEXT)

    def test_fill_replaces_every_placeholder(self):
        out = fill.fill(TEXT, "falsify", "a" * 40)
        self.assertIn('repo: "https://github.com/CodexOperator/falsify"', out)
        self.assertIn('sha: "' + "a" * 40 + '"', out)
        self.assertNotIn("{", out.split("description")[0])

    def test_fill_refuses_what_the_validator_would(self):
        for args in (("fal sify", "a" * 40), ("falsify", "abc"), ("falsify", "A" * 40)):
            with self.assertRaises(SystemExit):
                fill.fill(TEXT, *args)


if __name__ == "__main__":
    unittest.main()
