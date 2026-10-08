from pathlib import Path
import threading
import yaml


class PolicyEngine:

    def __init__(self, config_path=None):

        if config_path is None:
            config_path = (
                Path(__file__).resolve().parents[1]
                / "config"
                / "policies.yaml"
            )

        self.config_path = Path(config_path)

        self._lock = threading.RLock()
        self._mtime_ns = None
        self._rules = {}

        self._load_if_needed(force=True)

    def _load_if_needed(self, force=False):

        try:
            mtime_ns = self.config_path.stat().st_mtime_ns

        except FileNotFoundError:
            raise FileNotFoundError(
                f"Policy file not found: {self.config_path}"
            )

        with self._lock:

            if not force and mtime_ns == self._mtime_ns:
                return

            with self.config_path.open(
                "r",
                encoding="utf-8"
            ) as file:

                data = yaml.safe_load(file) or {}

            rules = data.get("rules", [])

            if not isinstance(rules, list):
                raise ValueError(
                    "'rules' must be a list"
                )

            new_rules = {}

            for rule in rules:

                service = str(
                    rule.get("service", "")
                ).strip().lower()

                action = str(
                    rule.get("action", "")
                ).strip().upper()

                port = int(rule.get("port"))

                if not service:
                    raise ValueError(
                        "Missing service"
                    )

                if action not in {"ALLOW", "DENY"}:
                    raise ValueError(
                        "Action must be ALLOW or DENY"
                    )

                new_rules[
                    (service, port)
                ] = action

            self._rules = new_rules
            self._mtime_ns = mtime_ns

    def evaluate(self, service, port):

        self._load_if_needed()

        with self._lock:

            return self._rules.get(
                (service.lower(), int(port)),
                "DENY"
            )

    def rules(self):

        self._load_if_needed()

        with self._lock:
            return dict(self._rules)