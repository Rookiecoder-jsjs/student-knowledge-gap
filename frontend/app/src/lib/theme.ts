/** 模块色编码（包豪斯原色系统核心）。侧栏导航方块与各页面 <Page accent> 单一来源。
 * 红=工作台、蓝=考试、黑=学生、赭=知识点；黄 #e2a93b 仅作装饰色（--color-bh-yellow），
 * 不作模块色——黄底白字对比不足。 */
export const ACCENTS = {
  /** 工作台：包豪斯红（默认 accent） */
  dashboard: "#d63a2f",
  /** 考试：构成蓝 */
  exam: "#1f5fbf",
  /** 学生：构成黑 */
  student: "var(--color-module-student)",
  /** 知识点/分析：暗赭（白字 AA 达标） */
  knowledge: "#8a5f0e",
} as const;
