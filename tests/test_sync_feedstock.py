import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import sync_versions as sync


class TestFeedstockSync(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "pyproject.toml").write_text('name = "env-repair"\nversion = "0.2.8"\n', encoding="utf-8")
        recipes = self.root / "conda.recipe"
        recipes.mkdir()
        self.local = recipes / "meta.yaml"
        self.local.write_text('{% set name = "env-repair" %}\n{% set version = "0.2.7" %}\n', encoding="utf-8")
        self.template = recipes / "meta-forge.yaml"
        self.original = ('schema_version: 1\ncontext:\n  name: env-repair\n  version: "0.2.7"\n'
                         'source:\n  sha256: ' + 'a' * 64 + '\nbuild:\n  number: 4\n'
                         'requirements:\n  host:\n    - setuptools >=68\nextra:\n  custom: retained\n')
        self.template.write_text(self.original, encoding="utf-8")
        self.feedstock = self.root / "feedstock"
        (self.feedstock / "recipe").mkdir(parents=True)
        self.target = self.feedstock / "recipe" / "recipe.yaml"
        self.target.write_text(self.original, encoding="utf-8")
        patcher = patch.object(sync, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_sync(self, *args):
        with contextlib.redirect_stderr(io.StringIO()), patch.object(
            sync, "_sha256_pypi_sdist", return_value=("https://example.invalid/source.tar.gz", "b" * 64)
        ):
            return sync.main(list(args))

    def test_updates_version_hash_build_number_and_preserves_feedstock_settings(self):
        self.assertEqual(self.run_sync("--pypi-sdist", "--feedstock", str(self.feedstock)), 0)
        expected = self.original.replace('version: "0.2.7"', 'version: "0.2.8"').replace("a" * 64, "b" * 64).replace("number: 4", "number: 0")
        self.assertEqual(self.target.read_text(encoding="utf-8"), expected)

    def test_same_version_retains_build_number(self):
        self.target.write_text(self.original.replace('"0.2.7"', '"0.2.8"'), encoding="utf-8")
        self.assertEqual(self.run_sync("--pypi-sdist", "--feedstock", str(self.feedstock)), 0)
        self.assertIn("number: 4", self.target.read_text(encoding="utf-8"))

    def test_missing_hash_does_not_change_any_recipe(self):
        before = self.local.read_bytes()
        self.assertEqual(self.run_sync("--feedstock", str(self.feedstock)), 1)
        self.assertEqual(self.local.read_bytes(), before)
        self.assertEqual(self.target.read_text(encoding="utf-8"), self.original)

    def test_missing_feedstock_is_not_created(self):
        destination = self.root / "missing"
        self.assertEqual(self.run_sync("--pypi-sdist", "--feedstock", str(destination)), 1)
        self.assertFalse(destination.exists())
        self.assertIn('"0.2.7"', self.local.read_text(encoding="utf-8"))

    def test_download_failure_does_not_reuse_old_hash_or_write_recipes(self):
        with patch.object(sync, "_sha256_pypi_sdist", side_effect=OSError("unavailable")), contextlib.redirect_stderr(io.StringIO()):
            result = sync.main(["--pypi-sdist", "--feedstock", str(self.feedstock)])
        self.assertEqual(result, 1)
        self.assertEqual(self.target.read_text(encoding="utf-8"), self.original)
        self.assertEqual(self.template.read_text(encoding="utf-8"), self.original)

    def test_target_options_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            sync.main(["--feedstock", str(self.feedstock), "--staged-recipes", "legacy"])

    def test_exact_archive_is_supported_offline(self):
        archive = self.root / "source.tar.gz"
        archive.write_bytes(b"published archive fixture")
        self.assertEqual(self.run_sync("--sdist", str(archive), "--feedstock", str(self.feedstock)), 0)
        self.assertIn(sync._sha256_file(archive), self.target.read_text(encoding="utf-8"))
