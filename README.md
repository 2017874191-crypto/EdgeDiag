# EdgeDiag

面向设备异常诊断的工程项目。当前完成协议、MQTT 接入、基础规则检测，以及可追踪的 Agent 工具调用工作流。完整 V1 仍在开发；范围与限制见 [目标文档](docs/v1-scope-and-tradeoffs.md) 和 [当前实现](docs/agent-workflow.md)。

## 启动

需要 Python 3.11+；MQTT 环境通过 Docker Compose 提供。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
docker compose up --build
```

Linux 激活虚拟环境使用 `source .venv/bin/activate`。API 文档位于 http://localhost:8000/docs；MQTT 位于 localhost:1883。Compose 的本机匿名 MQTT 配置仅用于开发。

另开终端，激活环境后运行：

```powershell
python -m device_simulator --scenario bearing_fault --duration 30
python -m scripts.demo_diagnosis
```

演示脚本通过 HTTP 注入一条异常遥测、创建诊断并读取持久化 Trace。MQTT 模拟器负责验证消息接入；当前诊断需要通过 API 触发，尚未自动从 MQTT 事件调度。

## Agent 模式

默认 `AGENT_PROVIDER=rules` 无需模型账户，执行 4 个工具并返回 `status=degraded, mode=rules, fallback_reason=provider_disabled`。

复制 `.env.example` 为 `.env`，设置 `AGENT_PROVIDER=anthropic`、`ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL`；如使用兼容网关，同时配置 `ANTHROPIC_BASE_URL`。随后重新启动 Compose。

模型模式支持多轮 tool_use/tool_result，带设备范围限制、只读权限、超时重试、步数预算、结构化输出和引用检查。模型故障会返回规则降级报告。合成手册采用词项检索；真实模型效果尚待配置账户后验证。

## API

| 接口 | 用途 |
|---|---|
| POST /v1/telemetry | 遥测校验与去重 |
| GET /v1/devices/{device_id}/telemetry | 有界、按测量时间排序的历史 |
| GET /v1/incidents | 查看当前内存中的异常 |
| POST /v1/incidents/{id}/runs | 执行诊断，返回 trace_id、工具记录和报告 |
| GET /v1/runs/{trace_id} | 查询持久化诊断 |
| POST /v1/incidents/{id}/diagnose | 兼容接口，仅返回报告 |
| GET /v1/tools | 查看只读工具及参数 Schema |

## 验证

```powershell
python -m pytest -q
docker compose config --quiet
```

自动化测试覆盖工具越权、非法参数、超时/重试、模型调用协议、引用校验、模型降级、工作流取消、运行记录重启恢复、设备重启序列号和乱序历史。模型用可控替身测试，不把离线通过等同于真实模型质量。CI 在 Python 3.11/3.12 运行测试并构建安装包。

## 当前限制

遥测和 Incident 仍在内存中；SQLite 只持久化诊断记录和证据。当前规则为单点阈值，尚未加入持续窗口/告警归并。诊断结果尚未通过 MQTT 下发，命令 ACK、持久 Outbox、TLS/ACL、自动任务恢复和真实固件均待实现。运行单个 API worker。

## 目录

```text
protocol/          Pydantic 消息契约与 MQTT Topics
device_simulator/  遥测和故障场景模拟
services/          数据接入、规则检测、HTTP API
agent/             工具注册、知识检索、模型适配、诊断与运行记录
scripts/           演示入口
tests/             回归与失败路径测试
docs/              第一版基线和当前能力边界
deploy/            本机 MQTT 配置
```

实现参考与固定版本见 [复用依据](docs/agent-workflow.md#复用依据)。
