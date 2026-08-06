# Traffic Meter（方案 A）

可选本地流量统计层：面板仍粘贴上游代理（如 cliproxy），勾选「启用流量统计」后，任务启动时自动套一层 `127.0.0.1` 计数代理再转发上游。

- 默认关闭，不影响复制粘贴习惯
- 数据：`/opt/automyai/data/traffic_meter/sessions.jsonl`
- OpenAI3：`/openai3/api/traffic`
- Grok TTK：`/api/grok/ttk/traffic`（需登录）

统计为代理隧道字节（含 CONNECT 开销），与商家后台账单可能有小幅差异。
