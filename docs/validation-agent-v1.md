# 本次验证记录

日期：2026-11-06（Asia/Shanghai）。

## 已执行

- Python 3.11：python -m pytest -q，25 passed，2 条依赖弃用提示。
- docker compose config --quiet：通过（只验证配置）。
- python -m pip wheel --no-deps . -w dist：构建成功；检查 wheel 内含 agent/manuals.json。
- 启动真实 Uvicorn 服务（本机 18000 端口、MQTT_ENABLED=false、rules 模式），运行 scripts.demo_diagnosis：
  HTTP 遥测接入 → Incident → 4 个工具 → degraded 诊断 → SQLite 写入 → 按 trace_id 再读，结果一致。演示服务已停止。
- git diff --check：通过。

## 尚未执行

- Docker 容器和真实 MQTT 联调：本机 Docker Desktop Linux daemon 未运行。
- 外部模型调用：未配置模型账户，本次使用可控 Provider 替身覆盖 tool_use/tool_result、错误、超时和非法引用。
- Python 3.12 / Linux：已添加 CI 配置，尚未在本地执行。
- 真实硬件、RTOS、TLS/ACL、设备控制 ACK：不在本次完成范围。

这些结果仅验证本次工作流和失败路径，不代表模型诊断准确率或完整 V1 验收。
