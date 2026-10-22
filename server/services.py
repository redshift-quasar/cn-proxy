# NOTE: all data below is fake test data (4111... is a standard test card).
SERVICES = {
    "web": {
        "port": 8001,
        "response": "Web service: request received successfully.",
    },
    "database": {
        "port": 8002,
        "response": (
            "Database service: request received successfully.\n"
            "id=1 | name=Asha Rao | card=4111 1111 1111 1111 "
            "| ssn=123-45-6789\n"
            "id=2 | name=Ravi Kumar | email=ravi@example.com "
            "| password=hunter2"
        ),
    },
    "file": {
        "port": 8003,
        "response": "File service: request received successfully.",
    },
}


def response_for_port(port):

    for service in SERVICES.values():

        if service["port"] == port:
            return service["response"]

    return "Unknown simulated service."
