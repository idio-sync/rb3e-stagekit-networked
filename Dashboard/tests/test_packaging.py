"""Checks on the container packaging that do not need a Docker daemon.

The image has not been built or run. What can be checked here is that it asks
for the right dependencies, names settings that actually exist, copies files
that are actually present, and that the server really does run on the reduced
dependency set the image installs.
"""

from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

from rb3dashboard.runtime.config import ENV_PREFIX, ServerConfig

REPO = Path(__file__).resolve().parents[2]
DASHBOARD = REPO / "Dashboard"


def read(path):
    return path.read_text(encoding="utf-8")


def requirement_names(text):
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(re.split(r"[><=!\[]", line)[0].strip().lower())
    return names


class RequirementTests(unittest.TestCase):
    def setUp(self):
        self.server = requirement_names(read(DASHBOARD / "requirements-server.txt"))
        self.desktop = requirement_names(read(DASHBOARD / "requirements.txt"))

    def test_the_server_needs_only_requests(self):
        self.assertEqual(self.server, {"requests"})

    def test_no_desktop_dependency_is_installed_in_the_image(self):
        """Tk, Pillow, Discord IPC and display enumeration have no place here."""
        for unwanted in ("pillow", "pypresence", "screeninfo", "yt-dlp",
                         "google-api-python-client", "pyinstaller"):
            self.assertNotIn(unwanted, self.server)

    def test_every_server_requirement_is_also_a_desktop_requirement(self):
        """One version story: the desktop already pins what the server uses."""
        self.assertTrue(self.server.issubset(self.desktop))


class DockerfileTests(unittest.TestCase):
    def setUp(self):
        self.text = read(REPO / "Dockerfile")

    def test_it_copies_files_that_exist(self):
        copied = re.findall(r"^COPY\s+(\S+)\s+\S+$", self.text, re.MULTILINE)

        self.assertTrue(copied)
        for source in copied:
            self.assertTrue((REPO / source).exists(), source)

    def test_it_runs_the_server_entry_point(self):
        self.assertIn('ENTRYPOINT ["python", "/app/rb3server.py"]', self.text)

    def test_it_does_not_run_as_root(self):
        self.assertIn("USER rb3", self.text)

    def test_the_image_declares_no_anonymous_volume(self):
        """An anonymous volume hides a forgotten -v until the data is gone."""
        self.assertNotRegex(self.text, r"(?m)^\s*VOLUME")

    def test_it_health_checks_with_the_tracked_script(self):
        self.assertIn("HEALTHCHECK", self.text)
        self.assertIn("/app/healthcheck.py", self.text)
        self.assertTrue((DASHBOARD / "tools" / "healthcheck.py").exists())

    def test_it_installs_only_the_server_requirements(self):
        self.assertIn("requirements-server.txt", self.text)
        self.assertNotIn("Dashboard/requirements.txt", self.text)

    def test_it_never_copies_the_desktop_entry_point(self):
        self.assertNotIn("dashboard.py", self.text)


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.text = read(REPO / "docker-compose.yml")

    def test_host_networking_is_used(self):
        """Bridge networking carries no broadcast, so the console is invisible."""
        self.assertIn("network_mode: host", self.text)

    def test_a_data_volume_is_mapped(self):
        self.assertIn(":/data", self.text)

    def test_the_web_port_is_not_the_console_port(self):
        port = re.search(r'RB3E_WEB_PORT:\s*"(\d+)"', self.text)

        self.assertIsNotNone(port)
        self.assertNotEqual(port.group(1), "21070")

    def test_stage_kit_is_not_enabled_by_default(self):
        """Enabling it takes Pico telemetry away from a desktop dashboard."""
        self.assertIn('RB3E_STAGEKIT_ENABLED: "false"', self.text)

    def test_the_compose_file_demands_a_token(self):
        """A copied compose file must not start an unauthenticated API.

        Compose's ${VAR:?message} form aborts `docker compose up` when the
        variable is unset or empty, which is a refusal the operator sees before
        a container exists.
        """
        text = read(REPO / "docker-compose.yml")

        self.assertNotIn('RB3E_AUTH_TOKEN: ""', text)
        self.assertRegex(text, r"RB3E_AUTH_TOKEN:\s*\$\{RB3E_AUTH_TOKEN:\?[^}]+\}")

    def test_an_example_environment_file_names_the_token(self):
        example = read(REPO / ".env.example")

        self.assertIn("RB3E_AUTH_TOKEN=", example)


