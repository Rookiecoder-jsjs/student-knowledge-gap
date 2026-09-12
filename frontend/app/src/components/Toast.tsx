import { CheckCircle, Info, Warning, WarningCircle } from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useState, type ReactNode } from "react";
import { EASE } from "../lib/motion-tokens";
import { ToastContext, type PushToast, type ToastKind } from "../lib/toast";

const TOAST_META: Record<ToastKind, { Icon: typeof Info; cls: string }> = {
  success: { Icon: CheckCircle, cls: "text-success" },
  info: { Icon: Info, cls: "text-info" },
  warn: { Icon: Warning, cls: "text-warn" },
  danger: { Icon: WarningCircle, cls: "text-danger" },
};

let toastSeq = 0;

/** 全站唯一轻提示出口（saas-redesign P1）：底部居中、3.2s 自动消退、aria-live。 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<{ id: number; msg: string; kind: ToastKind }[]>([]);
  const reduce = useReducedMotion();
  const push: PushToast = (msg, kind = "info") => {
    const id = ++toastSeq;
    setToasts((t) => [...t, { id, msg, kind }]);
    window.setTimeout(() => {
      setToasts((t) => t.filter((x) => x.id !== id));
    }, 3200);
  };
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div
        className="pointer-events-none fixed bottom-5 left-1/2 z-[70] flex w-full max-w-sm -translate-x-1/2 flex-col items-center gap-2 px-4"
        aria-live="polite"
      >
        <AnimatePresence>
          {toasts.map(({ id, msg, kind }) => {
            const { Icon, cls } = TOAST_META[kind];
            return (
              <motion.div
                key={id}
                initial={reduce ? false : { opacity: 0, y: 10, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={reduce ? undefined : { opacity: 0, y: 6, scale: 0.98 }}
                transition={{ duration: 0.18, ease: EASE }}
                className="pointer-events-auto flex w-full items-center gap-2.5 rounded-xl border border-line bg-surface px-3.5 py-2.5 shadow-float"
              >
                <Icon size={16} weight="fill" className={`shrink-0 ${cls}`} aria-hidden />
                <span className="min-w-0 break-words text-sm text-ink">{msg}</span>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}
