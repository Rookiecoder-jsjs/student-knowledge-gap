import {
  Archive,
  ArrowUp,
  ChartLineUp,
  CheckCircle,
  ClipboardText,
  Copy,
  Info,
  List,
  PlusCircle,
  Sparkle,
  Stop,
  X,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChatMarkdown } from "../components/Markdown";
import { Badge, Button, Card, Page, PageHeader, Select, Skeleton } from "../components/ui";
import { getToken, LAST_CLASS_KEY } from "../lib/auth";
import { listClasses } from "../lib/api";
import { ACCENTS } from "../lib/theme";

/**
 * AI 教研员工作台（agent-product-design §10.1；UI 重设计 2026-09-10）。
 *
 * 对接 gateway v1（/rpc 透传 app-server + SSE 事件流），全部能力零网关改动：
 * - 会话历史：`thread/list` → 侧栏（预览+相对时间）；点击 `thread/resume`
 *   回填历史（thread.turns[].items）；`thread/archive` 归档移除。
 * - 模型与思考强度：`model/list` 数据驱动（目录来自驱动 home 的 models.json），
 *   逐线程经 `thread/settings/update` 覆盖；选择记忆在 localStorage。
 * - 渲染按 FINDINGS F5/F8 实测形状：item/completed 里 agentMessage 文本在
 *   item.text，mcpToolCall 带 server/tool 字段；事件按 threadId 过滤。
 * - 流式（2026-09-11，runtime v2 协议实测形状）：item/started 建流式项 →
 *   item/agentMessage/delta 增量并入（itemId 对位）→ item/completed 以权威
 *   文本收口（无流式项时退回追加，SSE 重连安全）；thread/tokenUsage/updated
 *   驱动页脚用量徽标（total=本会话累计，last=上一轮）。
 * - 工具调用进行中先上「正在查询」行（item/started），完成态原摘要不变。
 * - 工具调用只显示摘要行（Phase 2 边界保留）。
 */

/** key = codex item id（item/started · completed · delta 三事件同源），流式项据此对位合并。 */
type ChatItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string; streaming?: boolean; key?: string }
  | { kind: "tool"; server: string; tool: string; readOnly: boolean; done: boolean; key?: string }
  | { kind: "notice"; text: string };

const GW_KEY = "sc.gateway.base";

/**
 * 网关地址：默认**同源**（空串 = 相对路径）。dev 由 vite 把 /rpc、/threads/* 代理到
 * 127.0.0.1:8100，生产由前端 nginx 反代到 gateway 容器（DEPLOY.md §8）。
 * 网关自身不配 CORS——浏览器必须同源访问，故这里不能回落到 `hostname:8100` 绝对地址。
 * localStorage `sc.gateway.base` 可覆盖为直连地址（本地调试用）。
 */
function loadGatewayBase(): string {
  return localStorage.getItem(GW_KEY) ?? "";
}

/** model/list 行（app-server v2 Model 的前端所需子集；camelCase）。 */
interface EffortOption {
  reasoningEffort: string;
  description?: string;
}
interface ModelInfo {
  id: string;
  displayName?: string;
  description?: string;
  hidden?: boolean;
  isDefault?: boolean;
  supportedReasoningEfforts?: EffortOption[];
  defaultReasoningEffort?: string;
}

/** thread/list 行（Thread 子集；时间为 Unix 秒）。 */
interface ThreadRow {
  id: string;
  name?: string | null;
  preview?: string;
  createdAt?: number;
  updatedAt?: number;
}

interface ClassScope {
  id: number;
  name: string;
  subject?: string;
}

/** thread/resume 回填的历史 item/turn（ThreadItem 子集）。 */
interface HistoryItem {
  id?: string;
  type?: string;
  text?: string;
  content?: { text?: string }[];
  server?: string;
  tool?: string;
  readOnlyHint?: boolean | null;
}
interface HistoryTurn {
  items?: HistoryItem[];
  error?: { message?: string } | null;
}

interface ServerRequest {
  id: number | string;
  method: string;
  params: Record<string, unknown>;
  kind: "approval" | "elicitation";
}

/** 思考强度中文短标（models.json 描述为英文；未知档位回退原值）。 */
const EFFORT_LABELS: Record<string, string> = {
  minimal: "最简",
  low: "快速",
  medium: "均衡",
  high: "深入",
  xhigh: "更深",
  max: "最深",
};

/** token 用量短标：万级以下原样（千分位），万级起 k 缩写（12345 → 12.3k）。 */
function fmtTokens(n: number): string {
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}k`;
  return n.toLocaleString("en-US");
}

/** 空态建议（点击填入输入框）。 */
const SUGGESTIONS = [
  { title: "看班级概况", hint: "最近的整体表现与待办", prompt: "我们班最近情况怎么样？", icon: ChartLineUp },
  { title: "找出薄弱点", hint: "基础点掌握得如何", prompt: "基础点掌握得如何？", icon: ClipboardText },
  { title: "复盘一次考试", hint: "哪些问题最值得先处理", prompt: "上次考试暴露了哪些薄弱点？", icon: Sparkle },
  { title: "起草干预建议", hint: "生成一份可修改的草稿", prompt: "帮我起草一份学困生的干预建议", icon: CheckCircle },
];

/** MCP 工具对教师只呈现业务动作，不暴露 server/tool 等实现细节。 */
const TOOL_LABELS: Record<string, string> = {
  get_class_overview: "读取班级概况",
  get_exam_summary: "复盘考试表现",
  get_kp_mastery: "分析知识点掌握度",
  get_kp_detail: "查看知识点关系",
  run_attribution: "追溯薄弱点原因",
  get_teaching_progress: "核对教学进度",
  get_student_progress: "对账干预进度",
  list_students: "核对学生范围",
  // MCP 注册名包含 _tool 后缀，必须与 item.mcpToolCall.tool 完全一致。
  create_report_draft_tool: "起草报告草稿",
  record_intervention_tool: "记录干预建议",
};

function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? "调用分析能力";
}

function relTime(ts?: number): string {
  if (!ts) return "";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 7 * 86400) return `${Math.floor(diff / 86400)} 天前`;
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}-${d.getDate()}`;
}

