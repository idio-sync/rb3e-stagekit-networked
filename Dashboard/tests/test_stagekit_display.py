"""Optional desktop lighting descriptions follow the firmware byte meanings."""

from types import SimpleNamespace
import unittest

from rb3dashboard.desktop.stagekit import StageKitViewMixin


class StageKitDisplayTests(unittest.TestCase):
    def test_command_byte_and_led_pattern_are_described_independently(self):
        logs = []
        view = SimpleNamespace(log_stagekit_var=SimpleNamespace(get=lambda: True),
                               log_message=logs.append)
        cases = (
            (0xff, 0x20, 'Blue LEDs=0xFF'),
            (0x12, 0x40, 'Green LEDs=0x12'),
            (0x81, 0x60, 'Yellow LEDs=0x81'),
            (0x00, 0x80, 'Red LEDs=0x00'),
            (0x00, 0x01, 'Fog=ON'), (0xff, 0x02, 'Fog=OFF'),
            (0x00, 0x03, 'Strobe=1'), (0x00, 0x04, 'Strobe=2'),
            (0x00, 0x05, 'Strobe=3'), (0x00, 0x06, 'Strobe=4'),
            (0x00, 0x07, 'Strobe=OFF'), (0x00, 0xff, 'All OFF'),
            (0x55, 0x99, 'Unknown command=0x99'),
        )
        for left, right, description in cases:
            with self.subTest(left=left, right=right):
                StageKitViewMixin.on_stagekit_event(view, left, right)
                self.assertIn(description, logs[-1])
                self.assertIn('(L=0x{:02X} R=0x{:02X})'.format(left, right), logs[-1])

    def test_disabled_optional_logging_produces_no_message(self):
        logs = []
        view = SimpleNamespace(log_stagekit_var=SimpleNamespace(get=lambda: False),
                               log_message=logs.append)
        StageKitViewMixin.on_stagekit_event(view, 0xff, 0x20)
        self.assertEqual(logs, [])


if __name__ == '__main__':
    unittest.main()
