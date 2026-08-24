import { ArrowUp, Sparkle, Stop } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Button, Card, Input, Page, PageHeader } from "../components/ui";
import { ACCENTS } from "../lib/theme";

/**
 * AI 教研员对话原型（agent-product-design §10.1 Phase 1「前端对话原型」）。
 *
 * 对接 gateway v1：POST /auth/login → POST /rpc（thread/start、turn/start、
 * interrupt）→ GET /threads/{id}/events SSE 事件流。
 * 渲染按 FINDINGS F5/F8 实测形状：item/completed 里 agentMessage 文本在 item.text，
 * mcpToolCall 带 server/tool 字段；事件按 threadId 过滤。
 *
 * 原型边界（Phase 2 收敛）：网关地址/账号口令暂由本地状态输入（不接 sc 账号体系）；
 * 工具调用只显示摘要行，不展开参数与结果全文。
 */

type ChatItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; server: string; tool: string; readOnly: boolean }
  | { kind: "notice"; text: string };

const GW_KEY = "sc.gateway.base";

function loadGatewayBase(): string {
  return localStorage.getItem(GW_KEY) || `${window.location.protocol}//${window.location.hostname}:8100`;
}

export default function Assistant() {
  const [base, setBase] = useState(loadGatewayBase());
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<string>("");
  const [passInput, setPassInput] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);

  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const threadIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const login = async () => {
    setAuthError(null);
    try {
      const r = await fetch(`${base}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: user, password: passInput }),
      });
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
      const data = await r.json();
      setToken(data.token);
      localStorage.setItem(GW_KEY, base);
    } catch (e) {
      setAuthError((e as Error).message);
    }
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
            let ev: { method?: string; params?: Record<string, unknown>; result?: unknown };
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

  const handleEvent = useCallback((ev: { method?: string; params?: Record<string, unknown> }) => {
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
    } else if (ev.method === "error") {
      setItems((prev) => [...prev, { kind: "notice", text: String(params.message ?? "错误") }]);
      setBusy(false);
    }
  }, []);

  const send = async () => {
    const text = input.trim();
    if (!text || !token || busy) return;
    setInput("");
    setItems((prev) => [...prev, { kind: "user", text }]);
    setBusy(true);

    const rpc = async (method: string, params: Record<string, unknown>) => {
      const r = await fetch(`${base}/rpc`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ method, params }),
      });
      if (!r.ok) throw new Error(`${method}: ${(await r.json()).detail ?? r.status}`);
      return r.json();
    };

    try {
      if (!threadIdRef.current) {
        const started = await rpc("thread/start", { cwd: "/tmp", approvalPolicy: "never" });
        const tid =
          (started.result as { thread?: { id?: string } } | undefined)?.thread?.id ?? null;
        threadIdRef.current = tid;
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
      await fetch(`${base}/rpc`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          method: "turn/interrupt",
          params: { threadId: threadIdRef.current },
        }),
      });
    } catch {
      /* 中断失败不打断 UI */
    }
  };

  const newThread = () => {
    abortRef.current?.abort();
    threadIdRef.current = null;
    setItems([]);
    setBusy(false);
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);

  if (!token) {
    return (
      <Page accent={ACCENTS.dashboard}>
        <PageHeader
          title="AI 教研员"
          desc="基于班级真实数据的调查与研判助手（原型）"
        />
        <Card className="mx-auto max-w-md p-6">
          <div className="space-y-3">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-faint">网关地址</span>
              <Input value={base} onChange={(e) => setBase(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-faint">教师账号</span>
              <Input value={user} onChange={(e) => setUser(e.target.value)} autoComplete="username" />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-faint">口令</span>
              <Input
                type="password"
                value={passInput}
                onChange={(e) => setPassInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && login()}
                autoComplete="current-password"
              />
            </label>
            {authError && <p className="text-xs text-danger">{authError}</p>}
            <Button onClick={login} disabled={!user || !passInput} className="w-full justify-center">
              <Sparkle size={15} />
              连接
            </Button>
          </div>
        </Card>
      </Page>
    );
  }

  return (
    <Page accent={ACCENTS.dashboard}>
      <PageHeader
        title="AI 教研员"
        desc="回答基于工具查询的真实班级数据；结论请结合课堂实际判断"
        actions={
          <Button variant="secondary" onClick={newThread}>
            新对话
          </Button>
        }
      />

      <Card className="flex h-[calc(100vh-230px)] min-h-[420px] flex-col p-0">
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {items.length === 0 && (
            <div className="py-16 text-center text-sm text-ink-faint">
              试试问：「我们班最近情况怎么样？」「基础点掌握得如何？」
            </div>
          )}
          {items.map((it, i) => {
            if (it.kind === "user")
              return (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[80%] rounded-xl rounded-br-sm bg-accent px-4 py-2.5 text-sm text-white">
                    {it.text}
                  </div>
                </div>
              );
            if (it.kind === "assistant")
              return (
                <div key={i} className="max-w-[85%] whitespace-pre-wrap rounded-xl rounded-bl-sm bg-surface-2 px-4 py-2.5 text-sm leading-relaxed">
                  {it.text}
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
              <div key={i} className="rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-ink-soft">
                {it.text}
              </div>
            );
          })}
          {busy && (
            <div className="flex items-center gap-2 text-xs text-ink-faint">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" aria-hidden />
              正在思考…
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="border-t border-line p-3">
          <div className="flex items-center gap-2">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) send();
              }}
              placeholder={busy ? "教研员正在处理…" : "向教研员提问…"}
              disabled={busy}
            />
            {busy ? (
              <Button variant="secondary" onClick={interrupt} aria-label="中断">
                <Stop size={15} />
              </Button>
            ) : (
              <Button onClick={send} disabled={!input.trim()} aria-label="发送">
                <ArrowUp size={15} />
              </Button>
            )}
          </div>
        </div>
      </Card>
    </Page>
  );
}
