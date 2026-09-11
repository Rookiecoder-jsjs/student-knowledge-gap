import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { setApiUnauthorized } from "./api";
import { apiLogin, apiMe, clearToken, EXPIRED_KEY, LAST_CLASS_KEY, type Session } from "./auth";

interface AuthState {
  /** undefined=首次加载；null=未登录；否则为会话。 */
  session: Session | null | undefined;
  setSession: (s: Session | null) => void;
  login: (username: string, password: string) => Promise<Session>;
  logout: () => void;
  /** 会话恢复重试（后端瞬断后 error 页「重试」用）：重跑 token → /auth/me。 */
  rebootstrap: () => void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [bootstrapNonce, setBootstrapNonce] = useState(0);
  // 本标签页曾持有会话：401 踢出提示只对「被踢」生效，secure 模式匿名探测的 401 不算
  const hadSession = useRef(false);

  // 会话恢复（localStorage token → /auth/me）；网络瞬断后可经 rebootstrap 重跑
  useEffect(() => {
    let alive = true;
    apiMe()
      .then((s) => {
        if (alive) setSession(s);
      })
      .catch(() => {
        if (alive) setSession(null);
      });
    return () => {
      alive = false;
    };
  }, [bootstrapNonce]);

  useEffect(() => {
    if (session) hadSession.current = true;
  }, [session]);

  // 任一受保护请求 401（token 过期等）→ 全局登出回登录页；曾有会话则置一次性
  // 标记，登录页据此提示「登录已过期」（开放/secure 探测的匿名 401 不置）。
  useEffect(() => {
    setApiUnauthorized(() => {
      clearToken();
      if (hadSession.current) {
        try {
          sessionStorage.setItem(EXPIRED_KEY, "1");
        } catch {
          /* 隐私模式等：提示缺失可接受 */
        }
      }
      setSession(null);
    });
  }, []);

  const login = useCallback(async (username: string, password: string): Promise<Session> => {
    const s = await apiLogin(username, password);
    hadSession.current = true;
    setSession(s);
    return s;
  }, []);

  // 显式退出 = 换人清场：token、上次班级、过期标记一并清。401 踢出不清班级
  // 记忆——过期重登可直落上次班级（landingFor），显式退出视为换人。
  const logout = useCallback(() => {
    clearToken();
    try {
      localStorage.removeItem(LAST_CLASS_KEY);
    } catch {
      /* noop */
    }
    try {
      sessionStorage.removeItem(EXPIRED_KEY);
    } catch {
      /* noop */
    }
    hadSession.current = false;
    setSession(null);
  }, []);

  const rebootstrap = useCallback(() => setBootstrapNonce((n) => n + 1), []);

  const value = useMemo(
    () => ({ session, setSession, login, logout, rebootstrap }),
    [session, login, logout, rebootstrap]
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// oxlint-disable-next-line react/only-export-components -- hook 与 Provider 同文件成对导出
export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth 必须在 <AuthProvider> 内使用");
  return v;
}
