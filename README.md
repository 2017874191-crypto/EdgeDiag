# EdgeDiag

面向工业设备的边缘智能诊断平台。第一版提供：

```text
device simulator → MQTT → ingestion API → anomaly detection → diagnosis API
```

当前版本以协议、可靠接入、故障注入和可测试性为重点，设备模拟器与未来 STM32 固件共享同一套消息协议。

## Quick start

```powershell
docker compose up --build
```

启动后：

- API: http://localhost:8000/docs
- Health: http://localhost:8000/health
- MQTT: localhost:1883

运行模拟设备：

```powershell
python -m device_simulator --scenario bearing_fault --duration 30
```

## Scenarios

- `normal`
- `thermal_runaway`
- `bearing_fault`
- `sensor_invalid`
- `offline`

## Repository layout

```text
protocol/          Pydantic message contracts and MQTT topics
device_simulator/  Device behavior and fault injection
services/          FastAPI ingestion, state and diagnosis APIs
tests/             Protocol and service tests
deploy/             Local MQTT configuration
```

## Current boundary

The first release does not execute dangerous device operations and does not let an LLM directly publish MQTT messages. It establishes the deterministic telemetry, incident and diagnosis foundation before adding the Agent runtime.
