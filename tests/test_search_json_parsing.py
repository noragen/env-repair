import unittest

import env_repair as ed


class TestSearchJsonParsing(unittest.TestCase):
    def test_extract_search_results_wildcard_result_pkgs(self):
        data = {
            "query": {"query": "msgpack*", "type": "search"},
            "result": {
                "msg": "",
                "pkgs": [
                    {"name": "msgpack-numpy", "version": "0.4.8"},
                    {"name": "msgpack-python", "version": "1.1.2"},
                ],
                "status": "OK",
            },
        }
        self.assertEqual(set(ed.extract_search_results(data)), {"msgpack-numpy", "msgpack-python"})

    def test_extract_search_results_result_dict_keys(self):
        data = {
            "result": {
                "pyqt": [{"name": "pyqt", "version": "5.15.11"}],
                "zope.interface": [{"name": "zope.interface", "version": "8.0.1"}],
            }
        }
        self.assertEqual(set(ed.extract_search_results(data)), {"pyqt", "zope.interface"})


if __name__ == "__main__":
    unittest.main()
