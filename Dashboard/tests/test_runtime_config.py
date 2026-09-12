"""Server configuration: precedence, validation, and actionable errors."""

import json
from pathlib import Path
import tempfile
import unittest

from rb3dashboard.runtime.config import ConfigError, ServerConfig


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def write(self, payload, name="server.json"):
        path = self.directory / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def load(self, path=None, **environ):
        return ServerConfig.load(path=path, environ=environ)


class DefaultTests(ConfigTestCase):
    def test_defaults_are_usable_without_a_file_or_environment(self):
        config = self.load()

        self.assertEqual(config.listen_address, "0.0.0.0")
        self.assertEqual(config.web_port, 8321)
        self.assertFalse(config.stagekit_enabled)
        self.assertEqual(config.discovery_targets, [])
        self.assertEqual(config.auth_token, "")

    def test_the_web_port_does_not_collide_with_the_console(self):
        """TCP 21070 belongs to RB3Enhanced's own song list and web UI."""
        self.assertNotEqual(ServerConfig.load(environ={}).web_port, 21070)

    def test_discovery_is_off_by_default(self):
        """Only one process may own Pico discovery."""
        self.assertFalse(self.load().stagekit_enabled)

    def test_a_missing_default_file_is_not_an_error(self):
        config = ServerConfig.load(path=None, environ={})

        self.assertEqual(config.web_port, 8321)


class FileTests(ConfigTestCase):
    def test_values_are_read_from_a_json_file(self):
        path = self.write({"web_port": 9000, "stagekit_enabled": True,
                           "auth_token": "secret"})

        config = self.load(path=path)

        self.assertEqual(config.web_port, 9000)
        self.assertTrue(config.stagekit_enabled)
        self.assertEqual(config.auth_token, "secret")

    def test_a_missing_explicit_file_is_an_error(self):
        with self.assertRaises(ConfigError) as caught:
            self.load(path=self.directory / "absent.json")

        self.assertIn("absent.json", str(caught.exception))

    def test_malformed_json_names_the_file(self):
        path = self.directory / "broken.json"
        path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(ConfigError) as caught:
            self.load(path=path)

        self.assertIn("broken.json", str(caught.exception))

    def test_a_non_object_document_is_rejected(self):
        path = self.write([1, 2, 3])

        with self.assertRaises(ConfigError):
            self.load(path=path)

    def test_an_unknown_key_is_reported_rather_than_ignored(self):
        """A silently ignored typo looks exactly like a setting that did nothing."""
        path = self.write({"web_prot": 9000})

        with self.assertRaises(ConfigError) as caught:
            self.load(path=path)

        self.assertIn("web_prot", str(caught.exception))


