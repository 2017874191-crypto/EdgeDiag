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

演示脚本通过 HTTP 注入连续 3 条异常遥测、创建诊断、重放相同请求并校验持久化 Trace。MQTT 模拟器负责验证消息接入；当前诊断仍需 API 提交，尚未自动从 MQTT 事件调度。

## Agent 模式

默认 `AGENT_PROVIDER=rules` 无需模型账户，执行 4 个工具并返回 `status=degraded, mode=rules, fallback_reason=provider_disabled`。

复制 `.env.example` 为 `.env`，设置 `AGENT_PROVIDER=anthropic`、`ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL`；如使用兼容网关，同时配置 `ANTHROPIC_BASE_URL`。随后重新启动 Compose。

模型模式支持多轮 tool_use/tool_result，带设备范围限制、只读权限、超时重试、步数预算、结构化输出和引用检查。模型故障会返回规则降级报告。合成手册采用词项检索；真实模型效果尚待配置账户后验证。

## API

| 接口 | 用途 |
|---|---|
| POST /v1/telemetry | 遥测校验与去重 |
| GET /v1/devices/{device_id}/telemetry | 有界、按测量时间排序的历史 |
| GET /v1/incidents | 查看持久化异常 |
| GET /v1/incidents/{id}/events | 查看异常开启、更新、恢复记录 |
| POST /v1/incidents/{id}/jobs | 提交有界持久任务，返回 202；支持 request_key 与 expected_revision |
| GET /v1/jobs/{job_id} | 查询排队、执行与结果 |
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

遥测、Incident、状态事件、任务快照与诊断记录均持久化到 SQLite。持续窗口规则与告警归并、幂等请求回执、2 个 worker/256 个待处理任务上限、只读任务中断恢复已经实现。详见[持久化与任务语义](docs/persistence-and-jobs.md)。

运行单个 API 进程。尚未提供 MQTT 事件自动提交诊断、诊断结果下发、设备命令 ACK、Outbox、TLS/ACL 和真实固件；设备时钟纠偏与存储保留策略也待补齐。

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