/** resume 回填：turns[].items → 聊天条目（reasoning 等内部项跳过）。 */
function turnsToItems(turns: HistoryTurn[]): ChatItem[] {
  const out: ChatItem[] = [];
  for (const turn of turns) {
    for (const it of turn.items ?? []) {
      if (it.type === "userMessage") {
        const text = (it.content ?? []).map((c) => c.text ?? "").join("").trim();
        if (text) out.push({ kind: "user", text });
      } else if (it.type === "agentMessage") {
        if (it.text) out.push({ kind: "assistant", text: it.text, key: it.id });
      } else if (it.type === "mcpToolCall") {
        out.push({
          kind: "tool",
          server: String(it.server ?? ""),
          tool: String(it.tool ?? ""),
          readOnly: Boolean(it.readOnlyHint),
          done: true, // 历史回填都是终态
          key: it.id,
        });
      } else if (it.type === "error") {
        out.push({ kind: "notice", text: `出错：${String(it.text ?? "")}` });
      }
    }
    if (turn.error?.message) out.push({ kind: "notice", text: `出错：${turn.error.message}` });
  }
  return out;
}

export default function Assistant() {
  // auth-roles-design 统一登录：直接复用 sc 工作台会话 token。
  const [base, setBase] = useState(loadGatewayBase());
  const [token] = useState<string | null>(() => getToken());

  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [serverRequest, setServerRequest] = useState<ServerRequest | null>(null);
  const [streamState, setStreamState] = useState<"connecting" | "connected" | "reconnecting">("connecting");
  const threadIdRef = useRef<string | null>(null);
  const turnSeqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 模型目录与选择（model/list；选择记忆在 localStorage）
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelId, setModelId] = useState("");
  const [effort, setEffort] = useState("");
  // 会话历史（thread/list）与移动端抽屉
  const [threads, setThreads] = useState<ThreadRow[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showContext, setShowContext] = useState(false);
  const [threadNextCursor, setThreadNextCursor] = useState<string | null>(null);
  const [threadLoading, setThreadLoading] = useState(false);
  const [threadError, setThreadError] = useState<string | null>(null);
  const threadCursorRef = useRef<string | null>(null);
  // token 用量（thread/tokenUsage/updated；total=本会话累计，last=上一轮）
  const [usage, setUsage] = useState<{ total: number; last: number } | null>(null);
  const [classScope, setClassScope] = useState<ClassScope | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const currentModel = models.find((m) => m.id === modelId) ?? models[0] ?? null;
  const efforts = currentModel?.supportedReasoningEfforts ?? [];

  const rpc = useCallback(
    async (method: string, params: Record<string, unknown>) => {
      if (!token) throw new Error("未登录");
      const r = await fetch(`${base}/rpc`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ method, params }),
        // turn/start 阻塞至回合完成（长分析可达数分钟）；上限 5 分钟防死等
        signal: AbortSignal.timeout(300_000),
      });
      const body = (await r.json().catch(() => ({}))) as {
        result?: unknown;
        detail?: string;
        error?: { message?: string };
      };
      if (!r.ok) {
        throw new Error(body.error?.message ?? body.detail ?? `${method} HTTP ${r.status}`);
      }
      return body.result;
    },
    [base, token],
  );

  const respondToServerRequest = useCallback(
    async (request: ServerRequest, result: object) => {
      if (!token) throw new Error("未登录");
      const r = await fetch(`${base}/rpc`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ id: request.id, result }),
        signal: AbortSignal.timeout(30_000),
      });
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? `响应审批请求失败（HTTP ${r.status}）`);
      }
    },
    [base, token],
  );

  const resolveServerRequest = useCallback(
    async (decision: "accept" | "decline" | "cancel") => {
      if (!serverRequest) return;
      try {
        const result =
          serverRequest.kind === "elicitation"
            ? {
                action: decision === "accept" ? "accept" : decision,
                content: decision === "accept" ? {} : null,
              }
            : { decision };
        await respondToServerRequest(serverRequest, result);
        setServerRequest(null);
      } catch (e) {
        setItems((prev) => [...prev, { kind: "notice", text: `审批响应失败：${(e as Error).message}` }]);
      }
    },
    [respondToServerRequest, serverRequest],
  );

  const loadThreads = useCallback(async (append = false) => {
    if (!token || (append && !threadCursorRef.current)) return;
    if (!append) threadCursorRef.current = null;
    setThreadLoading(true);
    setThreadError(null);
    try {
      const cursor = append ? threadCursorRef.current : null;
      const payload = (await rpc("thread/list", {
        limit: 50,
        archived: false,
        ...(cursor ? { cursor } : {}),
      })) as {
        data?: ThreadRow[];
        nextCursor?: string | null;
        next_cursor?: string | null;
      } | null;
      const rows = payload?.data ?? [];
      setThreads((prev) => {
        const merged = append ? [...prev, ...rows] : rows;
        const seen = new Set<string>();
        return merged
          .filter((t) => {
            if (seen.has(t.id)) return false;
            seen.add(t.id);
            return true;
          })
          .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0));
      });
      const next = payload?.nextCursor ?? payload?.next_cursor ?? null;
      threadCursorRef.current = next;
      setThreadNextCursor(next);
    } catch (e) {
      setThreadError((e as Error).message);
    } finally {
      setThreadLoading(false);
    }
  }, [rpc, token]);

  const reloadThreads = useCallback(() => loadThreads(false), [loadThreads]);
  const loadMoreThreads = useCallback(() => loadThreads(true), [loadThreads]);

  /**
   * turn/start 的 RPC 结果在网关侧会等到回合结束才返回。若 SSE 恰好尚未连上，
   * 浏览器收不到任何 item 事件，就会只看到自己发出的提问并一直停在处理中。
   * 回合结束后用 thread/resume 做一次幂等对账，既补齐漏掉的回答，也把流式残片
   * 收口成服务端的权威内容。历史 item 保留 id，迟到的 SSE 不会重复插入。
   */
  const reconcileThread = useCallback(
    async (tid: string, seq: number) => {
      try {
        const thread = ((await rpc("thread/resume", { threadId: tid })) as {
          thread?: { id?: string; turns?: HistoryTurn[] };
        } | null)?.thread;
        // 用户可能已快速发起下一轮；旧对账不能覆盖新轮次的本地消息/流式内容。
        if (threadIdRef.current !== tid || turnSeqRef.current !== seq) return;
        const restored = turnsToItems(thread?.turns ?? []);
        if (restored.length > 0) setItems(restored);
      } catch {
        // SSE 正常时无需对账；对账失败也不应覆盖已经收到的流式内容。
      }
    },
    [rpc],
  );

  // 与 Shell 的班级切换器对齐：上下文面板只读展示当前工作班级，不改变线程协议。
  const loadClassScope = useCallback(async () => {
    try {
      const rows = (await listClasses()).classes ?? [];
      const remembered = Number(localStorage.getItem(LAST_CLASS_KEY));
      const picked = rows.find((item) => item.class_id === remembered) ?? rows[0];
      if (picked) {
        setClassScope({ id: picked.class_id, name: picked.name, subject: picked.subject });
      }
    } catch {
      // 上下文只是辅助信息，加载失败不应打断对话主流程。
    }
  }, []);

  // 启动：模型目录（选择回放 localStorage）+ 会话历史
  useEffect(() => {
    if (!token) return;
    let alive = true;
    (async () => {
      try {
        const rows =
          ((await rpc("model/list", {})) as { data?: ModelInfo[] } | null)?.data ?? [];
        const visible = rows.filter((m) => !m.hidden);
        if (!alive) return;
        setModels(visible);
        const def = visible.find((m) => m.isDefault) ?? visible[0] ?? null;
        const storedModel = localStorage.getItem("sc.assistant.model");
        const picked = visible.find((m) => m.id === storedModel) ?? def;
        if (picked) {
          setModelId(picked.id);
          const level = picked.supportedReasoningEfforts ?? [];
          const storedEffort = localStorage.getItem("sc.assistant.effort");
          const eff = level.some((e) => e.reasoningEffort === storedEffort)
            ? (storedEffort as string)
            : (picked.defaultReasoningEffort ?? level[0]?.reasoningEffort ?? "");
          setEffort(eff);
        }
      } catch (e) {
        if (alive) {
          setItems((prev) => [...prev, { kind: "notice", text: `模型目录加载失败：${(e as Error).message}` }]);
        }
      }
      if (alive) {
        void reloadThreads();
        void loadClassScope();
      }
    })();
    return () => {
      alive = false;
    };
  }, [token, rpc, reloadThreads, loadClassScope]);

  // 逐线程设置（模型/思考强度）；无活动线程时仅记忆，新线程 start 后应用
  const applySettings = useCallback(
    async (mid: string, eff: string, announce: boolean) => {
      if (!threadIdRef.current || !mid) return;
      try {
        await rpc("thread/settings/update", { threadId: threadIdRef.current, model: mid, effort: eff });
        if (announce) setItems((prev) => [...prev, { kind: "notice", text: "模型设置已对本会话生效" }]);
      } catch (e) {
        setItems((prev) => [...prev, { kind: "notice", text: `设置失败：${(e as Error).message}` }]);
      }
    },
    [rpc],
  );

  const changeModel = (id: string) => {
    setModelId(id);
    localStorage.setItem("sc.assistant.model", id);
    const m = models.find((x) => x.id === id);
    if (m && !(m.supportedReasoningEfforts ?? []).some((e) => e.reasoningEffort === effort)) {
      const eff = m.defaultReasoningEffort ?? effort;
      setEffort(eff);
      localStorage.setItem("sc.assistant.effort", eff);
      void applySettings(id, eff, false);
      return;
    }
    void applySettings(id, effort, true);
  };

  const changeEffort = (e: string) => {
    setEffort(e);
    localStorage.setItem("sc.assistant.effort", e);
    void applySettings(modelId, e, true);
  };

  // SSE 订阅：登录后建立，按 threadId 过滤事件。连接意外结束时自动退避重连，
  // 避免一次网络抖动让后续所有回合都失去流式事件。
  useEffect(() => {
    if (!token) return;
    const ctrl = new AbortController();
    let retry = 0;
    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        let timer = 0;
        const finish = () => {
          window.clearTimeout(timer);
          ctrl.signal.removeEventListener("abort", finish);
          resolve();
        };
        timer = window.setTimeout(finish, ms);
        ctrl.signal.addEventListener("abort", finish, { once: true });
      });
    const connect = async () => {
      while (!ctrl.signal.aborted) {
        try {
          setStreamState(retry === 0 ? "connecting" : "reconnecting");
          const resp = await fetch(`${base}/threads/x/events`, {
            headers: { Authorization: `Bearer ${token}` },
            signal: ctrl.signal,
          });
          if (!resp.ok || !resp.body) throw new Error(`SSE HTTP ${resp.status}`);
          retry = 0;
          setStreamState("connected");
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buf = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done) throw new Error("SSE 连接已结束");
            buf += decoder.decode(value, { stream: true });
            let idx;
            while ((idx = buf.indexOf("\n\n")) !== -1) {
              const frame = buf.slice(0, idx);
              buf = buf.slice(idx + 2);
              const line = frame.split("\n").find((l) => l.startsWith("data: "));
              if (!line) continue;
              let ev: { id?: number | string; method?: string; params?: Record<string, unknown> };
              try {
                ev = JSON.parse(line.slice(6));
              } catch {
                continue;
              }
              handleEvent(ev);
            }
          }
        } catch (e) {
          if (ctrl.signal.aborted || (e as Error).name === "AbortError") return;
          setStreamState("reconnecting");
          await sleep(Math.min(1000 * 2 ** Math.min(retry, 3), 8000));
          retry += 1;
        }
      }
    };
    void connect();
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, base]);

  const handleEvent = useCallback(
    (ev: { id?: number | string; method?: string; params?: Record<string, unknown> }) => {
      const params = ev.params ?? {};
      const evThread =
        (params.threadId as string) ?? (params.thread as { id?: string } | undefined)?.id ?? null;
      if (evThread && threadIdRef.current && evThread !== threadIdRef.current) return;

      // server-initiated approval 必须回传原 request id；否则 app-server 会一直
      // 等待，前端表现为回合卡住。审批在对话区内完成，写入工具仍保持教师终审。
      if (
        ev.id != null &&
        (ev.method === "item/commandExecution/requestApproval" ||
          ev.method === "item/fileChange/requestApproval" ||
          ev.method === "item/permissions/requestApproval" ||
          ev.method?.endsWith("/requestApproval"))
      ) {
        setServerRequest({
          id: ev.id,
          method: ev.method ?? "requestApproval",
          params,
          kind: "approval",
        });
        return;
      }
      if (ev.id != null && ev.method === "mcpServer/elicitation/request") {
        setServerRequest({ id: ev.id, method: ev.method, params, kind: "elicitation" });
        return;
      }
      // 当前工作台暂不支持结构化追问；明确返回空答案，让回合收到可读失败而非
      // 永久等待。后续增加表单时可复用同一 server-request 回传通道。
      if (ev.id != null && ev.method === "item/tool/requestUserInput") {
        void respondToServerRequest(
          { id: ev.id, method: ev.method, params, kind: "elicitation" },
          { answers: {} },
        );
        setItems((prev) => [...prev, { kind: "notice", text: "教研员请求补充信息，但当前界面暂不支持该表单，已跳过。" }]);
        return;
      }

      // 流式生命周期（key=codex item id）：started 建流式项 → delta 增量并入 →
      // completed 以权威文本收口。completed 兼容无 started 项（SSE 重连丢事件）：
      // 退回旧追加路径，不会重复（对位以 item id 为准）。
      if (ev.method === "item/started") {
        const item = params.item as Record<string, unknown> | undefined;
        if (!item) return;
        const t = item.type ?? item.item_type;
        const id = String(item.id ?? "");
        // userMessage 不在此渲染：发送方已本地先行上屏，started 再画会重复
        if (t === "agentMessage") {
          setItems((prev) =>
            prev.some((it) => it.kind === "assistant" && it.key === id)
              ? prev
              : [...prev, { kind: "assistant", text: "", streaming: true, key: id }],
          );
        } else if (t === "mcpToolCall") {
          setItems((prev) =>
            prev.some((it) => it.kind === "tool" && it.key === id)
              ? prev
              : [
                  ...prev,
                  {
                    kind: "tool",
                    server: String(item.server ?? ""),
                    tool: String(item.tool ?? ""),
                    readOnly: Boolean(item.readOnlyHint),
                    done: false,
                    key: id,
                  },
                ],
          );
        }
      } else if (ev.method === "item/agentMessage/delta") {
        const itemId = String(params.itemId ?? "");
        const delta = String(params.delta ?? "");
        if (!delta) return;
        setItems((prev) => {
          let idx = itemId
            ? prev.findIndex((it) => it.kind === "assistant" && it.key === itemId)
            : -1;
          if (idx < 0 && !itemId) {
            // 协议上 delta 必带 itemId；缺了就并入最后一个流式项兜底
            for (let i = prev.length - 1; i >= 0; i -= 1) {
              const it = prev[i];
              if (it.kind === "assistant" && it.streaming) {
                idx = i;
                break;
              }
            }
          }
          if (idx < 0) {
            // started 缺失（重连等）：以 delta 自带 itemId 立项
            return [...prev, { kind: "assistant", text: delta, streaming: true, key: itemId }];
          }
          const cur = prev[idx];
          if (cur.kind !== "assistant") return prev;
          // 对账已用服务端终态收口时，忽略随后迟到的增量，避免正文重复。
          if (!cur.streaming) return prev;
          const next = [...prev];
          next[idx] = { ...cur, text: cur.text + delta };
          return next;
        });
      } else if (ev.method === "item/completed") {
        const item = params.item as Record<string, unknown> | undefined;
        if (!item) return;
        const t = item.type ?? item.item_type;
        const id = String(item.id ?? "");
        if (t === "agentMessage") {
          const text = String(item.text ?? "");
          setItems((prev) => {
            const idx = prev.findIndex((it) => it.kind === "assistant" && it.key === id);
            if (idx < 0) {
              return text ? [...prev, { kind: "assistant", text }] : prev;
            }
            const cur = prev[idx];
            if (cur.kind !== "assistant") return prev;
            const next = [...prev];
            // 完成文本权威；空完成文本退回保留已流式收到的部分
            next[idx] = { kind: "assistant", text: text || cur.text, key: id };
            return next;
          });
        } else if (t === "mcpToolCall") {
          const row: ChatItem = {
            kind: "tool",
            server: String(item.server ?? ""),
            tool: String(item.tool ?? ""),
            readOnly: Boolean(item.readOnlyHint),
            done: true,
            key: id,
          };
          setItems((prev) => {
            const idx = prev.findIndex((it) => it.kind === "tool" && it.key === id);
            if (idx < 0) return [...prev, row];
            const next = [...prev];
            next[idx] = row;
            return next;
          });
        } else if (t === "error" || item.error) {
          setItems((prev) => [...prev, { kind: "notice", text: `出错：${String(item.message ?? "")}` }]);
        }
      } else if (ev.method === "thread/tokenUsage/updated") {
        // 形状（runtime v2 thread.rs）：tokenUsage.{total,last}.totalTokens
        const tu = params.tokenUsage as
          | { total?: { totalTokens?: number }; last?: { totalTokens?: number } }
          | undefined;
        const total = tu?.total?.totalTokens ?? 0;
        if (total) setUsage({ total, last: tu?.last?.totalTokens ?? 0 });
      } else if (ev.method === "warning") {
        setItems((prev) => [...prev, { kind: "notice", text: String(params.message ?? "警告") }]);
      } else if (ev.method === "turn/completed") {
        setServerRequest(null);
        setBusy(false);
        void reloadThreads();
      } else if (ev.method === "error") {
        setServerRequest(null);
        setItems((prev) => [...prev, { kind: "notice", text: String(params.message ?? "错误") }]);
        setBusy(false);
      }
    },
    [reloadThreads, respondToServerRequest],
  );

  const send = async () => {
    const text = input.trim();
    if (!text || !token || busy) return;
    const turnSeq = turnSeqRef.current + 1;
    turnSeqRef.current = turnSeq;
    setInput("");
    setItems((prev) => [...prev, { kind: "user", text }]);
    setBusy(true);

    try {
      if (!threadIdRef.current) {
        const started = (await rpc("thread/start", {
          cwd: "/tmp",
          approvalPolicy: "on-request",
        })) as { thread?: { id?: string } } | null;
        const tid = started?.thread?.id ?? null;
        threadIdRef.current = tid;
        setActiveId(tid);
        // 新线程带上当前模型与思考强度（失败不阻断对话）
        if (tid && currentModel) {
          try {
            await rpc("thread/settings/update", {
              threadId: tid,
              model: currentModel.id,
              effort,
            });
          } catch {
            /* 设置失败不阻断 */
          }
        }
      }
      await rpc("turn/start", {
        threadId: threadIdRef.current,
        input: [{ type: "text", text }],
        approvalPolicy: "on-request",
      }); // 阻塞至 turn 完成；正文经 SSE 流式到达
      const tid = threadIdRef.current;
      if (tid) {
        // 不依赖 turn/completed 通知来解除 busy；通知丢失时也能正常收口。
        setBusy(false);
        void reconcileThread(tid, turnSeq);
      }
    } catch (e) {
      setItems((prev) => [...prev, { kind: "notice", text: (e as Error).message }]);
      setBusy(false);
    }
  };

  const interrupt = async () => {
    if (!token || !threadIdRef.current) return;
    try {
      await rpc("turn/interrupt", { threadId: threadIdRef.current });
    } catch {
      /* 中断失败不打断 UI */
    }
  };

  const resumeThread = async (t: ThreadRow) => {
    if (busy) {
      setItems((prev) => [...prev, { kind: "notice", text: "当前回合进行中，请先中断或等待完成" }]);
      return;
    }
    turnSeqRef.current += 1;
    try {
      const thread = ((await rpc("thread/resume", { threadId: t.id })) as {
        thread?: { id?: string; turns?: HistoryTurn[] };
      } | null)?.thread;
      const tid = thread?.id ?? t.id;
      threadIdRef.current = tid;
      setActiveId(tid);
      setShowHistory(false);
      const restored = turnsToItems(thread?.turns ?? []);
      setItems(
        restored.length > 0
          ? restored
          : [{ kind: "notice", text: "该会话暂无可展示的历史内容，可以直接继续提问" }],
      );
    } catch (e) {
      setItems((prev) => [...prev, { kind: "notice", text: `恢复会话失败：${(e as Error).message}` }]);
    }
  };

  const archiveThread = async (t: ThreadRow) => {
    try {
      await rpc("thread/archive", { threadId: t.id });
    } catch (e) {
      setItems((prev) => [...prev, { kind: "notice", text: `归档失败：${(e as Error).message}` }]);
      return;
    }
    if (activeId === t.id) newThread();
    void reloadThreads();
  };

  const newThread = () => {
    abortRef.current?.abort();
    turnSeqRef.current += 1;
    threadIdRef.current = null;
    setActiveId(null);
    setShowHistory(false);
    setShowContext(false);
    setItems([]);
    setBusy(false);
    setUsage(null); // 新会话无累计；用量随下一个 tokenUsage 通知重建
  };

  const copyMessage = async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey((current) => (current === key ? null : current)), 1600);
    } catch {
      // 剪贴板权限被浏览器拒绝时保持无感，不影响继续对话。
    }
  };

  useEffect(() => {
    // 流式增量期间用瞬时跟随——smooth 动画被高频重置会抖动
    const last = items[items.length - 1];
    const streaming = last !== undefined && last.kind === "assistant" && Boolean(last.streaming);
    bottomRef.current?.scrollIntoView({ behavior: streaming ? "auto" : "smooth" });
  }, [items]);

  /** 会话历史列表（lg 侧栏与移动端抽屉共用；面板头由各壳自配）。 */
  const historyPanel = (
    <>
      <div className="flex-1 overflow-y-auto p-2">
        {threadLoading && threads.length === 0 && <Skeleton rows={3} />}
        {threadError && (
          <div className="mx-2 mb-2 flex items-center justify-between gap-2 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-xs text-danger" role="alert">
            <span className="min-w-0 truncate">历史会话加载失败：{threadError}</span>
            <Button size="sm" variant="secondary" onClick={() => void reloadThreads()}>
              重试
            </Button>
          </div>
        )}
        {threads.length === 0 && !threadLoading && !threadError && (
          <p className="px-3 py-8 text-center text-xs leading-relaxed text-ink-faint">
            暂无历史会话。
            <br />
            发起第一段对话后，这里会按时间列出全部会话。
          </p>
        )}
        <ul className="space-y-1">
          {threads.map((t) => {
            const active = t.id === activeId;
            return (
              <li key={t.id} className="group relative">
                <button
                  onClick={() => void resumeThread(t)}
                  className={`w-full rounded-lg px-3 py-2.5 pr-8 text-left transition-colors ${
                    active ? "bg-accent-soft/70" : "hover:bg-surface-2"
                  }`}
                >
                  <p
                    className={`truncate text-[13px] leading-snug ${
                      active ? "font-medium text-accent-deep" : "text-ink"
                    }`}
                  >
                    {t.name || t.preview || "未命名会话"}
                  </p>
                  <p className="mt-0.5 text-[11px] text-ink-faint">{relTime(t.updatedAt ?? t.createdAt)}</p>
                </button>
                <button
                  onClick={() => void archiveThread(t)}
                  aria-label="归档会话"
                  title="归档（从列表移除）"
                  className="absolute right-1.5 top-2 rounded-md p-1 text-ink-faint opacity-0 transition-[background-color,color,opacity] hover:bg-danger-soft hover:text-danger group-hover:opacity-100"
                >
                  <Archive size={13} />
                </button>
              </li>
            );
          })}
        </ul>
        {threadNextCursor && (
          <Button
            size="sm"
            variant="ghost"
            className="mt-2 w-full"
            onClick={() => void loadMoreThreads()}
            disabled={threadLoading}
          >
            {threadLoading ? "加载中…" : "加载更多"}
          </Button>
        )}
      </div>
    </>
  );

  const activeThread = threads.find((thread) => thread.id === activeId) ?? null;
  const contextPanel = (
    <div className="flex h-full flex-col">
      <div className="border-b border-line px-4 py-3">
        <p className="text-sm font-semibold text-ink">会话信息</p>
        <p className="mt-0.5 text-xs text-ink-faint">让每次研判都在清晰的范围内进行</p>
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
        <section>
          <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.14em] text-ink-faint">当前范围</p>
          <div className="rounded-xl border border-line bg-surface-2/60 p-3">
            <p className="text-sm font-semibold text-ink">{classScope?.name ?? "当前工作班级"}</p>
            <p className="mt-1 text-xs leading-relaxed text-ink-soft">
              {classScope?.subject ? `${classScope.subject} · ` : ""}只读取已提交的教学数据
            </p>
          </div>
        </section>
        <section>
          <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.14em] text-ink-faint">这位教研员可以</p>
          <ul className="space-y-2 text-xs leading-relaxed text-ink-soft">
            <li className="flex gap-2"><ChartLineUp size={15} className="mt-0.5 shrink-0 text-accent" />看懂班级与考试表现</li>
            <li className="flex gap-2"><ClipboardText size={15} className="mt-0.5 shrink-0 text-accent" />沿知识点关系追溯原因</li>
            <li className="flex gap-2"><Sparkle size={15} className="mt-0.5 shrink-0 text-accent" />起草可修改的教学建议</li>
          </ul>
        </section>
        <section className="rounded-xl border border-accent/20 bg-accent-soft/45 p-3">
          <div className="flex items-start gap-2">
            <Info size={16} className="mt-0.5 shrink-0 text-accent-deep" />
            <p className="text-xs leading-relaxed text-ink-soft">
              结论会附带数据依据；涉及对外发布或写入记录时，请前往待签发确认。
            </p>
          </div>
        </section>
        <div className="space-y-1 border-t border-line pt-3">
          {classScope && (
            <Link className="flex items-center justify-between rounded-lg px-2 py-2 text-xs text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink" to={`/c/${classScope.id}`}>
              回到班级工作台 <span aria-hidden>→</span>
            </Link>
          )}
          <Link className="flex items-center justify-between rounded-lg px-2 py-2 text-xs text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink" to="/inbox">
            查看待签发 <span aria-hidden>→</span>
          </Link>
          <Link className="flex items-center justify-between rounded-lg px-2 py-2 text-xs text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink" to="/kb">
            查看知识库 <span aria-hidden>→</span>
          </Link>
        </div>
      </div>
      <div className="border-t border-line px-4 py-3 text-[11px] text-ink-faint">
        {activeThread ? "当前会话会持续保留在历史列表中" : "新会话将在发送第一条消息后建立"}
      </div>
    </div>
  );

  if (!token) {
    return (
      <Page accent={ACCENTS.knowledge}>
        <PageHeader
          title="AI 教研员"
          desc="基于班级真实数据的调查与研判助手（需教师/管理员登录）"
        />
        <Card className="mx-auto max-w-md p-4">
          <div className="space-y-3">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-faint">网关地址</span>
              <input
                value={base}
                onChange={(e) => setBase(e.target.value)}
                className="w-full rounded-md border border-line-strong bg-surface px-3 py-2 text-sm text-ink transition-colors focus:border-accent"
              />
            </label>
            <p className="text-xs leading-relaxed text-ink-faint">
              未检测到登录会话。统一登录后可直接对话——先在登录页以教师/管理员身份
              登录，再回到本页即可（无需再次输入口令）。
            </p>
          </div>
        </Card>
      </Page>
    );
  }

  return (
    <Page accent={ACCENTS.knowledge}>
      <PageHeader
        title="AI 教研员"
        desc="围绕班级数据提问，和一位熟悉你班级的教研员一起做判断"
        actions={
          <>
            {/* 移动端历史入口 */}
            <Button variant="secondary" className="lg:hidden" onClick={() => setShowHistory(true)}>
              <List size={15} />
              历史
            </Button>
            <Button variant="ghost" size="sm" className="2xl:hidden" onClick={() => setShowContext(true)}>
              <Info size={15} />
              会话信息
            </Button>
            {/* 模型选择（model/list 数据驱动；目录扩模型零代码） */}
            <Select
              size="sm"
              value={modelId}
              onChange={(e) => changeModel(e.target.value)}
              aria-label="模型"
              title={currentModel?.description || "模型"}
              className="w-44"
            >
              {models.length === 0 && <option value="">默认模型</option>}
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.displayName ?? m.id}
                </option>
              ))}
            </Select>
            {/* 思考强度（当前模型 supported_reasoning_levels） */}
            <Select
              size="sm"
              value={effort}
              onChange={(e) => changeEffort(e.target.value)}
              aria-label="思考强度"
              title={
                efforts.find((x) => x.reasoningEffort === effort)?.description ?? "思考强度"
              }
              className="w-36"
            >
              {efforts.length === 0 && <option value="">默认</option>}
              {efforts.map((e) => (
                <option key={e.reasoningEffort} value={e.reasoningEffort}>
                  思考·{EFFORT_LABELS[e.reasoningEffort] ?? e.reasoningEffort}
                </option>
              ))}
            </Select>
            <Button variant="secondary" onClick={newThread}>
              <PlusCircle size={15} />
              新对话
            </Button>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)] 2xl:grid-cols-[240px_minmax(0,1fr)_240px]">
        {/* 会话历史侧栏（lg+ 常驻） */}
        <Card className="hidden h-[calc(100vh-230px)] min-h-[520px] flex-col overflow-hidden p-0 lg:flex">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <div>
              <p className="text-sm font-semibold">会话</p>
              <p className="mt-0.5 text-[11px] text-ink-faint">按最近活动排序</p>
            </div>
            <div className="flex items-center gap-1.5">
              <Badge tone="neutral">{threads.length}</Badge>
              <button
                type="button"
                onClick={newThread}
                aria-label="新建会话"
                title="新建会话"
                className="rounded-lg p-1.5 text-ink-faint transition-colors hover:bg-accent-soft hover:text-accent-deep"
              >
                <PlusCircle size={16} />
              </button>
            </div>
          </div>
          {historyPanel}
        </Card>

        {/* 对话区 */}
        <Card className="flex h-[calc(100vh-230px)] min-h-[520px] flex-col overflow-hidden p-0">
          <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-ink">
                {activeThread?.name || activeThread?.preview || "新会话"}
              </p>
              <p className="mt-0.5 truncate text-[11px] text-ink-faint">
                {classScope?.name ? `${classScope.name} · ` : ""}数据研判工作区
              </p>
            </div>
            <Badge tone={streamState === "connected" ? "accent" : "warn"}>
              {busy ? "处理中" : streamState === "connected" ? "已连接" : "重连中"}
            </Badge>
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto p-4 md:p-6">
            {items.length === 0 && (
              <div className="mx-auto flex max-w-2xl flex-col items-center gap-5 py-12 text-center md:py-16">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-accent-soft text-accent-deep">
                  <Sparkle size={21} weight="fill" />
                </div>
                <div>
                  <p className="text-base font-semibold text-ink">从一个教学问题开始</p>
                  <p className="mt-1 text-sm text-ink-faint">
                    每个问题都会基于真实班级数据展开调查，并说明判断依据。
                  </p>
                </div>
                <div className="grid w-full gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map(({ title, hint, prompt, icon: Icon }) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => setInput(prompt)}
                      className="group flex items-start gap-3 rounded-xl border border-line bg-surface px-3.5 py-3 text-left transition-[border-color,background-color,box-shadow,transform] duration-150 hover:-translate-y-px hover:border-accent/45 hover:bg-accent-soft/30 hover:shadow-soft active:translate-y-0"
                    >
                      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-deep">
                        <Icon size={15} weight="fill" />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-[13px] font-semibold text-ink">{title}</span>
                        <span className="mt-0.5 block truncate text-xs text-ink-faint">{hint}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            {serverRequest && (
              <div className="rounded-xl border border-warn/40 bg-warn/10 p-4" role="alert">
                <div className="flex items-start gap-3">
                  <Info size={18} className="mt-0.5 shrink-0 text-warn" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-ink">需要教师确认</p>
                    <p className="mt-1 text-xs leading-relaxed text-ink-soft">
                      {String(
                        serverRequest.params.reason ??
                          serverRequest.params.message ??
                          (serverRequest.params.meta as { codex_tool_title?: string } | undefined)
                            ?.codex_tool_title ??
                          "教研员准备执行一项需要确认的操作",
                      )}
                    </p>
                    {!!serverRequest.params.command && (
                      <code className="mt-2 block max-h-28 overflow-auto whitespace-pre-wrap rounded-lg bg-surface px-2.5 py-2 text-[11px] text-ink-soft">
                        {String(serverRequest.params.command)}
                      </code>
                    )}
                    {!!serverRequest.params.cwd && (
                      <p className="mt-1 truncate text-[11px] text-ink-faint">
                        目录：{String(serverRequest.params.cwd)}
                      </p>
                    )}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button size="sm" onClick={() => void resolveServerRequest("accept")}>
                        允许继续
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => void resolveServerRequest("decline")}>
                        拒绝
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            )}
            {items.map((it, i) => {
              if (it.kind === "user")
                return (
                  <div key={i} className="flex justify-end">
                    <div className="max-w-[80%] whitespace-pre-wrap rounded-xl rounded-br-sm bg-accent px-4 py-2.5 text-sm text-white">
                      {it.text}
                    </div>
                  </div>
                );
              if (it.kind === "assistant") {
                // 流式且尚无正文：气泡内打点，先占位后填字
                const waiting = it.streaming && !it.text;
                const messageKey = it.key ?? `assistant-${i}`;
                return (
                  <div key={messageKey} className="group max-w-[85%]">
                    <div className="rounded-xl rounded-bl-sm bg-surface-2 px-4 py-2.5 text-sm leading-relaxed">
                      {waiting ? (
                        <span className="flex items-center gap-1 py-0.5" aria-label="正在输出">
                          {[0, 1, 2].map((d) => (
                            <span
                              key={d}
                              className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent/60"
                              style={{ animationDelay: `${d * 150}ms` }}
                            />
                          ))}
                        </span>
                      ) : (
                        <ChatMarkdown content={it.text} />
                      )}
                    </div>
                    {!waiting && it.text && (
                      <div className="mt-1 flex items-center gap-1 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100">
                        <button
                          type="button"
                          onClick={() => void copyMessage(it.text, messageKey)}
                          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-ink-faint transition-colors hover:bg-surface-2 hover:text-ink"
                          aria-label="复制回答"
                        >
                          <Copy size={12} />
                          {copiedKey === messageKey ? "已复制" : "复制"}
                        </button>
                      </div>
                    )}
                  </div>
                );
              }
              if (it.kind === "tool")
                return (
                  <div
                    key={it.key ?? i}
                    className="flex flex-wrap items-center gap-2 pl-1 text-xs text-ink-faint"
                  >
                    <Badge tone={it.readOnly ? "neutral" : "warn"}>{toolLabel(it.tool)}</Badge>
                    {it.done ? (
                      <span className="inline-flex items-center gap-1">
                        <CheckCircle size={13} className="text-success" weight="fill" />
                        {it.readOnly ? "依据已读取" : "草稿已准备"}
                      </span>
                    ) : (
                      <span className="flex items-center gap-1.5">
                        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent/60" aria-hidden />
                        {it.readOnly ? "正在读取数据…" : "正在准备草稿…"}
                      </span>
                    )}
                  </div>
                );
              return (
                <div
                  key={i}
                  className="rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-ink-soft"
                >
                  {it.text}
                </div>
              );
            })}
            {busy && (
              <div className="flex items-center gap-2 text-xs text-ink-faint">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" aria-hidden />
                正在思考{effort ? `（${EFFORT_LABELS[effort] ?? effort}）` : ""}…
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="border-t border-line p-3">
            <div className="flex items-end gap-2">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    void send();
                  }
                }}
                placeholder={busy ? "教研员正在处理…" : "向教研员提问…（Enter 发送 · Shift+Enter 换行）"}
                disabled={busy}
                rows={2}
                className="w-full resize-none rounded-xl border border-line-strong bg-surface px-3 py-2.5 text-sm text-ink placeholder:text-ink-faint transition-colors focus:border-accent"
              />
              {busy ? (
                <Button variant="secondary" onClick={interrupt} aria-label="中断">
                  <Stop size={15} />
                </Button>
              ) : (
                <Button onClick={() => void send()} disabled={!input.trim()} aria-label="发送">
                  <ArrowUp size={15} />
                </Button>
              )}
            </div>
            <p className="mt-2 flex items-center justify-between gap-3 text-[11px] text-ink-faint">
              <span className="truncate">分析范围：{classScope?.name ?? "当前工作班级"}</span>
              <span className="shrink-0">只读分析 · Enter 发送</span>
            </p>
            {/* token 用量徽标（thread/tokenUsage/updated 驱动；流式回合内实时增长） */}
            {usage && (
              <p className="mt-1.5 text-right text-[11px] tabular-nums text-ink-faint">
                本会话 {fmtTokens(usage.total)} tokens · 上轮 {fmtTokens(usage.last)}
              </p>
            )}
          </div>
        </Card>

        {/* 会话上下文（2xl 常驻；窄屏通过“会话信息”按钮打开抽屉） */}
        <Card className="hidden h-[calc(100vh-230px)] min-h-[520px] overflow-hidden p-0 2xl:block">
          {contextPanel}
        </Card>
      </div>

      {/* 移动端历史抽屉 */}
      {showHistory && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setShowHistory(false)}
            aria-hidden
          />
          <div className="absolute inset-y-0 left-0 flex w-72 flex-col bg-surface shadow-lift">
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <p className="text-sm font-semibold">会话历史</p>
              <button
                onClick={() => setShowHistory(false)}
                aria-label="关闭"
                className="rounded-md p-1 text-ink-faint hover:bg-surface-2 hover:text-ink"
              >
                <X size={16} />
              </button>
            </div>
            {historyPanel}
          </div>
        </div>
      )}

      {/* 移动端会话信息抽屉 */}
      {showContext && (
        <div className="fixed inset-0 z-50 2xl:hidden">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setShowContext(false)}
            aria-hidden
          />
          <div className="absolute inset-y-0 right-0 flex w-80 max-w-[90vw] flex-col bg-surface shadow-lift">
            <div className="flex items-center justify-end border-b border-line px-3 py-2">
              <button
                type="button"
                onClick={() => setShowContext(false)}
                aria-label="关闭会话信息"
                className="rounded-lg p-1.5 text-ink-faint transition-colors hover:bg-surface-2 hover:text-ink"
              >
                <X size={16} />
              </button>
            </div>
            {contextPanel}
          </div>
        </div>
      )}
    </Page>
  );
}