class EnvironmentTests(ConfigTestCase):
    def test_environment_values_are_applied(self):
        config = self.load(RB3E_WEB_PORT="9100", RB3E_AUTH_TOKEN="from-env")

        self.assertEqual(config.web_port, 9100)
        self.assertEqual(config.auth_token, "from-env")

    def test_environment_overrides_the_file(self):
        path = self.write({"web_port": 9000})

        config = ServerConfig.load(path=path, environ={"RB3E_WEB_PORT": "9100"})

        self.assertEqual(config.web_port, 9100)

    def test_booleans_accept_common_spellings(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            self.assertTrue(self.load(RB3E_STAGEKIT_ENABLED=value).stagekit_enabled)
        for value in ("0", "false", "no", "off"):
            self.assertFalse(self.load(RB3E_STAGEKIT_ENABLED=value).stagekit_enabled)

    def test_an_unparsable_boolean_names_the_variable(self):
        with self.assertRaises(ConfigError) as caught:
            self.load(RB3E_STAGEKIT_ENABLED="maybe")

        self.assertIn("RB3E_STAGEKIT_ENABLED", str(caught.exception))

    def test_an_unparsable_number_names_the_variable(self):
        with self.assertRaises(ConfigError) as caught:
            self.load(RB3E_WEB_PORT="http")

        self.assertIn("RB3E_WEB_PORT", str(caught.exception))

    def test_lists_accept_comma_separated_values(self):
        config = self.load(RB3E_DISCOVERY_TARGETS="192.0.2.255, 198.51.100.255")

        self.assertEqual(config.discovery_targets,
                         ["192.0.2.255", "198.51.100.255"])

    def test_unrelated_environment_variables_are_ignored(self):
        config = self.load(PATH="/usr/bin", RB3E_NOT_A_SETTING="x")

        self.assertEqual(config.web_port, 8321)


class ValidationTests(ConfigTestCase):
    def test_ports_must_be_in_range(self):
        for port in ("0", "65536", "-1"):
            with self.assertRaises(ConfigError):
                self.load(RB3E_WEB_PORT=port)

    def test_timeouts_must_be_positive(self):
        with self.assertRaises(ConfigError):
            self.load(RB3E_CONSOLE_TIMEOUT="0")
        with self.assertRaises(ConfigError):
            self.load(RB3E_MAINTENANCE_INTERVAL="-1")

    def test_devices_cannot_be_forgotten_before_they_go_offline(self):
        with self.assertRaises(ConfigError) as caught:
            self.load(RB3E_DEVICE_OFFLINE_AFTER="30",
                      RB3E_DEVICE_FORGET_AFTER="10")

        self.assertIn("device_forget_after", str(caught.exception))

    def test_capacities_must_be_positive(self):
        with self.assertRaises(ConfigError):
            self.load(RB3E_QUEUE_CAPACITY="0")

    def test_bind_addresses_cannot_be_blank(self):
        with self.assertRaises(ConfigError):
            self.load(RB3E_LISTEN_ADDRESS="   ")

    def test_discovery_targets_reject_blank_entries(self):
        with self.assertRaises(ConfigError):
            self.load(RB3E_DISCOVERY_TARGETS="192.0.2.255,,198.51.100.255")

    def test_a_wrongly_typed_file_value_names_the_field(self):
        path = self.write({"web_port": "nine thousand"})

        with self.assertRaises(ConfigError) as caught:
            self.load(path=path)

        self.assertIn("web_port", str(caught.exception))


class DataDirectoryTests(ConfigTestCase):
    def test_the_data_directory_can_be_set_explicitly(self):
        config = self.load(RB3E_DATA_DIR=str(self.directory / "appdata"))

        self.assertEqual(Path(config.data_dir), self.directory / "appdata")

    def test_a_default_data_directory_is_chosen(self):
        self.assertTrue(str(self.load().data_dir))

    def test_loading_creates_no_directories(self):
        """Configuration is read-only; services create what they need."""
        target = self.directory / "appdata"
        self.load(RB3E_DATA_DIR=str(target))

        self.assertFalse(target.exists())


class ApplicationKwargsTests(ConfigTestCase):
    def test_kwargs_match_the_application_constructor(self):
        import inspect

        from rb3dashboard.runtime.server import ServerApplication

        kwargs = self.load().application_kwargs()
        accepted = inspect.signature(ServerApplication.__init__).parameters

        self.assertTrue(kwargs)
        for name in kwargs:
            self.assertIn(name, accepted)

    def test_kwargs_carry_configured_values(self):
        config = self.load(RB3E_STAGEKIT_ENABLED="true",
                           RB3E_QUEUE_CAPACITY="7",
                           RB3E_HA_WEBHOOK_URL="http://ha.example/hook")

        kwargs = config.application_kwargs()

        self.assertTrue(kwargs["stagekit_enabled"])
        self.assertEqual(kwargs["queue_capacity"], 7)
        self.assertEqual(kwargs["webhook_url"], "http://ha.example/hook")


class DescribeTests(ConfigTestCase):
    def test_a_summary_lists_effective_values_without_the_token(self):
        """The summary is logged at startup, so it must not leak the token."""
        config = self.load(RB3E_AUTH_TOKEN="super-secret")

        summary = config.describe()

        self.assertIn("web_port", summary)
        self.assertNotIn("super-secret", summary)

    def test_the_summary_says_whether_a_token_is_set(self):
        self.assertIn("auth_token=<set>",
                      self.load(RB3E_AUTH_TOKEN="x").describe())
        self.assertIn("auth_token=<unset>", self.load().describe())


if __name__ == "__main__":
    unittest.main()
