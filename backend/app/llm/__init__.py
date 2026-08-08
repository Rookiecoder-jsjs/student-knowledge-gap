"""LLM provider 无关客户端层（DESIGN §11：vision + text，配置切换）。

不变量③：本层只负责解析/抽取，输出必须经 Schema 校验与人工审核闸门
（见 app/ingestion/photo.py），判断权不在 LLM。
"""
