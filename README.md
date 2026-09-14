# SC 学情诊断系统

SC 是一套面向中学教师的学情诊断工具。它把考试或练习结果整理成可追溯的知识点分析，帮助教师快速回答三件事：

- 哪些知识点需要关注？
- 学生可能卡在哪里？
- 下一步应该怎样补学，并如何通过复测确认效果？

系统当前以初一数学为试点，知识库可按学校的教材和学科继续扩展。

## 产品能做什么

1. **收集成绩与作答信息**：支持手工录入、Excel 导入和拍照录入。
2. **生成班级分析**：查看整体掌握情况、共性薄弱点和教学建议。
3. **生成学生诊断单**：按学生查看薄弱知识点、证据和下一步建议。
4. **支持教师复核**：重要标注和归因都可以审核、修改或否决。
5. **形成干预闭环**：记录补学行动，复测后查看掌握度是否改善。
6. **可选 AI 辅助**：用于试卷解析和文字整理；没有模型配置时，系统仍可使用确定性分析和模板报告。

SC 的输出是辅助教学决策的证据和建议，不替代教师判断，也不用于推断学生的家庭、动机或心理状态。

## 快速启动（本地开发）

### 环境要求

- Python 3.11+
- Node.js 20+
- npm

### 1. 启动后端

```bash
cd backend
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
# .venv\Scripts\Activate.ps1

pip install -e .
python -m uvicorn app.main:app --reload --port 8000
```

后端启动后可访问 <http://127.0.0.1:8000/docs> 查看接口文档。

### 2. 启动教师端

另开一个终端：

```bash
cd frontend/app
npm install
npm run dev
```

打开 <http://127.0.0.1:5173>。

### 3. （可选）启动产品官网

```bash
cd frontend/site
npm install
npm run dev
```

官网地址为 <http://127.0.0.1:5174>。

### 4. 导入示例知识库

首次使用可以导入仓库内置的初一数学知识库：

```bash
curl -X POST http://127.0.0.1:8000/kb/import \
  -H "Content-Type: application/json" \
  -d '{"yaml_path":"kb/math/grade7/kb.yaml"}'
```

然后在教师端创建学校、班级和考试，按页面提示录入或导入数据即可。

## 使用真实模型（可选）

复制配置模板：

```bash
cp backend/.env.example backend/.env
```

在 `backend/.env` 中填写模型供应商和密钥，例如 `SC_LLM_PROVIDER`、`SC_LLM_API_KEY`、`SC_LLM_BASE_URL` 和 `SC_LLM_MODEL`。不配置密钥时，涉及模型的功能会降级或提示配置，不影响基础数据管理和确定性分析。

## Docker 部署

适合试点或长期运行时使用 Docker Compose：

```bash
cp backend/.env.example backend/.env
cd deploy
docker compose up -d --build
```

默认入口：

- 官网：<http://127.0.0.1:5174>
- 教师端：<http://127.0.0.1:8080>
- 后端文档：<http://127.0.0.1:8000/docs>

完整的环境变量、备份恢复和升级说明见 [DEPLOY.md](DEPLOY.md)。如果是全新环境且 `gateway/.runtime/` 不存在，需要先按部署文档准备运行时文件。

## 常用操作

```bash
# 运行后端测试
cd backend
python -m pytest

# 生成演示数据和报告
python scripts/run_demo.py
```

演示产物会写入 `backend/output/`。数据库默认使用 SQLite；Docker 部署时数据保存在 Compose 数据卷中。

## 项目目录

| 目录 | 用途 |
| --- | --- |
| `backend/` | 数据管理、分析和报告服务 |
| `frontend/app/` | 教师、管理和学生使用的 Web 应用 |
| `frontend/site/` | 产品官网 |
| `gateway/` | AI 助手会话网关 |
| `runtime/` | AI 助手运行时组件 |
| `deploy/` | Docker Compose 部署文件 |

## 进一步了解

- [部署与运维](DEPLOY.md)
- [架构说明](handbook/ARCHITECTURE.md)
- [构建说明](handbook/BUILD.md)
- [许可证](LICENSE)