class EnvironmentNameTests(unittest.TestCase):
    """A typo here is silent: the setting simply never applies."""

    def named_variables(self, text):
        return set(re.findall(r"\b(RB3E_[A-Z0-9_]+)\b", text))

    def known_variables(self):
        config = ServerConfig.load(environ={})
        known = {ENV_PREFIX + name.upper() for name in config.as_dict()}
        known.add(ENV_PREFIX + "CONFIG")
        return known

    def test_compose_names_only_real_settings(self):
        named = self.named_variables(read(REPO / "docker-compose.yml"))

        self.assertTrue(named)
        self.assertEqual(named - self.known_variables(), set())

    def test_the_dockerfile_names_only_real_settings(self):
        named = self.named_variables(read(REPO / "Dockerfile"))

        self.assertEqual(named - self.known_variables(), set())

    def test_the_health_check_names_only_real_settings(self):
        named = self.named_variables(read(DASHBOARD / "tools" / "healthcheck.py"))

        self.assertEqual(named - self.known_variables(), set())


class HealthCheckTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(DASHBOARD / "tools"))
        self.addCleanup(lambda: sys.path.remove(str(DASHBOARD / "tools")))
        import healthcheck
        self.healthcheck = healthcheck

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name

    def test_a_wildcard_bind_is_probed_on_loopback(self):
        """The check runs inside the container and never leaves it."""
        for host in ("0.0.0.0", "", "::", "*"):
            url = self.healthcheck.target({"RB3E_WEB_HOST": host,
                                           "RB3E_WEB_PORT": "8321"})
            self.assertEqual(url, "http://127.0.0.1:8321/api/health")

    def test_a_specific_host_and_port_are_used(self):
        url = self.healthcheck.target({"RB3E_WEB_HOST": "10.0.0.5",
                                       "RB3E_WEB_PORT": "9000"})

        self.assertEqual(url, "http://10.0.0.5:9000/api/health")

    def test_the_defaults_match_the_configuration_defaults(self):
        defaults = ServerConfig.load(environ={})
        url = self.healthcheck.target({})

        self.assertEqual(url, "http://127.0.0.1:%d/api/health" % defaults.web_port)

    def test_a_healthy_server_passes(self):
        class Response:
            status = 200

            def read(self_inner):
                return b'{"status": "ok", "running": true}'

        ok, detail = self.healthcheck.check("http://x/api/health",
                                            opener=lambda url, timeout=None: Response())

        self.assertTrue(ok, detail)

    def test_a_server_that_is_not_running_yet_fails(self):
        """The port answers before every service is up."""
        class Response:
            status = 200

            def read(self_inner):
                return b'{"status": "ok", "running": false}'

        ok, _ = self.healthcheck.check("http://x/api/health",
                                       opener=lambda url, timeout=None: Response())

        self.assertFalse(ok)

    def test_an_unreachable_server_fails_without_raising(self):
        def refuse(url, timeout=None):
            raise IOError("connection refused")

        ok, detail = self.healthcheck.check("http://x/api/health", opener=refuse)

        self.assertFalse(ok)
        self.assertIn("connection refused", detail)

    def test_an_error_status_fails(self):
        class Response:
            status = 503

            def read(self_inner):
                return b""

        ok, _ = self.healthcheck.check("http://x/api/health",
                                       opener=lambda url, timeout=None: Response())

        self.assertFalse(ok)

    def test_an_unreadable_body_fails(self):
        class Response:
            status = 200

            def read(self_inner):
                return b"<html>not json</html>"

        ok, _ = self.healthcheck.check("http://x/api/health",
                                       opener=lambda url, timeout=None: Response())

        self.assertFalse(ok)

    def test_it_follows_a_configuration_file(self):
        path = Path(self.directory) / "server.json"
        path.write_text('{"web_port": 9100}', encoding="utf-8")

        url, error = self.healthcheck.resolve({"RB3E_CONFIG": str(path)})

        self.assertIsNone(error)
        self.assertIn(":9100/", url)

    def test_the_environment_still_wins_over_the_file(self):
        path = Path(self.directory) / "server.json"
        path.write_text('{"web_port": 9100}', encoding="utf-8")

        url, error = self.healthcheck.resolve({"RB3E_CONFIG": str(path),
                                               "RB3E_WEB_PORT": "9200"})

        self.assertIsNone(error)
        self.assertIn(":9200/", url)

    def test_an_unreadable_configuration_is_unhealthy_not_a_guess(self):
        """A server that cannot load its config never serves. Never guess."""
        path = Path(self.directory) / "server.json"
        path.write_text("{not json", encoding="utf-8")

        url, error = self.healthcheck.resolve({"RB3E_CONFIG": str(path)})

        self.assertIsNone(url)
        self.assertIn(str(path), error)

    def test_an_unreadable_configuration_exits_nonzero(self):
        path = Path(self.directory) / "server.json"
        path.write_text("{not json", encoding="utf-8")

        self.assertEqual(self.healthcheck.main({"RB3E_CONFIG": str(path)}), 1)

    def test_no_configuration_file_uses_the_environment(self):
        url, error = self.healthcheck.resolve({"RB3E_WEB_PORT": "8321"})

        self.assertIsNone(error)
        self.assertIn(":8321/", url)

    def test_a_file_that_is_not_utf8_is_unhealthy_not_a_traceback(self):
        path = Path(self.directory) / "server.json"
        path.write_bytes(b"\xff\xfe{\x00}\x00")

        url, error = self.healthcheck.resolve({"RB3E_CONFIG": str(path)})

        self.assertIsNone(url)
        self.assertIn(str(path), error)


