# sc 前端镜像：node 构建 dist -> nginx 静态托管 + /api 前缀剥离反代 backend。
# 构建命令与 CI（frontend job）一致：tsc -b && vite build。
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
