# Phase 0 验链发现记录（FINDINGS）

> 性质：验链过程中的**实证发现账本**——每条都经过本机实测或官方文档核对，
> 是 Phase 1 魔改的直接依据。与 ARCHITECTURE.md 的关系：那篇讲「上游是什么样」，
> 这篇讲「我们在锚点版本上实测到了什么、对设计有什么影响」。
> 维护纪律：随验链进展追加，编号递增不复用。

---

## F1 · MCP 工具的暴露形态：namespace 分组 + 独立字段调用

**实测环境**：codex-cli 0.149.1（npm 预编译，与锚点同版本），隔离 CODEX_HOME。

0.149.1 把每个 MCP server 的工具以 **namespace 分组**形态下发给模型：

```json
{"type": "namespace", "name": "mcp__sc", "tools": [{"type": "function", "name": "get_class_overview", ...}]}
```

模型回调时 function_call 用**独立的两个字段**（不是拼接名）：
`name="get_class_overview"` + `namespace="mcp__sc"`。

代码依据（锚点路径）：`codex-rs/tools/src/responses_api.rs:64`（namespace 分组）、
`codex-rs/core/src/tools/router.rs:154` `build_tool_call`（`ToolName::new(namespace, name)`）、
`codex-rs/codex-mcp/src/tools.rs:228` `callable_namespace_with_prefix`（默认加 `mcp__` 前缀，
可被 `non_prefixed_mcp_tool_servers` 配置豁免）。

**对魔改的影响**：网关/前端展示工具名时用拼接名 `mcp__sc__get_class_overview`
（`flat_tool_name` 语义）；给模型的工具调用必须拆回两字段。Phase 1 换人格 prompt 时
无需动这套命名——它是壳内自洽的。

## F2 · 只读工具免审批 = 一行 MCP annotations

壳对 MCP 工具的审批判据在 `codex-rs/core/src/mcp_tool_call.rs:2195`
`requires_mcp_tool_approval`：`destructive_hint=true` 必审批；
`read_only_hint=true` 直接免审批；两者皆无则按 approval_policy 走。
策略为 `never` 时未声明注解的工具会被拒绝执行并回喂错误文本
（`:1423`"MCP tool call requires approval, but approval policy is never"）。

sc MCP Server 的落地：FastMCP `@mcp.tool(annotations={"readOnlyHint": True})` 即自动放行。
**这正是设计文档 §5.3「只读工具自动放行」的原生机制，零核改即可用。**

## F3 · wire_api="chat" 已移除；国产模型走原生 Responses

锚点版本 `WireApi` 枚举只剩 `Responses`（`codex-rs/model-provider-info/src/lib.rs:63`），
配置写 `chat` 直接报错并指向上游 discussion #7782。上游自带的
`responses-api-proxy` 只是转发代理不做协议翻译。

**DeepSeek 官方已原生支持 Responses API**（2026-08-24 核对其官方文档）：
- 接入 Codex 指南：`https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/codex`
  （官方 config.toml 模板：`base_url="https://api.deepseek.com/"` + `wire_api="responses"`
  + `model_catalog_json=~/.codex/models.json`，含一键配置脚本）；
- function 工具、function_call/function_call_output 输入项均支持；SSE 与 OpenAI 同构；
- 无状态 API：`previous_response_id`/`store` 不支持 → response_id 书签续传必须本地实现
  （与设计文档 §5.10 注记方向一致，但**不需要协议翻译层了**，工作量大幅下降）；
- custom 工具仅支持 apply_patch（Codex 兼容特例）；并行工具调用恒开。

设计文档 §5.10/§6.2 的「Chat 兼容层」表述需按此修订。验链③脚本：
`scripts/verify_chain3_deepseek.py`（待 API key 实测）。

## F4 · 单进程多 thread Turn 真并发（验链② 实测通过）

同一 app-server 进程内两个 thread 各自 turn/start（mock 模型注入 3s 延迟）：
总耗时 ≈ 单 Turn 耗时（并发比 1.96≈2），mock 端两路请求完全交错；
两 thread 各自落独立 rollout JSONL。协议 v1 文档「一 Session 至多一 Task」的自述
不构成跨教师串行阻塞——**网关单进程直连方案可行，无需进程池**。

验证脚本：`scripts/verify_chain1.py --concurrent`。

## F5 · app-server 事件流形状（浏览器侧渲染依据）

经网关 WS 实测的通知面（`thread/start` + `turn/start` 一个完整 Turn）：
`thread/started` → `mcpServer/startupStatus/updated`(starting→ready) →
`warning`（未知 model 元数据时）→ `thread/status/changed` → `turn/started` →
`item/started` / `item/completed`（userMessage / mcpToolCall / agentMessage 三种 item 类型）
→ `thread/tokenUsage/updated` → `account/rateLimits/updated` → `turn/completed`。

关键形状：`item/completed` 的 params 里 agentMessage 文本在 **`item.text`**
（不是 content 数组）；mcpToolCall 带 `server`/`tool`/`readOnlyHint`/`result.content[].text`。
前端对话面板直接按此渲染。

## F6 · mock-educator 缺模型元数据只是 warning 不是错误

无 models.json 声明时壳回落 fallback metadata 并打 warning，Turn 照常完成。
DeepSeek 官方提供了现成 `models.json`（见 F3），Phase 1 接入时直接采用，
顺带消除该 warning 并获得正确的 context_window 等参数。

---

## 验链状态板

| # | 内容 | 状态 | 证据 |
|---|---|---|---|
| ① | MCP 链路（壳⇄MCP⇄sc 真实数据） | ✅ 通过 | `scripts/verify_chain1.py`，rollout 含真实班级数据 |
| ② | 单进程双教师并发 | ✅ 通过 | `--concurrent`，并发比 1.96 |
| ③ | 国产模型工具调用保真度 | ⏳ 待 key | 脚本就绪 `scripts/verify_chain3_deepseek.py`；文档情报见 F3 |
| 附 | 网关雏形（WS⇄stdio 翻译） | ✅ 通过 | `scripts/verify_gateway.py` |
