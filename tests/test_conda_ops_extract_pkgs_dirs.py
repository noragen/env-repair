import unittest

from env_repair.conda_ops import extract_pkgs_dirs


class TestExtractPkgsDirs(unittest.TestCase):
    def test_extracts_list(self):
        info = {"pkgs_dirs": ["C:\\Anaconda3\\pkgs", "D:\\cache\\pkgs"]}
        self.assertEqual(extract_pkgs_dirs(info), ["C:\\Anaconda3\\pkgs", "D:\\cache\\pkgs"])

    def test_handles_missing(self):
        self.assertEqual(extract_pkgs_dirs({}), [])


if __name__ == "__main__":
    unittest.main()

