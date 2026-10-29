import unittest
import re
from launcher.tunnel import CLOUDFLARED_URL_REGEX, is_tool_available

class TestTunnel(unittest.TestCase):
    def test_regex_matching(self):
        sample_log = "2026-10-30T00:00:00Z INF +--------------------------------------------------------------------------------------------+\n" \
                     "2026-10-30T00:00:00Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |\n" \
                     "2026-10-30T00:00:00Z INF |  https://alpha-beta-gamma.trycloudflare.com                                               |\n" \
                     "2026-10-30T00:00:00Z INF +--------------------------------------------------------------------------------------------+\n"

        match = CLOUDFLARED_URL_REGEX.search(sample_log)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(0), "https://alpha-beta-gamma.trycloudflare.com")
        self.assertEqual(match.group(0).replace("https://", "wss://"), "wss://alpha-beta-gamma.trycloudflare.com")

    def test_is_tool_available(self):
        # python3 is always available
        self.assertTrue(is_tool_available("python3"))
        # non-existent command
        self.assertFalse(is_tool_available("non_existent_command_12345"))

if __name__ == "__main__":
    unittest.main()

