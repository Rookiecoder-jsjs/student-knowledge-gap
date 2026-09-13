/** 模块色编码（教育风彩色系统核心）。Shell 导航胶囊与各页面 <Page accent> 共用同一来源。 */
export const ACCENTS = {
  /** 工作台：青绿（默认 accent） */
  dashboard: "#0f766e",
  /** 考试：批注琥珀（与告警色拉开明度） */
  exam: "#b85f24",
  /** 学生：沉静湖蓝 */
  student: "#356a8a",
  /** 知识点/分析：草木绿，避免通用 SaaS 紫 */
  knowledge: "#59745a",
} as const;
