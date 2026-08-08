"""知识追踪层 + 归因层（DESIGN §6/§7）。

- evidence:   提交时从作答派生不可变证据事件
- mastery:    derive-on-read 掌握度推导（时间衰减加权，分认知层级）
- weakness:   证据门槛 + 双基准薄弱判定 + 轨迹分类
- attribution: 规则引擎（前置缺陷 / 遗忘衰减 / 数据不足）
"""
