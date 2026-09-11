import { Archive, ArrowUp, List, PlusCircle, Stop, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ChatMarkdown } from "../components/Markdown";
import { Badge, Button, Card, Page, PageHeader } from "../components/ui";
import { getToken } from "../lib/auth";
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
 * - 工具调用只显示摘要行（Phase 2 边界保留）。
 */

type ChatItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; server: string; tool: string; readOnly: boolean }
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

/** thread/resume 回填的历史 item/turn（ThreadItem 子集）。 */
interface HistoryItem {
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

/** 思考强度中文短标（models.json 描述为英文；未知档位回退原值）。 */
const EFFORT_LABELS: Record<string, string> = {
  minimal: "最简",
  low: "快速",
  medium: "均衡",
  high: "深入",
  xhigh: "更深",
  max: "最深",
};

/** 空态建议（点击填入输入框）。 */
const SUGGESTIONS = [
  "我们班最近情况怎么样？",
  "基础点掌握得如何？",
  "上次考试暴露了哪些薄弱点？",
  "帮我起草一份学困生的干预建议",
];

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
        if (it.text) out.push({ kind: "assistant", text: it.text });
      } else if (it.type === "mcpToolCall") {
        out.push({
          kind: "tool",
          server: String(it.server ?? ""),
          tool: String(it.tool ?? ""),
          readOnly: Boolean(it.readOnlyHint),
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
  const threadIdRef = useRef<string | null>(null);
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

  const reloadThreads = useCallback(async () => {
    if (!token) return;
    try {
      const rows = ((await rpc("thread/list", { limit: 50, archived: false })) as {
        data?: ThreadRow[];
      } | null)?.data ?? [];
      setThreads([...rows].sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0)));
    } catch {
      /* 历史加载失败不打断对话 */
    }
  }, [rpc, token]);

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
      if (alive) void reloadThreads();
    })();
    return () => {
      alive = false;
    };
  }, [token, rpc, reloadThreads]);

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

  // SSE 订阅：登录后建立，按 threadId 过滤事件
  useEffect(() => {
    if (!token) return;
    const ctrl = new AbortController();
    (async () => {
      try {
        const resp = await fetch(`${base}/threads/x/events`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (!resp.ok || !resp.body) throw new Error(`SSE HTTP ${resp.status}`);
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf("\n\n")) !== -1) {
            const frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const line = frame.split("\n").find((l) => l.startsWith("data: "));
            if (!line) continue;
            let ev: { method?: string; params?: Record<string, unknown> };
            try {
              ev = JSON.parse(line.slice(6));
            } catch {
              continue;
            }
            handleEvent(ev);
          }
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          setItems((prev) => [...prev, { kind: "notice", text: `连接断开：${(e as Error).message}` }]);
        }
      }
    })();
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, base]);

  const handleEvent = useCallback(
    (ev: { method?: string; params?: Record<string, unknown> }) => {
      const params = ev.params ?? {};
      const evThread =
        (params.threadId as string) ?? (params.thread as { id?: string } | undefined)?.id ?? null;
      if (evThread && threadIdRef.current && evThread !== threadIdRef.current) return;

      if (ev.method === "item/completed") {
        const item = params.item as Record<string, unknown> | undefined;
        if (!item) return;
        const t = item.type ?? item.item_type;
        if (t === "agentMessage") {
          const text = String(item.text ?? "");
          if (text) setItems((prev) => [...prev, { kind: "assistant", text }]);
        } else if (t === "mcpToolCall") {
          setItems((prev) => [
            ...prev,
            {
              kind: "tool",
              server: String(item.server ?? ""),
              tool: String(item.tool ?? ""),
              readOnly: Boolean(item.readOnlyHint),
            },
          ]);
        } else if (t === "error" || item.error) {
          setItems((prev) => [...prev, { kind: "notice", text: `出错：${String(item.message ?? "")}` }]);
        }
      } else if (ev.method === "warning") {
        setItems((prev) => [...prev, { kind: "notice", text: String(params.message ?? "警告") }]);
      } else if (ev.method === "turn/completed") {
        setBusy(false);
        void reloadThreads();
      } else if (ev.method === "error") {
        setItems((prev) => [...prev, { kind: "notice", text: String(params.message ?? "错误") }]);
        setBusy(false);
      }
    },
    [reloadThreads],
  );

  const send = async () => {
    const text = input.trim();
    if (!text || !token || busy) return;
    setInput("");
    setItems((prev) => [...prev, { kind: "user", text }]);
    setBusy(true);

    try {
      if (!threadIdRef.current) {
        const started = (await rpc("thread/start", {
          cwd: "/tmp",
          approvalPolicy: "never",
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
      }); // 阻塞至 turn 完成；正文经 SSE 流式到达
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
    threadIdRef.current = null;
    setActiveId(null);
    setShowHistory(false);
    setItems([]);
    setBusy(false);
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);

  /** 会话历史列表（lg 侧栏与移动端抽屉共用；面板头由各壳自配）。 */
  const historyPanel = (
    <>
      <div className="flex-1 overflow-y-auto p-2">
        {threads.length === 0 && (
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
                  className="absolute right-1.5 top-2 rounded-md p-1 text-ink-faint opacity-0 transition-all hover:bg-danger-soft hover:text-danger group-hover:opacity-100"
                >
                  <Archive size={13} />
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </>
  );

  if (!token) {
    return (
      <Page accent={ACCENTS.knowledge}>
        <PageHeader
          title="AI 教研员"
          desc="基于班级真实数据的调查与研判助手（需教师/管理员登录）"
        />
        <Card className="mx-auto max-w-md p-6">
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
        desc="回答基于工具查询的真实班级数据；结论请结合课堂实际判断"
        actions={
          <>
            {/* 移动端历史入口 */}
            <Button variant="secondary" className="lg:hidden" onClick={() => setShowHistory(true)}>
              <List size={15} />
              历史
            </Button>
            {/* 模型选择（model/list 数据驱动；目录扩模型零代码） */}
            <select
              value={modelId}
              onChange={(e) => changeModel(e.target.value)}
              aria-label="模型"
              title={currentModel?.description || "模型"}
              className="max-w-[170px] rounded-full border border-line-strong bg-surface px-3 py-1.5 text-sm text-ink transition-colors focus:border-accent"
            >
              {models.length === 0 && <option value="">默认模型</option>}
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.displayName ?? m.id}
                </option>
              ))}
            </select>
            {/* 思考强度（当前模型 supported_reasoning_levels） */}
            <select
              value={effort}
              onChange={(e) => changeEffort(e.target.value)}
              aria-label="思考强度"
              title={
                efforts.find((x) => x.reasoningEffort === effort)?.description ?? "思考强度"
              }
              className="rounded-full border border-line-strong bg-surface px-3 py-1.5 text-sm text-ink transition-colors focus:border-accent"
            >
              {efforts.length === 0 && <option value="">默认</option>}
              {efforts.map((e) => (
                <option key={e.reasoningEffort} value={e.reasoningEffort}>
                  思考·{EFFORT_LABELS[e.reasoningEffort] ?? e.reasoningEffort}
                </option>
              ))}
            </select>
            <Button variant="secondary" onClick={newThread}>
              <PlusCircle size={15} />
              新对话
            </Button>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
        {/* 会话历史侧栏（lg+ 常驻） */}
        <Card className="hidden h-[calc(100vh-230px)] min-h-[420px] flex-col overflow-hidden p-0 lg:flex">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <p className="text-sm font-semibold">会话历史</p>
            <Badge tone="neutral">{threads.length}</Badge>
          </div>
          {historyPanel}
        </Card>

        {/* 对话区 */}
        <Card className="flex h-[calc(100vh-230px)] min-h-[420px] flex-col p-0">
          <div className="flex-1 space-y-4 overflow-y-auto p-5">
            {items.length === 0 && (
              <div className="flex flex-col items-center gap-4 py-14 text-center">
                <p className="text-sm text-ink-faint">
                  试试从这些问题开始——每一个都会真实查询班级数据：
                </p>
                <div className="flex flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => setInput(s)}
                      className="rounded-full border border-line bg-surface px-3.5 py-1.5 text-[13px] text-ink-soft transition-colors hover:border-accent/50 hover:text-accent"
                    >
                      {s}
                    </button>
                  ))}
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
              if (it.kind === "assistant")
                return (
                  <div
                    key={i}
                    className="max-w-[85%] rounded-xl rounded-bl-sm bg-surface-2 px-4 py-2.5 text-sm leading-relaxed"
                  >
                    <ChatMarkdown content={it.text} />
                  </div>
                );
              if (it.kind === "tool")
                return (
                  <div key={i} className="flex items-center gap-2 pl-1 text-xs text-ink-faint">
                    <Badge tone={it.readOnly ? "neutral" : "warn"}>
                      {it.server}/{it.tool}
                    </Badge>
                    <span>{it.readOnly ? "查询了班级数据" : "执行了操作"}</span>
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
                className="w-full resize-none rounded-md border border-line-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-faint transition-colors focus:border-accent"
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
          </div>
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
    </Page>
  );
}
