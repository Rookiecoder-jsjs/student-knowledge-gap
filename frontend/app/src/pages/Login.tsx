import {
  ArrowLeft,
  ChalkboardTeacher,
  ChatCircleDots,
  ShieldCheck,
  SignIn,
  Student,
} from "@phosphor-icons/react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button, Card, Input } from "../components/ui";
import { useAuth } from "../lib/AuthContext";
import { EXPIRED_KEY } from "../lib/auth";
import { landingFor } from "../lib/portal";
import { ACCENTS } from "../lib/theme";
import { PRODUCT_NAME, WEBSITE_URL } from "../lib/site";
import { BrandMark } from "../components/BrandMark";

/** 统一登录入口：原色几何品牌面与全站静帧主题共用令牌。 */

const ENDS = [
  {
    icon: ChalkboardTeacher,
    accent: ACCENTS.dashboard,
    title: "教师工作台",
    desc: "拍卷 → 诊断 → 签发 → 追踪，班级质量一览",
  },
  {
    icon: ChatCircleDots,
    accent: ACCENTS.knowledge,
    title: "AI 教研员",
    desc: "对话式教研——数据在侧，追问在旁",
  },
  {
    icon: Student,
    accent: ACCENTS.student,
    title: "学生门户",
    desc: "我的薄弱点 · 我的报告 · 我的改进单",
  },
] as const;

/** 与全站一致，静态呈现内容。保留 delay 调用兼容。 */
function Reveal({ className, children }: { delay?: number; className?: string; children: ReactNode }) {
  return <div className={className}>{children}</div>;
}

/** 知识点图谱线稿（装饰）：节点 + 连线的星座图，白线极淡，三枚模块色节点
 * 呼应背景光斑——产品灵魂（知识图谱 / 薄弱点溯源）的门面化陈述。 */
