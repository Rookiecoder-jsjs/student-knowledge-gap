/** 官网入口由构建参数注入；本地 Docker 默认指向官网容器 5174。 */
export const WEBSITE_URL = (import.meta.env.VITE_SITE_URL ?? "http://127.0.0.1:5174").replace(/\/+$/, "");
