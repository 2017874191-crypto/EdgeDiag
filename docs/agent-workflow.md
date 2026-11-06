# 第一版迭代：工具注册与诊断工作流

## 本次能力

单个 DeviceDiagnosticAgent 使用冻结的设备/Incident 快照执行诊断。工具契约包含 Pydantic 参数 Schema、只读权限、超时和可重试属性。执行边界借鉴 DiAn AgentToolSpec 与 Orbit ToolRegistry。

流程：

1. 创建 trace_id，将 running 记录写入 SQLite。
2. 收集 Incident、设备状态、温度统计；模型模式再收集合成手册证据。
3. rules 模式返回 degraded 规则建议；anthropic 模式进入工具调用循环。
4. 模型可以调用注册的只读工具，结果以 tool_result 回传。
5. 校验报告 Schema、evidence_id 和 document_id，生成 Diagnosis。
6. 完成或降级后持久化诊断和 Trace；取消与进程中断分别记录 cancelled/interrupted。

工具列表：get_incident_context、get_device_status、query_telemetry、calculate_signal_features、search_device_manual。状态工具展示最后样本，不声称证明设备在线。统计只使用有效样本；对已聚合 vibration_rms 再求 RMS 不代表原始波形分析。

## 边界与预算

- 单个 API 进程；HTTP 请求通过持久任务与回执复用诊断。每次执行最多 60 秒，参见 [v0.2 任务语义](persistence-and-jobs.md)。
- 模型最多 6 次调用、12 次追加工具调用；不含模型模式前置 4 次证据采集。规则模式仅前置 3 次，不调用模型。
- 每个工具默认 2 秒，仅超时/连接故障允许一次安全重试。
- 模型请求有传输与运行时超时；模型不可用、输出非法、引用不存在或预算耗尽时降级。
- 模型不能修改设备 ID、Incident ID 或告警等级，也不能调用写工具。
- 检查建议全部要求人员确认；未提供设备命令和审批执行接口。
- confidence=0 表示根因置信度未校准；规则检测器原有分值不是根因概率。
- 校验引用存在，不证明模型引用与每句话语义一致；需后续人工/评测验证。
- 手册为合成演示资料，采用词项匹配；不是向量检索或真实厂商手册。无匹配返回空数组。
- Trace 保存调用参数、结果、耗时、错误类型和模型 usage；不存 API key 或原始异常。
- SQLite 持久化遥测、去重键、Incident、任务与诊断记录。原执行 Trace 中断时标记 interrupted，外层只读任务可以根据冻结快照重跑，最多 3 次。
- 当前无自动 MQTT 事件触发诊断、诊断下发、持久 Outbox、命令 ACK 闭环；不报告消息交付成功。
- MQTT 仍是本机匿名开发配置，Compose 端口绑定 127.0.0.1；TLS/ACL 与鉴权待下一阶段。

## 复用依据

按已有源码契约实现了针对设备诊断的精简版本，并未整体引入 Orbit/DiAn 的运行依赖。

- DiAn 47a866ce5118f3f7b4df83c714d2a706c0b0d6a5：
  [AgentToolSpec](https://github.com/2017874191-crypto/DiAn/blob/47a866ce5118f3f7b4df83c714d2a706c0b0d6a5/backend/agents/tools.py)
- Orbit 9a54d24c27d63401afd27a071e5eff72b1b63211：
  [ToolRegistry](https://github.com/2017874191-crypto/Orbit/blob/9a54d24c27d63401afd27a071e5eff72b1b63211/pico/agent/tools/registry.py)、
  [Tracing](https://github.com/2017874191-crypto/Orbit/blob/9a54d24c27d63401afd27a071e5eff72b1b63211/pico/tracing/spans.py)
- 模型适配遵循 [Anthropic Messages 工具协议](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)；使用 httpx，API key 和模型必须显式配置。
- Mosquitto 和 paho-mqtt 继续承担消息协议传输。

## 下一批验收缺口

MQTT 事件自动提交诊断、MQTT Outbox/设备 ACK、TLS/ACL、模型与 Broker 真实联调、Zephyr Native/STM32。持久化与请求幂等已经落地，完整 V1 验收尚未完成。
