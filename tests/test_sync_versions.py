import tempfile
import unittest
import contextlib
import io
from pathlib import Path
from unittest.mock import patch


class TestSyncVersions(unittest.TestCase):
    def test_staged_recipes_copy(self):
        # Run the tool in-process so we don't depend on shell quoting on Windows.
        import tools.sync_versions as sv

        meta_local = sv.ROOT / "conda.recipe" / "meta.yaml"
        meta_forge = sv.ROOT / "conda.recipe" / "meta-forge.yaml"
        meta_local_before = meta_local.read_text(encoding="utf-8")
        meta_forge_before = meta_forge.read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as td:
            staged = Path(td) / "staged-recipes"
            staged.mkdir(parents=True, exist_ok=True)

            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = sv.main(["--staged-recipes", str(staged)])
                self.assertEqual(rc, 0)

                pyproject = (sv.ROOT / "pyproject.toml").read_text(encoding="utf-8")
                name = sv._project_name_from_pyproject(pyproject)

                copied = staged / "recipes" / name / "meta.yaml"
                self.assertTrue(copied.exists())
            finally:
                meta_local.write_text(meta_local_before, encoding="utf-8")
                meta_forge.write_text(meta_forge_before, encoding="utf-8")

    def test_pypi_sdist_hashing_is_used_when_requested(self):
        import tools.sync_versions as sv

        meta_forge = sv.ROOT / "conda.recipe" / "meta-forge.yaml"
        meta_forge_before = meta_forge.read_text(encoding="utf-8")

        try:
            with patch.object(sv, "_sha256_pypi_sdist", return_value=("https://example.invalid/x.tar.gz", "a" * 64)):
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = sv.main(["--pypi-sdist"])
            self.assertEqual(rc, 0)
            self.assertIn("sha256: " + ("a" * 64), meta_forge.read_text(encoding="utf-8"))
        finally:
            meta_forge.write_text(meta_forge_before, encoding="utf-8")
