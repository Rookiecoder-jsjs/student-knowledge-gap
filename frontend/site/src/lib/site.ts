/**
 * 官网品牌常量（docs/site-redesign.md §7）。
 * 品牌名与 app 登录页一致（「薄弱点分析」）——保持产品一致性；如定新品牌名，只改这里。
 */
export const SITE = {
  name: "薄弱点分析",
  suffix: "教学质量分析平台",
  tagline: "从一张试卷，到每个学生的提升路径",
  /** TODO(上线前必填): 商务联系邮箱——CTA 表单以 mailto 提交，当前为占位 */
  email: "hello@example.com",
  /** 应用登录入口由构建参数注入；同域部署时可保持 /login。 */
  loginUrl: `${(import.meta.env.VITE_APP_URL ?? "").replace(/\/+$/, "")}/login`.replace(/^\/login$/, "/login"),
} as const;
