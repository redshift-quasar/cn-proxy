MAX_REQUEST_BYTES = 1024


def parse_request(data: str) -> dict:
    """Parse 'service=<name>;port=<number>' into {'service': str, 'port': int}."""

    if not isinstance(data, str):
        raise ValueError("Request must be text")

    data = data.strip()

    if not data:
        raise ValueError("Empty request")

    fields = {}

    for part in data.split(";"):
        part = part.strip()

        if not part:
            continue

        if "=" not in part:
            raise ValueError(f"Invalid field: {part}")

        key, value = part.split("=", 1)

        key = key.strip().lower()
        value = value.strip()

        if key not in {"service", "port"}:
            raise ValueError(f"Unknown field: {key}")

        if not value:
            raise ValueError(f"Missing value for {key}")

        if key in fields:
            raise ValueError(f"Duplicate field: {key}")

        fields[key] = value

    if "service" not in fields or "port" not in fields:
        raise ValueError("Request must contain service and port")

    service = fields["service"].lower()

    try:
        port = int(fields["port"])
    except ValueError:
        raise ValueError("Port must be an integer")

    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")

    return {"service": service, "port": port}
