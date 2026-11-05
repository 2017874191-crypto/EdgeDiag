from collections import defaultdict, deque
from threading import Lock
from protocol.models import Incident, Telemetry


class StateStore:
    def __init__(self, history_size: int = 300):
        self._lock = Lock()
        self.telemetry: dict[str, deque[Telemetry]] = defaultdict(lambda: deque(maxlen=history_size))
        self.incidents: dict[str, Incident] = {}
        self.seen: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=1000))
        self.last_seen: dict[str, int] = {}

    def accept(self, message: Telemetry) -> bool:
        key = f"{message.boot_id}:{message.seq}"
        with self._lock:
            if message.seq in self.seen[message.device_id]:
                return False
            self.seen[message.device_id].append(message.seq)
            self.telemetry[message.device_id].append(message)
            self.last_seen[message.device_id] = message.timestamp_ms
            return True

    def history(self, device_id: str, limit: int = 100) -> list[Telemetry]:
        with self._lock:
            return list(self.telemetry.get(device_id, []))[-limit:]

    def add_incident(self, incident: Incident) -> Incident:
        with self._lock:
            self.incidents[incident.incident_id] = incident
        return incident

    def get_incident(self, incident_id: str) -> Incident | None:
        with self._lock:
            return self.incidents.get(incident_id)

