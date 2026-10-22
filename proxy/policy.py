from pathlib import Path
import threading
import yaml


class PolicyEngine:
    """Loads ALLOW/DENY rules from YAML and hot-reloads them when the file changes.

    - Unknown (service, port) pairs are DENIED (default deny).
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

        rules = (data or {}).get("rules", [])

        if not isinstance(rules, list):
            raise ValueError("'rules' must be a list")

        new_rules = {}

        for index, rule in enumerate(rules):

            if not isinstance(rule, dict):
                raise ValueError(f"Rule #{index + 1} must be a mapping")

            service = str(rule.get("service", "")).strip().lower()
            action = str(rule.get("action", "")).strip().upper()

            if not service:
                raise ValueError(f"Rule #{index + 1}: missing service")

            try:
                port = int(rule.get("port"))
            except (TypeError, ValueError):
                raise ValueError(f"Rule #{index + 1}: invalid port")

            if not 1 <= port <= 65535:
                raise ValueError(f"Rule #{index + 1}: port out of range")

            if action not in {"ALLOW", "DENY"}:
                raise ValueError(
                    f"Rule #{index + 1}: action must be ALLOW or DENY"
                )

            new_rules[(service, port)] = action

        return new_rules

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
                data = yaml.safe_load(file)

            new_rules = self._parse(data)

            old_rules = self._rules
            self._rules = new_rules

            if not initial:
                self._notify(old_rules, new_rules)

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
            return self._rules.get((str(service).lower(), int(port)), "DENY")

    def rules(self):

        self._reload_if_needed()

        with self._lock:
            return dict(self._rules)
