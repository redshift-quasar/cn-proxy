SERVICES = {
    "web": {
        "port": 8001,
        "response":
            "Web service: request received successfully.",
    },

    "database": {
        "port": 8002,
        "response":
            "Database service: request received successfully.",
    },

    "file": {
        "port": 8003,
        "response":
            "File service: request received successfully.",
    },
}


def response_for_port(port):

    for service in SERVICES.values():

        if service["port"] == port:
            return service["response"]

    return "Unknown simulated service."