import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "../lib/AuthContext";
import { roleFlags, type RoleFlags } from "../lib/portal";

/** 当前门户角色旗标（组件文件内用；纯计算在 lib/portal）。 */
// oxlint-disable-next-line react/only-export-components -- hook 与守卫组件同文件成对导出
export function useRoleFlags(): RoleFlags {
  return roleFlags(useAuth().session);
}

/** 路由级守卫：不满足旗标 → 重定向工作台（教师敲 /admin/*、匿名敲 /assistant 等不裸奔）。 */
export function Guard({ show, children }: { show: boolean; children: ReactNode }) {
  if (!show) return <Navigate to="/" replace />;
  return <>{children}</>;
}