function GraphSketch() {
  const nodes: [number, number][] = [
    [40, 240],
    [120, 180],
    [210, 210],
    [380, 180],
    [60, 120],
    [340, 40],
    [90, 60],
    [200, 140],
  ];
  return (
    <svg
      viewBox="0 0 420 300"
      fill="none"
      aria-hidden
      className="pointer-events-none absolute -right-6 bottom-0 w-[72%]"
    >
      <g stroke="white" strokeOpacity="0.1">
        <path d="M40 240 L120 180 L210 210 L300 150 L380 180" />
        <path d="M120 180 L150 90 L250 60 L300 150" />
        <path d="M210 210 L250 60" />
        <path d="M60 120 L150 90" />
        <path d="M60 120 L40 240" />
        <path d="M250 60 L340 40" />
        <path d="M340 40 L380 180" />
        <path d="M300 150 L340 40" />
      </g>
      <g fill="white" fillOpacity="0.22">
        {nodes.map(([x, y]) => (
          <circle key={`${x}-${y}`} cx={x} cy={y} r="2.5" />
        ))}
      </g>
      {/* 模块色节点：teal / purple / blue 各一，呼应三团光斑 */}
      <circle cx="150" cy="90" r="4" fill={ACCENTS.dashboard} fillOpacity="0.55" />
      <circle cx="250" cy="60" r="4.5" fill={ACCENTS.knowledge} fillOpacity="0.5" />
      <circle cx="300" cy="150" r="4" fill={ACCENTS.student} fillOpacity="0.5" />
    </svg>
  );
}

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 401 踢出提示（端进出修订）：全局登出钩子置一次性标记，这里读取后即清除
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    try {
      if (sessionStorage.getItem(EXPIRED_KEY) === "1") {
        sessionStorage.removeItem(EXPIRED_KEY);
        setExpired(true);
      }
    } catch {
      /* 隐私模式等：无提示可接受 */
    }
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password || busy) return;
    setBusy(true);
    setError(null);
    try {
      const s = await login(username.trim(), password);
      // 落地规则收口 lib/portal::landingFor：深链保持 / admin 直落校务台 /
      // 上次班级直落 / 学生非门户路径回门户；null = 停留原地挂载。
      const target = landingFor(s, location.pathname);
      if (target) nav(target, { replace: true });
    } catch (err) {
      setError((err as Error).message || "登录失败");
    } finally {
      setBusy(false);
    }
  };

  const form = (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <label htmlFor="login-username" className="flex flex-col gap-1.5">
        <span className="text-xs font-semibold text-ink-soft">用户名</span>
        <Input
          id="login-username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "login-error" : undefined}
          placeholder="教师/学生登录名（如学籍号）"
          autoComplete="username"
          autoFocus
        />
      </label>
      <label htmlFor="login-password" className="flex flex-col gap-1.5">
        <span className="text-xs font-semibold text-ink-soft">口令</span>
        <Input
          id="login-password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "login-error" : undefined}
          autoComplete="current-password"
          placeholder="••••••"
        />
      </label>
      {error && <p id="login-error" role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger">{error}</p>}
      <Button
        type="submit"
        disabled={!username.trim() || !password || busy}
        className="mt-1 w-full justify-center"
      >
        <SignIn size={15} />
        {busy ? "登录中…" : "登录"}
      </Button>
    </form>
  );

  return (
    <div className="grid min-h-[100dvh] bg-canvas lg:grid-cols-[1.08fr_1fr]">
      {/* 桌面品牌栏：原色背景、图谱线稿和模块入口。 */}
      <aside className="brand-band relative hidden flex-col justify-between overflow-hidden px-14 py-12 lg:flex">
        {/* 模块色光斑：低透明度大面积模糊（旧饱和渐变的替代，质感主来源） */}
        <div aria-hidden className="pointer-events-none absolute inset-0">
          <span
            className="absolute -top-24 -right-16 h-[28rem] w-[28rem] rounded-full blur-[110px]"
            style={{ background: "var(--color-bh-yellow)", opacity: 0.11 }}
          />
          <span
            className="absolute -left-24 bottom-[16%] h-[24rem] w-[24rem] rounded-full blur-[110px]"
            style={{ background: "var(--color-bh-blue)", opacity: 0.09 }}
          />
          <span
            className="absolute -bottom-32 right-[10%] h-[26rem] w-[26rem] rounded-full blur-[120px]"
            style={{ background: "var(--color-surface)", opacity: 0.08 }}
          />
        </div>
        <GraphSketch />

        {/* 品牌行 */}
        <Reveal className="relative flex items-center gap-3">
          <a href={WEBSITE_URL} aria-label="返回官网" className="flex items-center gap-3 rounded-xl outline-offset-4 focus-visible:outline-2 focus-visible:outline-white/80">
          <BrandMark size={44} />
          <div>
            <p className="font-display text-lg font-bold tracking-tight text-white">
              {PRODUCT_NAME}
            </p>
            <p className="text-[11px] text-white/55">教学质量分析平台</p>
          </div>
          </a>
        </Reveal>

        {/* display 大字陈述 + 三端列表（hairline 分隔，不再用玻璃卡） */}
        <div className="relative py-10">
          <Reveal delay={0.06}>
            <h2 className="font-display text-[2.1rem] font-bold leading-[1.3] tracking-tight text-white">
              拍一场卷，
              <br />
              看清一个班的薄弱点
            </h2>
          </Reveal>
          <Reveal delay={0.12}>
            <ul className="mt-10 border-t border-white/10">
              {ENDS.map(({ icon: Icon, accent, title, desc }) => (
                <li
                  key={title}
                  className="flex items-center gap-4 border-b border-white/10 py-4"
                >
                  <span
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/[0.06] ring-1 ring-white/10"
                    aria-hidden
                  >
                    <Icon size={17} weight="bold" style={{ background: accent }} className="p-0.5 text-white" />
                  </span>
                  <div>
                    <p className="text-sm font-semibold text-white">{title}</p>
                    <p className="mt-0.5 text-xs text-white/55">{desc}</p>
                  </div>
                </li>
              ))}
            </ul>
          </Reveal>
        </div>

        {/* 信任线 */}
        <Reveal delay={0.18} className="relative">
          <p className="flex items-center gap-1.5 text-xs text-white/55">
            <ShieldCheck size={14} aria-hidden />
            部署在校内 · 成绩与数据不出校
          </p>
        </Reveal>
      </aside>

      {/* 右：纸面表单（手机端顶部带品牌锁定行） */}
      <main className="flex min-h-[100dvh] flex-col items-center justify-center px-6 py-12">
        {/* 移动端品牌锁定行：小尺寸复刻左栏品牌段，替代旧渐变横幅 */}
        <Reveal className="mb-10 flex items-center gap-2.5 lg:hidden">
          <a href={WEBSITE_URL} aria-label="返回官网" className="flex items-center gap-2.5 rounded-xl outline-offset-4 focus-visible:outline-2 focus-visible:outline-accent">
          <BrandMark size={36} />
          <p className="font-display text-base font-bold tracking-tight text-ink">
            {PRODUCT_NAME}
          </p>
          </a>
        </Reveal>

        <Reveal delay={0.08} className="w-full max-w-sm">
          <h1 className="font-display text-2xl font-bold tracking-tight text-ink">
            欢迎回来
          </h1>
          <p className="mt-1.5 text-[13px] text-ink-soft">
            一个入口 · 三种身份，登录后按角色进入对应端
          </p>
          <Card className="mt-6 border-line-strong/70 p-6 shadow-lift sm:p-7">
            {expired && <p role="status" className="mb-3 rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">登录已过期，请重新登录</p>}
            {form}
          </Card>
        </Reveal>

        <Reveal delay={0.16}>
          <p className="mt-6 max-w-[42ch] text-center text-xs leading-relaxed text-ink-faint">
            教师 / 管理员 / 学生账号均由校内管理员开通；学生登录名默认为学籍号。
          </p>
          <a
            href={WEBSITE_URL}
            className="mx-auto mt-5 inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-xs font-semibold text-accent transition-colors hover:bg-accent/8 hover:text-accent-deep focus-visible:outline-2 focus-visible:outline-accent"
          >
            <ArrowLeft size={14} aria-hidden />
            返回官网
          </a>
        </Reveal>
      </main>
    </div>
  );
}