class DocumentationTests(unittest.TestCase):
    """Documentation ships with the project, so it has to be readable."""

    def documents(self):
        return sorted(list((REPO / "docs").rglob("*.md")) + [REPO / "README.md"])

    def test_every_document_is_valid_utf8(self):
        """A tool that writes with the platform default silently produces a
        file that renders as mojibake on GitHub."""
        for path in self.documents():
            try:
                path.read_bytes().decode("utf-8")
            except UnicodeDecodeError as error:
                self.fail("%s is not UTF-8: %s" % (path.name, error))

    def test_internal_links_and_anchors_resolve(self):
        import re

        def anchors(path):
            found = set()
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("#"):
                    title = line.lstrip("#").strip()
                    found.add(re.sub(r"[^a-z0-9 -]", "",
                                     title.lower()).replace(" ", "-"))
            return found

        broken = []
        for source in self.documents():
            text = source.read_text(encoding="utf-8")
            for target in re.findall(r"\]\((?!https?:|\.\./\.\./)([^)]+)\)", text):
                path, _, anchor = target.partition("#")
                resolved = (source.parent / path) if path else source
                if path and not resolved.exists():
                    broken.append("%s -> %s" % (source.name, path))
                elif anchor and resolved.suffix == ".md"                         and anchor not in anchors(resolved):
                    broken.append("%s -> %s#%s" % (source.name, path, anchor))
        self.assertEqual(broken, [])


class ReducedDependencyTests(unittest.TestCase):
    def test_the_server_starts_with_only_the_image_dependencies_available(self):
        """The best available stand-in for the image: block everything the
        image does not install, then build and start the real application."""
        script = textwrap.dedent("""\
            import importlib.abc
            import socket
            import sys
            import tempfile

            ABSENT = {
                'tkinter', 'PIL', 'googleapiclient', 'yt_dlp', 'pypresence',
                'screeninfo', 'vlc', 'paho',
            }

            class NotInstalled(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in ABSENT:
                        raise ImportError('No module named ' + fullname)

            sys.meta_path.insert(0, NotInstalled())

            import rb3server
            from rb3dashboard.runtime.config import ServerConfig

            # A real port: the configuration refuses 0, because an ephemeral
            # dashboard port is one nobody could find again.
            probe = socket.socket()
            probe.bind(('127.0.0.1', 0))
            free_port = probe.getsockname()[1]
            probe.close()

            with tempfile.TemporaryDirectory() as data_dir:
                config = ServerConfig.load(environ={
                    'RB3E_DATA_DIR': data_dir,
                    'RB3E_WEB_HOST': '127.0.0.1',
                    'RB3E_WEB_PORT': str(free_port),
                    'RB3E_AUTH_TOKEN': 'smoke',
                })
                application = rb3server.build_application(config)
                application.start()
                assert application.is_running()
                assert application.history is not None
                application.stop()
            print('server ran on the image dependency set')
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(DASHBOARD), capture_output=True, text=True, timeout=60)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("image dependency set", result.stdout)


if __name__ == "__main__":
    unittest.main()
