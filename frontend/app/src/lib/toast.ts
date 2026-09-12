import { createContext, useContext } from "react";

/**
 * 轻提示类型与上下文（saas-redesign P1）。
 * 组件文件只导出组件（fast-refresh 纪律）：context/hook 放本 lib，
 * ToastProvider 在 components/Toast.tsx。
 */
export type ToastKind = "success" | "info" | "warn" | "danger";
export type PushToast = (msg: string, kind?: ToastKind) => void;

export const ToastContext = createContext<PushToast>(() => {});

/** 发一条轻提示。须在 <ToastProvider> 内使用。 */
export const useToast = () => useContext(ToastContext);
