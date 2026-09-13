"""Filesystem checks that run before any service starts."""

from pathlib import Path
import tempfile
import unittest

from rb3dashboard.runtime.preflight import (
    PreflightError, check_data_directory, containing_mount)


def unmounted(path):
    """Say nothing about mount points; the mount tests cover that branch."""
    return None


class DataDirectoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def test_a_writable_directory_passes(self):
        self.assertEqual(
            check_data_directory(self.directory / "data", is_mount=unmounted), [])

    def test_it_creates_a_missing_directory(self):
        target = self.directory / "made" / "here"

        check_data_directory(target, is_mount=unmounted)

        self.assertTrue(target.is_dir())

    def test_it_leaves_no_probe_file_behind(self):
        check_data_directory(self.directory, is_mount=unmounted)

        self.assertEqual(list(self.directory.iterdir()), [])

    def test_a_path_that_is_a_file_is_fatal(self):
        target = self.directory / "afile"
        target.write_text("not a directory", encoding="utf-8")

        with self.assertRaises(PreflightError) as caught:
            check_data_directory(target, is_mount=unmounted)

        self.assertIn(str(target), str(caught.exception))

    def test_an_unwritable_directory_reports_who_we_are(self):
        def refuse(path, mode="x"):
            raise PermissionError(13, "Permission denied")

        with self.assertRaises(PreflightError) as caught:
            check_data_directory(self.directory, is_mount=unmounted, _open=refuse)

        message = str(caught.exception)
        self.assertIn(str(self.directory), message)
        self.assertIn("uid", message)
        self.assertIn("Permission denied", message)

    def test_a_percent_in_the_path_does_not_break_the_diagnostic(self):
        """An unexpanded %APPDATA% in a template field must not crash the check."""
        def refuse(path, mode="x"):
            raise PermissionError(13, "Permission denied")

        awkward = self.directory / "100%backup"
        awkward.mkdir()

        with self.assertRaises(PreflightError) as caught:
            check_data_directory(awkward, is_mount=unmounted, _open=refuse)

        self.assertIn("100%backup", str(caught.exception))
        self.assertIn("Permission denied", str(caught.exception))

    def test_a_full_disk_is_reported_not_raised_raw(self):
        """A write that fails after opening must still be a PreflightError."""
        class Full:
            def write(self, text):
                raise OSError(28, "No space left on device")

            def close(self):
                pass

        with self.assertRaises(PreflightError) as caught:
            check_data_directory(self.directory, is_mount=unmounted,
                                 _open=lambda path, mode="x": Full())

        self.assertIn("No space left on device", str(caught.exception))

    def test_a_failure_on_close_is_not_swallowed(self):
        """Buffered data is only really delivered at flush time.

        A share that is full often accepts write() into the buffer and fails
        when the handle is closed. Suppressing that is how a broken directory
        passes a write check.
        """
        class FailsOnClose:
            def write(self, text):
                pass

            def close(self):
                raise OSError(28, "No space left on device")

        with self.assertRaises(PreflightError) as caught:
            check_data_directory(self.directory, is_mount=unmounted,
                                 _open=lambda path, mode="x": FailsOnClose())

        self.assertIn("No space left on device", str(caught.exception))

    def test_it_never_touches_a_file_that_was_already_there(self):
        """The probe is exclusively created, so it cannot clobber anything."""
        bystander = self.directory / "history.sqlite3"
        bystander.write_text("precious", encoding="utf-8")

        check_data_directory(self.directory, is_mount=unmounted)

        self.assertEqual(bystander.read_text(encoding="utf-8"), "precious")

    def test_it_leaves_no_probe_behind_after_a_failed_write(self):
        class Full:
            def write(self, text):
                raise OSError(28, "No space left on device")

            def close(self):
                pass

        real = []

        def record(path, mode="x"):
            real.append(Path(path))
            return Full()

        with self.assertRaises(PreflightError):
            check_data_directory(self.directory, is_mount=unmounted, _open=record)

        self.assertEqual(list(self.directory.iterdir()), [])


class ContainingMountTests(unittest.TestCase):
    """The mount table answers the question os.path.ismount cannot."""

    SAMPLE = (
        "23 28 0:21 / /proc rw,relatime shared:12 - proc proc rw\n"
        "31 28 0:24 / / rw,relatime shared:1 - overlay overlay rw\n"
        "44 31 8:1 /appdata/rb3e /data rw,relatime shared:2 - ext4 /dev/sda1 rw\n"
    )

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.mountinfo = Path(directory.name) / "mountinfo"
        self.mountinfo.write_text(self.SAMPLE, encoding="utf-8")

    def resolve(self, path):
        # realpath is stubbed so these cases mean the same thing on Windows,
        # where os.path.realpath("/data") would become "C:\data".
        return containing_mount(path, mountinfo=str(self.mountinfo),
                                realpath=lambda value: value)

    def test_a_bind_mounted_directory_reports_its_own_mount(self):
        self.assertEqual(self.resolve("/data"), "/data")

    def test_a_subdirectory_of_a_mount_reports_that_mount(self):
        """os.path.ismount gets this one wrong, and the warning would lie."""
        self.assertEqual(self.resolve("/data/sessions"), "/data")

    def test_a_path_on_the_container_root_reports_the_root(self):
        self.assertEqual(self.resolve("/var/lib/whatever"), "/")

    def test_a_nested_pseudo_filesystem_wins_over_the_root(self):
        """The longest matching mount point is the containing one."""
        self.assertEqual(self.resolve("/proc/1"), "/proc")

    def test_an_unreadable_mount_table_says_nothing(self):
        self.assertIsNone(containing_mount("/data", mountinfo="/nonexistent",
                                           realpath=lambda value: value))

    def test_it_warns_when_the_directory_is_not_a_mount_point(self):
        warnings = check_data_directory(self.directory, is_mount=lambda path: False)

        self.assertTrue([text for text in warnings if "mount" in text.lower()])

    def test_it_is_quiet_when_the_directory_is_a_mount_point(self):
        self.assertEqual(check_data_directory(self.directory,
                                              is_mount=lambda path: True), [])
