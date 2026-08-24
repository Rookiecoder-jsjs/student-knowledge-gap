"""sc 领域能力的 MCP Server 入口（Agent 产品化 §5.1，Phase 0 最小实现）。

独立进程，stdio 传输；壳（codex 运行时）作为 MCP client 经此调用 sc 的
确定性管线。原则：**业务代码零改动**——本文件只做「领域能力 → MCP 工具」的
薄包装，聚合逻辑一律复用 queries/ 层纯函数，不复制实现。

工具约束（§5.1）：
- 只读（写操作工具属 Phase 3 且必须过审批门）；
- 返回不含学生真名（name_or_alias 原则，§9 prompt 最小化）；
- 每个响应携带 ``_provenance``（来源端点 + 参数），供前端「依据」链接回溯；
- 大结果集分页是后续工具的硬约束，本工具天然单班粒度无需分页。

用法（codex 配置示例，见仓库 handbook/ 或 DEPLOY 文档后续补充）：
    [mcp_servers.sc]
    command = "<venv>/bin/python"
    args = ["/abs/path/to/backend/app/mcp_server.py"]

数据库定位：未设置 SC_DATABASE_URL 时回落到 backend/sc.db（绝对路径），
使本进程可从任意工作目录启动；生产部署由 compose 显式注入环境变量。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# --- 引导：允许以脚本路径直接启动（cwd 无关）-------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

os.environ.setdefault("SC_DATABASE_URL", f"sqlite:///{_BACKEND_DIR / 'sc.db'}")

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("sc")


def _provenance(endpoint: str, params: dict | None = None) -> dict:
    """响应溯源块：来源端点 + 参数快照 + 生成时刻。"""
    return {
        "source": "sc",
        "endpoint": endpoint,
        "params": params or {},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


@mcp.tool(
    annotations={
        "readOnlyHint": True,  # 壳的免审批判据：mcp_tool_call.rs requires_mcp_tool_approval
        "destructiveHint": False,
    },
)
def get_class_overview() -> dict:
    """获取所有班级的轻量概览。

    每班返回：学生数、考试数、待办考试数、最近一场考试状态（已提交/待审核人数）、
    教学进度覆盖（已教知识点数 / 分析同分母总知识点数）。班级级统计，不含任何
    学生个人信息。适用于「现在各个班的情况怎么样」「哪个班有待办」类问题。
    """
    from app.kb.graph import KpGraph
    from app.kb.resolver import KbNotActiveError, active_kb
    from app.queries.classes_overview import classes_overview
    from app.db import get_session

    with get_session() as db:
        try:
            kb = active_kb(db)
        except KbNotActiveError:
            kb = None
        grade7_set = set(KpGraph(db, kb.id).grade7_kp_ids()) if kb is not None else set()
        data = classes_overview(db, grade7_set)

    data["_provenance"] = _provenance("GET /classes/overview")
    return data


if __name__ == "__main__":
    mcp.run(transport="stdio")
