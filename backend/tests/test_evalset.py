"""评测集回归门禁（agent-product-design §10.1 Phase 4 批次A）。

app/evalset.py 的 8 个标准问答对在内存库上全跑：任一断言失败即回归——
确定性管线行为漂移（阈值/排序/字段形状）第一时间被抓，Agent 结论与
管线对账的纪律由此测化。
"""

from __future__ import annotations

from app.evalset import CASES, READ_TOOLS, run_all


def test_all_cases_pass():
    rows = run_all(_fresh_session())
    failed = [r for r in rows if not r["ok"]]
    assert not failed, "对账失败用例: " + "; ".join(
        f"{r['id']}: " + ", ".join(c["name"] for c in r["checks"] if not c["ok"])
        for r in failed
    )


def test_covers_all_read_tools():
    """标准问答对覆盖核心只读路径；写工具和补充工具由专门测试覆盖。"""
    import asyncio

    from app.mcp_server import mcp

    registered = {tool.name for tool in asyncio.run(mcp.list_tools())}
    # 评测集以确定性管线的核心纯函数为基准；latest_exam_id 是辅助查询函数，
    # 不作为 MCP 工具注册，但必须保留回归覆盖。
    from app import mcp_tools

    read_fns = {
        "get_exam_summary", "get_kp_mastery", "run_attribution", "get_kp_detail",
        "get_teaching_progress", "list_students", "latest_exam_id",
    }
    assert READ_TOOLS == read_fns
    # latest_exam_id 是纯函数辅助查询，其余评测工具必须有对应 MCP 包装。
    assert (read_fns - {"latest_exam_id"}) <= registered
    assert all(hasattr(mcp_tools, n) for n in read_fns)


def test_per_case_shape():
    """每个用例至少两条断言且问题文本非空（防用例退化成空壳）。"""
    assert len(CASES) == 8
    for case in CASES:
        assert case.question.strip()
        assert len(case.checks) >= 2


def _fresh_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()
