import time
import unittest

from controller.sync import SyncEngine

class TestSyncEngine(unittest.TestCase):
    def test_drift_calculation(self):
        engine = SyncEngine()
        drift, expected, needs_seek = engine.calculate_drift(
            local_time_s=13.0,
            host_time_s=10.0,
            sent_at_ms=1000,
            now_ms=2000,
            host_paused=False
        )
        self.assertAlmostEqual(expected, 11.0)
        self.assertAlmostEqual(drift, 2.0)
        self.assertTrue(needs_seek)

        drift, expected, needs_seek = engine.calculate_drift(
            local_time_s=25.2,
            host_time_s=25.0,
            sent_at_ms=1000,
            now_ms=6000,
            host_paused=True
        )
        self.assertAlmostEqual(expected, 25.0)
        self.assertAlmostEqual(drift, 0.2)
        self.assertFalse(needs_seek)

    def test_echo_suppression(self):
        events = []
        engine = SyncEngine(on_local_event=lambda e, d: events.append((e, d)))

        engine.register_remote_command("pause", True, timeout=1.5)
        engine.update_property("pause", True)

        self.assertEqual(len(events), 0)

        engine.update_property("pause", False)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "local_pause")

    def test_local_seek_detection(self):
        events = []
        engine = SyncEngine(on_local_event=lambda e, d: events.append((e, d)))

        engine.update_property("time-pos", 10.0)
        time.sleep(0.1)

        engine.update_property("time-pos", 25.0)

        self.assertTrue(any(e[0] == "local_seek" for e in events))

if __name__ == "__main__":
    unittest.main()

