import unittest

from onebot_ws import DEFAULT_WS_URL, build_ws_connect_url, mask_ws_url, normalize_ws_url


class NormalizeWsUrlTest(unittest.TestCase):
    def test_empty_falls_back_to_default(self):
        self.assertEqual(normalize_ws_url(""), DEFAULT_WS_URL)
        self.assertEqual(normalize_ws_url("   "), DEFAULT_WS_URL)
        self.assertEqual(normalize_ws_url(None), DEFAULT_WS_URL)

    def test_host_only_gets_default_path(self):
        self.assertEqual(
            normalize_ws_url("ws://127.0.0.1:8080"), "ws://127.0.0.1:8080/onebot/v11/ws"
        )
        self.assertEqual(
            normalize_ws_url("ws://127.0.0.1:8080/"), "ws://127.0.0.1:8080/onebot/v11/ws"
        )

    def test_custom_path_is_preserved(self):
        self.assertEqual(normalize_ws_url("ws://h:1/custom"), "ws://h:1/custom")

    def test_wss_supported(self):
        self.assertEqual(normalize_ws_url("wss://h:1"), "wss://h:1/onebot/v11/ws")

    def test_non_ws_scheme_returned_untouched(self):
        self.assertEqual(normalize_ws_url("http://h:1"), "http://h:1")

    def test_query_and_fragment_preserved(self):
        self.assertEqual(
            normalize_ws_url("ws://h:1?a=b"), "ws://h:1/onebot/v11/ws?a=b"
        )

    def test_surrounding_whitespace_trimmed(self):
        self.assertEqual(normalize_ws_url("  ws://h:1  "), "ws://h:1/onebot/v11/ws")


class BuildWsConnectUrlTest(unittest.TestCase):
    def test_no_token_leaves_url_alone(self):
        self.assertEqual(
            build_ws_connect_url("ws://h:1", ""), "ws://h:1/onebot/v11/ws"
        )
        self.assertEqual(
            build_ws_connect_url("ws://h:1", "   "), "ws://h:1/onebot/v11/ws"
        )

    def test_token_appended_as_query(self):
        self.assertEqual(
            build_ws_connect_url("ws://h:1", "secret"),
            "ws://h:1/onebot/v11/ws?access_token=secret",
        )

    def test_existing_token_in_url_wins(self):
        self.assertEqual(
            build_ws_connect_url("ws://h:1/p?access_token=fromurl", "fromenv"),
            "ws://h:1/p?access_token=fromurl",
        )

    def test_other_query_params_preserved(self):
        url = build_ws_connect_url("ws://h:1/p?a=b", "secret")
        self.assertIn("a=b", url)
        self.assertIn("access_token=secret", url)

    def test_token_is_url_encoded(self):
        self.assertIn("%2F", build_ws_connect_url("ws://h:1/p", "a/b"))


class MaskWsUrlTest(unittest.TestCase):
    def test_no_query_unchanged(self):
        self.assertEqual(mask_ws_url("ws://h:1/p"), "ws://h:1/p")

    def test_access_token_masked(self):
        self.assertEqual(
            mask_ws_url("ws://h:1/p?access_token=secret"), "ws://h:1/p?access_token=%2A%2A%2A"
        )

    def test_token_key_masked_case_insensitively(self):
        masked = mask_ws_url("ws://h:1/p?Token=secret")
        self.assertNotIn("secret", masked)

    def test_other_params_kept(self):
        masked = mask_ws_url("ws://h:1/p?a=b&access_token=secret")
        self.assertIn("a=b", masked)
        self.assertNotIn("secret", masked)


if __name__ == "__main__":
    unittest.main()
