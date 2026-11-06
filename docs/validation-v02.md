# v0.2 验证记录

日期：2026-11-06（Asia/Shanghai）。

- Python 3.11：43 passed，2 条第三方依赖弃用提示。
- 实际 Uvicorn HTTP 演示：三条连续异常 → Incident → 持久任务 → 4 个工具 → 规则降级报告；相同请求重放返回相同 Trace。
- 停止并重新启动 Uvicorn 进程，原 Incident、遥测和 Trace 均可通过 HTTP 读取。
- 自动化验证：跨 SQLite 连接重复消息与任务领取、完整事务回滚、持续窗口/采样间隔/boot 边界、异常归并与恢复、无效/缺指标数据不消除机械故障、同请求幂等与冲突、容量限制、冻结快照、中断任务恢复次数、关闭服务重新排队、MQTT 持久化后才 ACK。
- docker compose config --quiet：配置校验通过。
- edgediag 0.2.0 wheel 构建通过。最终源代码还增加了缺指标不能作为恢复证据的回归用例；以仓库源代码及测试为本次交付。
- 本地 Docker daemon 未运行，未执行真实 Mosquitto/容器重投测试；MQTT 确认用替身验证。
- 未配置真实模型、未验证模型诊断准确率、未运行真实 MCU/RTOS。
- Python 3.12/Linux 由已有 CI 配置覆盖，本地未执行。

任务重跑是只读流程的至少一次尝试，模型调用可能重复计费。上述验证不代表设备命令 exactly-once 或生产 SLA。
