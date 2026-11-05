class Topics:
    @staticmethod
    def telemetry(device_id: str) -> str:
        return f"device/{device_id}/telemetry"

    @staticmethod
    def event(device_id: str) -> str:
        return f"device/{device_id}/event"

    @staticmethod
    def command(device_id: str) -> str:
        return f"device/{device_id}/command"

    @staticmethod
    def ack(device_id: str) -> str:
        return f"device/{device_id}/command/ack"

    @staticmethod
    def diagnosis(device_id: str) -> str:
        return f"device/{device_id}/diagnosis"
