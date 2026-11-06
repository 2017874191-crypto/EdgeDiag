from collections import defaultdict, deque
from threading import Lock
from protocol.models import Incident, Telemetry


class StateStore:
    def __init__(self, history_size: int = 300):
        self._lock = Lock()
        self.telemetry: dict[str, deque[Telemetry]] = defaultdict(lambda: deque(maxlen=history_size))
        self.incidents: dict[str, Incident] = {}
        self.seen: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=1000))
        self.last_seen: dict[str, int] = {}

    def accept(self, message: Telemetry) -> bool:
        key = f"{message.boot_id}:{message.seq}"
        with self._lock:
            if key in self.seen[message.device_id]:
                return False
            self.seen[message.device_id].append(key)
            self.telemetry[message.device_id].append(message)
            self.last_seen[message.device_id] = max(message.timestamp_ms, self.last_seen.get(message.device_id, 0))
            return True

    def history(self, device_id: str, limit: int = 100) -> list[Telemetry]:
        with self._lock:
            return sorted(self.telemetry.get(device_id, []), key=lambda m: m.timestamp_ms)[-limit:]

    def add_incident(self, incident: Incident) -> Incident:
        with self._lock:
            self.incidents[incident.incident_id] = incident
        return incident

    def get_incident(self, incident_id: str) -> Incident | None:
        with self._lock:
            return self.incidents.get(incident_id)

    def list_incidents(self) -> list[Incident]:
        with self._lock:
            return list(self.incidents.values())
