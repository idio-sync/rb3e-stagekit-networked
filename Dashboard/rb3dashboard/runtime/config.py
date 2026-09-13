"""Explicit server configuration from defaults, a JSON file, and the environment.

Precedence is defaults, then the JSON file, then environment variables, so a
container can override a mounted file without rewriting it. Every environment
variable is the field name in upper case with an ``RB3E_`` prefix.

Nothing here is inferred from the host. Bind addresses, broadcast targets, and
ports are stated explicitly, because the Stage 1 desktop guessed a /24 broadcast
from its default route and that guess does not survive a server with several
interfaces. The one inference that remains is opt-in: allow_inferred_discovery
restores the desktop's guess for anyone who wants it, and the server refuses to
start rather than guessing silently.

Unknown keys are rejected rather than ignored. A silently dropped typo behaves
exactly like a setting that had no effect, which is painful to diagnose after a
deployment.

Loading is read-only. It creates no directories and opens no socket; services
create what they need when they start.

This module must not import the desktop settings store. Server deployment and
desktop preferences are separate concerns with separate files.
"""

import json
import os
from pathlib import Path


ENV_PREFIX = "RB3E_"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """A configuration value is missing, malformed, or out of range."""


def _as_bool(value, where, name):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    raise ConfigError(
        "%s: %s must be a boolean such as true or false, not %r"
        % (where, name, value))


def _as_int(value, where, name):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ConfigError("%s: %s must be a whole number, not %r"
                          % (where, name, value))
    try:
        return int(str(value).strip())
    except ValueError:
        raise ConfigError("%s: %s must be a whole number, not %r"
                          % (where, name, value))


def _as_float(value, where, name):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ConfigError("%s: %s must be a number, not %r" % (where, name, value))
    try:
        return float(str(value).strip())
    except ValueError:
        raise ConfigError("%s: %s must be a number, not %r" % (where, name, value))


def _as_text(value, where, name):
    if not isinstance(value, str):
        raise ConfigError("%s: %s must be text, not %r" % (where, name, value))
    return value.strip()


def _as_list(value, where, name):
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")] if value.strip() else []
    elif isinstance(value, list):
        items = [_as_text(item, where, name) for item in value]
    else:
        raise ConfigError("%s: %s must be a list or comma separated text, not %r"
                          % (where, name, value))
    if any(not item for item in items):
        raise ConfigError("%s: %s must not contain blank entries" % (where, name))
    return items


# name -> (parser, default)
_FIELDS = {
    "listen_address": (_as_text, "0.0.0.0"),
    "telemetry_address": (_as_text, "0.0.0.0"),
    # Loopback by default: the API drives physical lights and the token
    # defaults to empty, so an unconfigured server must not be reachable from
    # the network. Deployments set this explicitly; see docs/deployment.md.
    "web_host": (_as_text, "127.0.0.1"),
    # TCP 21070 belongs to the console's own song list, song jump, and web UI,
    # so the dashboard server never claims that number by default.
    "web_port": (_as_int, 8321),
    "web_enabled": (_as_bool, True),
    "auth_token": (_as_text, ""),
    # Deliberately serving a tokenless API on a trusted network is allowed, but
    # only when it is said out loud. A real field rather than an ad-hoc
    # environment variable: _read_environment only reads names in _FIELDS,
    # _read_file rejects unknown keys, and _validate sees parsed values only.
    "allow_open_api": (_as_bool, False),
    "stagekit_enabled": (_as_bool, False),
    "discovery_targets": (_as_list, []),
    # Opt back into the desktop's default-route /24 guess. Off by default: on a
    # server with bridges and several interfaces the guess picks the wrong one,
    # and a Pico that never hears discovery looks exactly like a broken Pico.
    "allow_inferred_discovery": (_as_bool, False),
    "relay_targets": (_as_list, []),
    "discovery_enabled": (_as_bool, True),
    "discovery_interval": (_as_float, 5.0),
    "ha_webhook_url": (_as_text, ""),
    "console_address": (_as_text, ""),
    # Home Assistant over MQTT. Empty host leaves it off, and the webhook
    # stays available with no extra dependency.
    "mqtt_host": (_as_text, ""),
    "mqtt_port": (_as_int, 1883),
    "mqtt_username": (_as_text, ""),
    "mqtt_password": (_as_text, ""),
    "mqtt_base_topic": (_as_text, "rb3dashboard"),
    "mqtt_discovery_prefix": (_as_text, "homeassistant"),
    "mqtt_client_id": (_as_text, "rb3dashboard"),
    "history_enabled": (_as_bool, True),
    "webhook_capacity": (_as_int, 100),
    "webhook_retries": (_as_int, 2),
    "webhook_timeout": (_as_float, 5.0),
    "data_dir": (_as_text, ""),
    "device_offline_after": (_as_float, 10.0),
    "device_forget_after": (_as_float, 30.0),
    "console_timeout": (_as_float, 30.0),
    "maintenance_interval": (_as_float, 1.0),
    "queue_capacity": (_as_int, 1000),
    "command_rate": (_as_float, 10.0),
    "command_burst": (_as_float, 20.0),
    "log_capacity": (_as_int, 200),
}

