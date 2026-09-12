"""The listener must be usable without the desktop application."""

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


class ListenerImportTests(unittest.TestCase):
    def test_import_does_not_need_desktop_dependencies_or_start_services(self):
        script = textwrap.dedent("""\
            import importlib.abc
            import sys
            import threading
            from unittest.mock import patch

            class BlockDesktopImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {
                        'dashboard', 'tkinter', 'PIL', 'googleapiclient',
                        'yt_dlp', 'pypresence', 'screeninfo',
                    }:
                        raise AssertionError('Desktop dependency: ' + fullname)

            def block_side_effects(event, args):
                if event in {'socket.__new__', 'subprocess.Popen', 'os.system'}:
                    raise AssertionError('Startup side effect: ' + event)

            sys.meta_path.insert(0, BlockDesktopImports())
            sys.addaudithook(block_side_effects)
            with patch.object(threading.Thread, 'start',
                              side_effect=AssertionError('Started a thread')):
                from rb3dashboard.listener import UnifiedRB3EListener
                UnifiedRB3EListener()
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
