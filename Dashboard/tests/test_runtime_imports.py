"""The headless runtime must import on a host with no desktop session.

This walks the whole ``rb3dashboard.runtime`` package rather than naming
modules, so a module added by a later Stage 2 task is covered automatically.
"""

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


class RuntimeImportBoundaryTests(unittest.TestCase):
    def run_boundary_script(self, body):
        script = textwrap.dedent("""\
            import importlib
            import importlib.abc
            import pkgutil
            import sys
            import threading
            from unittest.mock import patch

            BLOCKED = {
                'dashboard', 'tkinter', 'PIL', 'googleapiclient',
                'yt_dlp', 'pypresence', 'screeninfo', 'vlc',
            }

            class BlockDesktopImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in BLOCKED:
                        raise AssertionError('Desktop dependency: ' + fullname)

            def block_side_effects(event, args):
                if event in {'socket.__new__', 'subprocess.Popen', 'os.system'}:
                    raise AssertionError('Import side effect: ' + event)

            sys.meta_path.insert(0, BlockDesktopImports())
            sys.addaudithook(block_side_effects)
        """) + textwrap.dedent(body)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_every_runtime_module_imports_without_desktop_dependencies(self):
        self.run_boundary_script("""\
            import rb3dashboard.runtime as runtime

            names = [name for _, name, _ in
                     pkgutil.walk_packages(runtime.__path__, runtime.__name__ + '.')]
            assert names, 'No runtime modules were discovered'
            with patch.object(threading.Thread, 'start',
                              side_effect=AssertionError('Started a thread')):
                for name in names:
                    importlib.import_module(name)
            print('imported: ' + ', '.join(sorted(names)))
        """)

    def test_runtime_services_construct_without_starting_work(self):
        self.run_boundary_script("""\
            with patch.object(threading.Thread, 'start',
                              side_effect=AssertionError('Started a thread')):
                from rb3dashboard.runtime.dispatch import HeadlessDispatcher
                from rb3dashboard.runtime.state import ApplicationState
                HeadlessDispatcher()
                ApplicationState()
        """)


if __name__ == "__main__":
    unittest.main()