_SECRET_FIELDS = {"auth_token", "mqtt_password"}


def _default_data_dir(environ):
    appdata = environ.get("APPDATA")
    if appdata:
        return str(Path(appdata) / "RB3Dashboard" / "server")
    return str(Path.home() / ".rb3dashboard" / "server")


_LOOPBACK_NAMES = {"localhost", "::1", "0:0:0:0:0:0:0:1"}


def _is_loopback(host):
    """Whether a bind address is reachable only from this machine.

    Accepts the whole 127.0.0.0/8 block, not just 127.0.0.1, because a
    container or a test may pick another address in it.
    """
    host = (host or "").strip().strip("[]").lower()
    if host in _LOOPBACK_NAMES:
        return True
    parts = host.split(".")
    if len(parts) == 4 and parts[0] == "127":
        return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)
    return False


class ServerConfig:
    """Validated, fully explicit server settings."""

    def __init__(self, **values):
        for name in _FIELDS:
            setattr(self, name, values[name])

    # -- loading --------------------------------------------------------

    @classmethod
    def load(cls, path=None, environ=None):
        """Build a configuration from defaults, an optional file, and the environment."""
        environ = os.environ if environ is None else environ
        values = {name: _copy_default(default)
                  for name, (_, default) in _FIELDS.items()}

        path = cls._resolve_path(path, environ)
        if path is not None:
            values.update(cls._read_file(path))

        values.update(cls._read_environment(environ))

        parsed = {}
        for name, (parse, _) in _FIELDS.items():
            value = values[name]
            where, raw = value if isinstance(value, _Sourced) else (None, value)
            if where is None:
                parsed[name] = raw
            else:
                parsed[name] = parse(raw, where, name)

        if not parsed["data_dir"]:
            parsed["data_dir"] = _default_data_dir(environ)

        cls._validate(parsed)
        return cls(**parsed)

    @staticmethod
    def _resolve_path(path, environ):
        if path is not None:
            return Path(path)
        named = environ.get(ENV_PREFIX + "CONFIG")
        return Path(named) if named else None

    @staticmethod
    def _read_file(path):
        where = "configuration file %s" % path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigError("%s could not be read: %s" % (where, error))
        try:
            document = json.loads(text)
        except ValueError as error:
            raise ConfigError("%s is not valid JSON: %s" % (where, error))
        if not isinstance(document, dict):
            raise ConfigError("%s must contain a JSON object" % where)
        unknown = sorted(set(document) - set(_FIELDS))
        if unknown:
            raise ConfigError(
                "%s has unknown settings: %s. Known settings are: %s"
                % (where, ", ".join(unknown), ", ".join(sorted(_FIELDS))))
        return {name: _Sourced(where, value) for name, value in document.items()}

    @staticmethod
    def _read_environment(environ):
        found = {}
        for name in _FIELDS:
            variable = ENV_PREFIX + name.upper()
            if variable in environ:
                found[name] = _Sourced("environment variable %s" % variable,
                                       environ[variable])
        return found

    @staticmethod
    def _validate(values):
        for name in ("web_port", "mqtt_port"):
            if not 1 <= values[name] <= 65535:
                raise ConfigError(
                    "%s must be between 1 and 65535, not %d" % (name, values[name]))
        for name in ("device_offline_after", "device_forget_after",
                     "console_timeout", "maintenance_interval",
                     "discovery_interval", "command_rate", "command_burst"):
            if values[name] <= 0:
                raise ConfigError("%s must be greater than zero, not %s"
                                  % (name, values[name]))
        if values["webhook_retries"] < 0:
            raise ConfigError("webhook_retries cannot be negative, not %d"
                              % values["webhook_retries"])
        if values["webhook_timeout"] <= 0:
            raise ConfigError("webhook_timeout must be greater than zero, not %s"
                              % values["webhook_timeout"])
        for name in ("queue_capacity", "log_capacity", "webhook_capacity"):
            if values[name] < 1:
                raise ConfigError("%s must be at least 1, not %d"
                                  % (name, values[name]))
        for name in ("listen_address", "telemetry_address", "web_host"):
            if not values[name]:
                raise ConfigError("%s must name an address to bind" % name)
        if values["device_forget_after"] < values["device_offline_after"]:
            raise ConfigError(
                "device_forget_after (%s) must not be less than "
                "device_offline_after (%s)"
                % (values["device_forget_after"], values["device_offline_after"]))
        if (values["web_enabled"] and not values["auth_token"]
                and not _is_loopback(values["web_host"])
                and not values["allow_open_api"]):
            raise ConfigError(
                "web_host is %r, which is reachable from the network, but "
                "auth_token is empty. Anything that can reach the port could "
                "drive your lights. Set auth_token (RB3E_AUTH_TOKEN), or bind "
                "127.0.0.1, or set allow_open_api if you really mean to serve "
                "an open API on a trusted network."
                % values["web_host"])
        if values["discovery_targets"] == ["auto"]:
            raise ConfigError(
                "discovery_targets must be broadcast addresses, not 'auto'. "
                "To use the inferred /24 from the default route, set "
                "allow_inferred_discovery instead.")
        if (values["stagekit_enabled"] and values["discovery_enabled"]
                and not values["discovery_targets"]
                and not values["allow_inferred_discovery"]):
            raise ConfigError(
                "stagekit_enabled is on, so this process owns Pico discovery, "
                "but discovery_targets is empty. Name the broadcast address of "
                "the network your Picos are on, for example "
                "RB3E_DISCOVERY_TARGETS=192.168.1.255. Set "
                "allow_inferred_discovery to guess a /24 from the default "
                "route instead, which is unreliable on a host with several "
                "interfaces.")

    # -- use ------------------------------------------------------------

    def as_dict(self):
        return {name: getattr(self, name) for name in sorted(_FIELDS)}

    def application_kwargs(self):
        """Return the subset of settings the application constructor accepts."""
        return {
            "stagekit_enabled": self.stagekit_enabled,
            "webhook_url": self.ha_webhook_url,
            "device_offline_after": self.device_offline_after,
            "device_forget_after": self.device_forget_after,
            "console_timeout": self.console_timeout,
            "maintenance_interval": self.maintenance_interval,
            "queue_capacity": self.queue_capacity,
            "discovery_targets": self.discovery_targets,
            "discovery_enabled": self.discovery_enabled,
            "discovery_interval": self.discovery_interval,
            "telemetry_address": self.telemetry_address,
            "web_enabled": self.web_enabled,
            "web_host": self.web_host,
            "web_port": self.web_port,
            "auth_token": self.auth_token,
            "console_address": self.console_address,
            "command_rate": self.command_rate,
            "command_burst": self.command_burst,
            "history_enabled": self.history_enabled,
            "data_dir": self.data_dir,
            "webhook_capacity": self.webhook_capacity,
            "webhook_retries": self.webhook_retries,
            "webhook_timeout": self.webhook_timeout,
            "relay_targets": self.relay_targets,
            "mqtt_host": self.mqtt_host,
            "mqtt_port": self.mqtt_port,
            "mqtt_username": self.mqtt_username,
            "mqtt_password": self.mqtt_password,
            "mqtt_base_topic": self.mqtt_base_topic,
            "mqtt_discovery_prefix": self.mqtt_discovery_prefix,
            "mqtt_client_id": self.mqtt_client_id,
        }

    def describe(self):
        """Return a one-line summary safe to log, with secrets withheld."""
        parts = []
        for name, value in self.as_dict().items():
            if name in _SECRET_FIELDS:
                parts.append("%s=%s" % (name, "<set>" if value else "<unset>"))
            else:
                parts.append("%s=%r" % (name, value))
        return " ".join(parts)


class _Sourced(tuple):
    """A raw value paired with where it came from, for error messages."""

    def __new__(cls, where, value):
        return super(_Sourced, cls).__new__(cls, (where, value))


def _copy_default(default):
    return list(default) if isinstance(default, list) else default
