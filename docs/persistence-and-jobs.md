# 持久化接入与诊断任务（v0.2）

## 已完成的闭环

HTTP / MQTT → SQLite 遥测事务 → 持续窗口检测 → Incident 归并与事件记录。
API 提交诊断 → SQLite 队列与请求回执 → 2 个只读诊断 worker → 原有 Agent Runtime → 持久化报告。

本次参考 EchoMind 的请求回执、案件事务、KeyedLocks/有界任务设计；领域写入和队列使用 SQLite 原子事务实现，未引入 Redis 运行依赖。

## 接入与异常语义

- 唯一键为 device_id + boot_id + seq，跨连接、跨服务重启去重。
- 相同唯一键、相同载荷返回 duplicate；不同载荷 HTTP 返回 409，MQTT 视为冲突消息丢弃并记录。
- HTTP 与 MQTT 统一调用 StateStore.ingest。插入遥测、更新测量时间水位、异常判定、Incident 更新、事件写入同事务提交。
- 迟到/同测量时间数据仍保存，但不在其接入时改写当前故障状态；后续新测量判定可读取已经保存的历史。
- 温度 >=80、振动 RMS >=1.5、联合温度 >=75 且振动 >=1.2；联合类型优先，单条数据不再同时建 3 个事件。
- 同一 boot 最近 3 个样本分类一致，跨度至少 2000ms，每个间隔大于 0 且不超过 5000ms，才形成持续异常或恢复。
- 传感器无效立即告警。无效测量不能证明既有机械故障恢复；此时传感器事件可与机械事件同时活动。
- 物理异常/恢复判定需要同时存在温度与振动指标；缺指标视为证据不足，不能作为正常或恢复证据。
- 同设备、同类型活动 Incident 复用 ID，增加 revision 和 occurrences。确认恢复后变为 resolved；恢复后复发创建新 ID。
- 设备重启会重新积累持续窗口。水位使用设备测量时间；时钟回退/未来时间纠偏尚未实现。
- confidence=0 表示规则分数与根因概率尚未校准，阈值为演示参数。

## 诊断请求幂等

提交：
POST /v1/incidents/{incident_id}/jobs，返回 202 和 job_id。
查询：
GET /v1/jobs/{job_id}。

可选请求体：
~~~json
{"request_key":"operator-request-001","expected_revision":1}
~~~

显式 request_key 绑定 incident_id、expected_revision、workflow_version。同 key 同参数重放同任务，即使 Incident 后来更新；同 key 不同参数 409。不同 key 表示明确的新诊断请求。

不传 key 时按 incident_id + Incident revision + workflow_version 复用任务。默认键的 revision 指检测器确认过的证据版本；新样本尚未形成稳定窗口时不会自动刷新版本。需要对当前最新历史重新分析时应使用新显式 key。

expected_revision 可用于提交前检查证据版本。首次提交时不匹配返回 409。已经创建的同 key 请求重试仍返回原回执。

Incident 与最多 300 条遥测在提交事务中冻结进任务快照。执行期间的新数据不会进入该任务。请求回执、任务状态和结果持久化，重启后可以重放。

旧 POST /runs、POST /diagnose 继续可用，内部提交同一持久任务并等待最多 65 秒。等待超时返回 504 和 job_id，任务继续执行，可轮询；浏览器断开不取消后台任务。

## 执行与恢复

- 默认 2 个 worker，最多 256 个 queued/running 任务；队列满返回 429 和 Retry-After，已有任务重放不占额外容量。
- SQLite 原子领取 queued → running，完成后写 completed/result。
- runtime 每次执行仍有 60 秒和模型/工具调用预算。
- 普通执行异常保存 failed，不用相同 key 隐式重试失败任务；需要显式新 key。
- 只读任务在进程重启/关闭后允许重新排队，最多领取 3 次；达到上限后标记 failed。
- 重启是基于提交时快照重新执行，不是恢复模型中间步骤。先前已发生的模型调用可能再次计费；不宣称模型 exactly-once。
- RunRepository 保留各次 Trace，任务 result 指向最终完成尝试的 Trace；失败尝试可留作审计。
- 本版单个 API 进程拥有数据库/worker，禁止多个应用实例同时执行启动恢复。多连接事务测试不等同多实例部署支持。

## MQTT 确认

接入客户端使用固定 ID、clean_session=false、manual_ack=true。只有事务成功（或明确丢弃非法消息）后才确认 QoS1。数据库异常不确认，连接恢复且 Broker 保留会话时可重投。

该机制不主动强制重连，数据库恢复后未确认消息的重新交付需要连接恢复等 Broker 重投条件。QoS0 没有交付保证。Mosquitto 配置启用持久化和独立数据卷，但尚未在本机实际 Docker Broker 上验证断电/重投。

此处是上行消息的 Broker 确认；设备命令执行 ACK、报告下发和 Outbox 仍未实现。

## 存储与运行

DOMAIN_DB 默认与 DIAGNOSTIC_DB 同目录，文件名 domain.sqlite3；Compose 显式挂载 /data/domain.sqlite3 与 /data/diagnostics.sqlite3。升级不会导入旧进程已丢失的内存遥测。

数据表：telemetry、devices、incidents、incident_events、diagnostic_jobs；独立 runs 表继续保存 Trace。当前没有数据保留/清理策略，长期部署需明确容量策略。

## 本次验收重点

持久化重启、跨连接消息/任务唯一约束、事务失败全部回滚、持续窗口/间隔/重启边界、告警归并与恢复、无效数据不消除机械故障、请求冲突、并发重复请求只得到同一 Trace、冻结快照、队列容量、中断任务恢复和恢复次数上限、写入后 MQTT ACK。

## 后续范围

MQTT 事件自动创建诊断任务、诊断结果 Outbox 与设备 ACK、TLS/ACL、身份权限、版本化语义检索、真实模型/真实 Broker 联调、Zephyr/STM32、在线状态检测。完整 V1 尚未验收完成。
