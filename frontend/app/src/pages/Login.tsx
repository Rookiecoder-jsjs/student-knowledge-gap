import {
  ChalkboardTeacher,
  ChatCircleDots,
  ShieldCheck,
  SignIn,
  Student,
  TreeStructure,
} from "@phosphor-icons/react";
import { useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button, Card, Input } from "../components/ui";
import { useAuth } from "../lib/AuthContext";
import { EXPIRED_KEY } from "../lib/auth";
import { landingFor } from "../lib/portal";
import { ACCENTS } from "../lib/theme";

/** 统一登录页（auth-roles-design §7）：教师/管理员/学生同一入口。

左右分栏（门面即分区陈述）：左=品牌 + 三端价值卡片 + 校内部署信任线；
右=登录表单。手机端左栏折叠为品牌横条，表单单列。
*/
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
      // 落地规则收口 lib/portal::landingFor：深链保持 / 纯校管直落校务台 /
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
    <form onSubmit={submit} className="space-y-3">
      <label className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-ink-faint">用户名</span>
        <Input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="教师/学生登录名（如学籍号）"
          autoComplete="username"
          autoFocus
        />
      </label>
      <label className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-ink-faint">口令</span>
        <Input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          placeholder="••••••"
        />
      </label>
      {error && <p className="text-xs text-danger">{error}</p>}
      <Button type="submit" disabled={!username.trim() || !password || busy} className="w-full justify-center">
        <SignIn size={15} />
        {busy ? "登录中…" : "登录"}
      </Button>
    </form>
  );

  return (
    <div className="grid min-h-[100dvh] bg-surface lg:grid-cols-[1.15fr_1fr]">
      {/* 左：品牌 + 三端价值 + 信任线（lg 以上展示） */}
      <aside className="hidden flex-col justify-between overflow-hidden bg-gradient-to-br from-[#0f766e] via-[#14b8a6] to-[#2563eb] px-14 py-12 lg:flex">
        <div className="flex items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-white/15 text-white backdrop-blur">
            <TreeStructure size={22} weight="bold" />
          </span>
          <div>
            <p className="font-display text-xl font-bold tracking-tight text-white">薄弱点分析</p>
            <p className="text-xs text-white/75">拍一场卷，看清一个班的薄弱点</p>
          </div>
        </div>

        <ul className="space-y-3">
          {ENDS.map(({ icon: Icon, accent, title, desc }) => (
            <li
              key={title}
              className="flex items-center gap-4 rounded-2xl border border-white/15 bg-white/10 px-5 py-4 backdrop-blur"
            >
              <span
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
                style={{ background: accent }}
                aria-hidden
              >
                <Icon size={19} weight="bold" className="text-white" />
              </span>
              <div>
                <p className="text-sm font-semibold text-white">{title}</p>
                <p className="mt-0.5 text-xs text-white/75">{desc}</p>
              </div>
            </li>
          ))}
        </ul>

        <p className="flex items-center gap-1.5 text-xs text-white/70">
          <ShieldCheck size={14} aria-hidden />
          部署在校内 · 成绩与数据不出校
        </p>
      </aside>

      {/* 右：登录表单（手机端含折叠品牌横条） */}
      <main className="flex min-h-[100dvh] flex-col items-center justify-center px-6 py-12">
        <div className="mb-8 w-full max-w-sm rounded-2xl bg-gradient-to-br from-[#0f766e] via-[#14b8a6] to-[#2563eb] px-5 py-4 lg:hidden">
          <div className="flex items-center gap-3 text-white">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/15 backdrop-blur">
              <TreeStructure size={18} weight="bold" />
            </span>
            <div>
              <p className="font-display text-base font-bold tracking-tight">薄弱点分析</p>
              <p className="text-[11px] text-white/75">拍一场卷，看清一个班的薄弱点</p>
            </div>
          </div>
        </div>

        <div className="w-full max-w-sm">
          <h1 className="text-lg font-semibold text-ink">登录</h1>
          <p className="mt-1 text-xs text-ink-faint">一个入口 · 三种身份，登录后按角色进入对应端</p>
          <Card className="mt-4 p-6">
            {expired && <p className="mb-3 text-xs text-warn">登录已过期，请重新登录</p>}
            {form}
          </Card>
        </div>

        <p className="mt-6 max-w-[42ch] text-center text-xs leading-relaxed text-ink-faint">
          教师 / 管理员 / 学生账号均由校内管理员开通；学生登录名默认为学籍号。
        </p>
      </main>
    </div>
  );
}
