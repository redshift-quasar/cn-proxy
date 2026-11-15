from pathlib import Path
import threading
import yaml

try:
    from .protection import parse_settings
except ImportError:
    from protection import parse_settings


class PolicyLoader(yaml.SafeLoader):
    """Reject duplicate keys rather than silently overwriting a policy."""


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


PolicyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)

VALID_ACTIONS = {"ALLOW", "DENY", "AUTO"}


class PolicyEngine:
    """Loads ALLOW/DENY rules from YAML and hot-reloads them when the file changes.

    - Actions: ALLOW (forward, no inspection), DENY (block), AUTO (forward,
      then inspect the response and block/redact it if it is sensitive).
    - Unknown (service, port) pairs get `default_action` (default DENY).
    - If the file becomes invalid at runtime, the last good rules are kept.
    - Listeners registered with add_listener() are called as
      listener(service, port, old_action, new_action) whenever a rule is
      added, removed or changed. Member 3 can hook the SDN integration
      layer here (old/new action is None for added/removed rules).
    """

    def __init__(self, config_path=None, logger=None):

        if config_path is None:
            config_path = (
                Path(__file__).resolve().parents[1]
                / "config"
                / "policies.yaml"
            )

        self.config_path = Path(config_path)
        self.logger = logger

        self._lock = threading.RLock()
        self._mtime_ns = None
        self._rules = {}
        self._settings = {"default_action": "DENY", "on_sensitive": "block"}
        self._listeners = []

        # First load must succeed, otherwise fail loudly at startup.
        self._load(initial=True)

    # ---------- listeners ----------
    def add_listener(self, callback):
        with self._lock:
            self._listeners.append(callback)

    # ---------- loading ----------
    @staticmethod
    def _parse(data):

        data = {} if data is None else data

        if not isinstance(data, dict):
            raise ValueError("Policy file must be a mapping")

        if set(data) - {"default_action", "inspection", "rules", "protection"}:
            raise ValueError("Unknown policy setting")

        default_action = str(data.get("default_action", "deny")).upper()

        if default_action not in VALID_ACTIONS:
            raise ValueError("default_action must be ALLOW, DENY or AUTO")

        inspection = data.get("inspection", {})

        if not isinstance(inspection, dict):
            raise ValueError("'inspection' must be a mapping")

        if set(inspection) - {"on_sensitive"}:
            raise ValueError("Unknown inspection setting")

        on_sensitive = str(inspection.get("on_sensitive", "block")).lower()

        if on_sensitive not in {"block", "redact"}:
            raise ValueError("inspection.on_sensitive must be block or redact")

        rules = data.get("rules", [])

        if not isinstance(rules, list):
            raise ValueError("'rules' must be a list")

        new_rules = {}

        for index, rule in enumerate(rules):

            if not isinstance(rule, dict):
                raise ValueError(f"Rule #{index + 1} must be a mapping")

            if set(rule) != {"service", "port", "action"}:
                raise ValueError(f"Rule #{index + 1}: expected service, port and action")

            service = str(rule.get("service", "")).strip().lower()
            action = str(rule.get("action", "")).strip().upper()

            if not service:
                raise ValueError(f"Rule #{index + 1}: missing service")

            port = rule.get("port")
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError(f"Rule #{index + 1}: port must be an integer between 1 and 65535")
            if (service, port) in new_rules:
                raise ValueError(f"Rule #{index + 1}: duplicate service/port")

            if action not in VALID_ACTIONS:
                raise ValueError(
                    f"Rule #{index + 1}: action must be ALLOW, DENY or AUTO"
                )

            new_rules[(service, port)] = action

        return new_rules, {
            "default_action": default_action,
            "on_sensitive": on_sensitive,
            "protection": parse_settings(data.get("protection")),
        }

    def _load(self, initial=False):

        try:
            mtime_ns = self.config_path.stat().st_mtime_ns
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Policy file not found: {self.config_path}"
            )

        with self._lock:

            if not initial and mtime_ns == self._mtime_ns:
                return

            # Remember the mtime first so a broken file is reported once,
            # not on every single request.
            self._mtime_ns = mtime_ns

            with self.config_path.open("r", encoding="utf-8") as file:
                data = yaml.load(file, Loader=PolicyLoader)

            new_rules, new_settings = self._parse(data)

            old_settings = self._settings
            old_rules = self._rules
            self._rules = new_rules
            self._settings = new_settings

            if not initial:
                self._notify(old_rules, new_rules)
                if old_settings != new_settings and self.logger:
                    self.logger.info("POLICY_SETTINGS_CHANGED | %s", new_settings)

    def _reload_if_needed(self):

        try:
            self._load()

        except Exception as exc:  # keep serving with the last good rules
            if self.logger:
                self.logger.error(
                    "POLICY_RELOAD_FAILED | keeping previous rules | %s", exc
                )

    def _notify(self, old_rules, new_rules):

        for key in sorted(set(old_rules) | set(new_rules)):

            old = old_rules.get(key)
            new = new_rules.get(key)

            if old == new:
                continue

            if self.logger:
                self.logger.info(
                    "POLICY_CHANGED | service=%s | port=%s | %s -> %s",
                    key[0], key[1], old, new,
                )

            for callback in list(self._listeners):
                try:
                    callback(key[0], key[1], old, new)
                except Exception as exc:
                    if self.logger:
                        self.logger.error(
                            "POLICY_LISTENER_FAILED | %s", exc
                        )

    # ---------- public API ----------
    def evaluate(self, service, port):

        self._reload_if_needed()

        with self._lock:
            return self._rules.get(
                (str(service).lower(), int(port)),
                self._settings["default_action"],
            )

    def on_sensitive(self):
        """'block' or 'redact': what to do when AUTO inspection fires."""

        self._reload_if_needed()

        with self._lock:
            return self._settings["on_sensitive"]

    def rules(self):

        self._reload_if_needed()

        with self._lock:
            return dict(self._rules)

    def snapshot(self):
        """Return consistent copies of rules and settings for SDN consumers.

        Reloads even without client requests, including default_action changes
        which do not produce per-rule listener notifications.
        """
        self._reload_if_needed()

        with self._lock:
            settings = dict(self._settings)
            settings["protection"] = dict(settings["protection"])
            return dict(self._rules), settings
