"""全局配置与算法常量。

溯源原则（DESIGN §4）：一切派生结果记录产生时的 kb/tagger/算法/prompt 版本。
算法版本号集中在此，变更时递增，写入 evidence_event.algo_version。
"""

from dataclasses import dataclass
import os

from dotenv import load_dotenv

# 加载 .env（SC_LLM_PROVIDER / SC_LLM_API_KEY / SC_LLM_MODEL / SC_LLM_BASE_URL 等）
load_dotenv()


ALGO_VERSION = "tracking-v0.1.0"

# ---------------------------------------------------------------------------
# 证据来源权重（DESIGN §6：考试 > 单元测 > 练习）
# ---------------------------------------------------------------------------
SOURCE_TYPE_WEIGHT: dict[str, float] = {
    "期中": 1.0,
    "期末": 1.0,
    "补录": 1.0,      # 补录的也是正式考试成绩
    "单元": 0.8,
    "练习": 0.5,
    "诊断": 0.7,      # 诊断小测：用于证伪归因，权重居中
}

# 半衰期（天）：考试类 60，练习类 30
HALF_LIFE_DAYS: dict[str, float] = {
    "期中": 60.0,
    "期末": 60.0,
    "补录": 60.0,
    "单元": 60.0,
    "诊断": 30.0,
    "练习": 30.0,
}

# ---------------------------------------------------------------------------
# 证据门槛（DESIGN §6 护栏字段）
# ---------------------------------------------------------------------------
MIN_EVIDENCE_COUNT = max(1, int(os.environ.get("SC_MIN_EVIDENCE_COUNT", "2")))  # 证据题目数 < 此值 -> 数据不足（生产默认 2：期中即可用，经大规模随机模拟验证）
EVIDENCE_LOW_WATERMARK = 3  # 评估但标记 low_evidence 的下限：MIN <= count < 此值 -> 依据较少（降门槛时的诚实性护栏）
STALE_DAYS = 90                 # 最近证据 > 90 天 → 可能已变化

# ---------------------------------------------------------------------------
# 薄弱判定（双基准）
# ---------------------------------------------------------------------------
DEFAULT_MASTERY_FLOOR = 0.6     # 绝对底线，可按知识点覆盖
COG_LEVELS = ("识记", "理解", "应用", "综合")  # 认知层级排序（低→高）
# 认知层级底线派生（kb-improvement-design K2）：未显式标注 mastery_floor 的 KP，
# 按期望认知层级的主导层级派生绝对底线。高阶综合题底线低（0.55 已是较好水平），
# 基础识记题底线高（0.70 以下即明显薄弱）。教师可逐 KP 显式覆盖 mastery_floor。
# 40 个 grade7 KP 中 36 个默认 0.6、仅 4 个显式 0.7 —— 派生让"综合题与识记题同底线"
# 的一刀切问题收敛。经验基线，教研复核（0.70/0.65/0.60/0.55）后可调。
COG_FLOOR_DEFAULTS: dict[str, float] = {
    "识记": 0.70,
    "理解": 0.65,
    "应用": 0.60,
    "综合": 0.55,
}
CLASS_PERCENTILE = 25           # 班级参照：低于班级 P25 判薄弱
CLASS_COMMON_WEAK_RATIO = 0.40  # 班级薄弱学生占比 ≥40% → 班级共性问题（教学建议）

# 薄弱判据模式（effectiveness-validation-plan V4）：
# standard = 低于底线 OR 低于班级P25 即判薄弱；
# strict（生产默认）= P25 判据仅在掌握度贴近底线（< floor + margin）时触发，
#   消除"全班达标仍按相对位置误报~25%"的结构性误报。
# 大规模随机模拟（150人×12场×6种子）验证：strict 误报 0.234->0.208（-11%），
# 召回 0.887 / 根源命中 0.863 不退化。设 standard 可回退至相对判据。
WEAKNESS_MODE = os.environ.get("SC_WEAKNESS_MODE", "strict")  # standard | strict（生产默认 strict：消除 P25 结构性误报，经大规模随机模拟验证召回/根源不退化）
WEAKNESS_P25_MARGIN = float(os.environ.get("SC_WEAKNESS_P25_MARGIN", "0.1"))

# ---------------------------------------------------------------------------
# 归因参数
# ---------------------------------------------------------------------------
PREREQ_MAX_DEPTH = 3            # 前置缺陷沿前置边下探 ≤3 层
PREREQ_ROOT_THRESHOLD = 0.6     # 前置点掌握度低于此值视为同步低
FORGET_PEAK_THRESHOLD = float(os.environ.get("SC_FORGET_PEAK_THRESHOLD", "0.7"))  # 历史掌握度曾 ≥此值才算"曾经掌握"（0.75->0.7 降噪敏感，V3-R2）
FORGET_DROP = 0.15              # 当前低于峰值 ≥0.15
FORGET_IDLE_DAYS = 30           # 最近 30 天无证据 → 视为无练习

# 异常考试：总分相对该生历史均值骤降 >30% → 证据降权
ANOMALY_SCORE_DROP = 0.30
ANOMALY_WEIGHT_FACTOR = 0.5

# 掌握度贝叶斯先验（kb-improvement-design K7-A）：mastery 估计向 difficulty 先验收缩。
# prior = 1 - difficulty_prior（难度低 → 先验掌握度高）。数据少（n 小）时用先验兜底，
# 避免 2 证据的极端值被当成确定结论；数据多时回归观测。设 0 关闭（保持纯观测）。
# 公式：mastery = (likelihood·n + prior·K) / (n + K)，K 为先验强度。
# 注意：全局收缩与 floor 判定结构性冲突——K=5 相对 n=2 权重 71%，会把低证据正常学生
# （真实 0.65-0.70）压过派生底线造成误报（金标 0.166 -> 0.415）。故默认关闭，仅按需
# 开启（SC_MASTERY_PRIOR_STRENGTH）待大规模随机模拟验证有效后再转正默认。
MASTERY_PRIOR_STRENGTH = max(
    0.0, float(os.environ.get("SC_MASTERY_PRIOR_STRENGTH", "0.0"))
)

# 全局薄弱抑制（effectiveness-validation-plan V3）：
# 学生在多数已覆盖知识点薄弱时，前置缺陷归因的「特定根源」解释力下降
# （更可能是整体基础/学习状态问题，而非单点前置缺陷）。此时下调前置缺陷
# 归因置信度并标注，避免对全局薄弱学生产出大量高置信度的伪因果根源主张。
# 不删除归因（仍是待验证假设），只降置信度；targeted-weak 学生（弱 kp 占比低）不受影响。
GLOBAL_WEAK_MIN_SAMPLE = 5      # 已评估知识点数 ≥ 此值才判全局薄弱
GLOBAL_WEAK_RATIO = 0.6         # 薄弱占比 ≥ 此值视为全局薄弱
GLOBAL_WEAK_CONF_CAP = 0.5      # 全局薄弱时前置缺陷归因置信度上限

# 选择题猜测校正：g = 1/选项数，默认四选一
DEFAULT_CHOICE_OPTIONS = 4

# 轨迹分类阈值
TRAJECTORY_TREND_PER_MONTH = 0.08   # 归一化斜率阈值（每 30 天）
TRAJECTORY_VOLATILITY = 0.20        # 值序列标准差 > 此值 → 震荡

# 级联错误：权重减半
CASCADE_WEIGHT_FACTOR = 0.5

# 失分归属混合度折扣（improvement-plan §1.4-C）：
# 标注 N 个 kp 的题，证据 weight 乘 (1/√N)^penalty，减少混合题失分对多 kp 的等量污染。
# 单 kp 题（N=1）不打折。默认 0.0（关闭，保持现有数值）；设为 1.0 启用全折扣。
EVIDENCE_MIX_PENALTY = float(os.environ.get("SC_EVIDENCE_MIX_PENALTY", "0.0"))

# 高置信题-kp 标注抽样复核（improvement-plan §3.2）。
# approve-tags 批量批准时，低置信(<0.9)永不批量通过；高置信题按稳定哈希抽样保留待审。
# 默认 0 保持现有工作流；试点/生产建议 0.1（10%）。
TAG_REVIEW_SAMPLE_RATE = max(
    0.0, min(1.0, float(os.environ.get("SC_TAG_REVIEW_SAMPLE_RATE", "0.0")))
)

# ---------------------------------------------------------------------------
# 运行时可用性参数（runtime-goals.md：G5/G6/G8/G12）
# ---------------------------------------------------------------------------
# G12：批量解析并发数（一班 40-50 张卷，默认 3 并发）
BATCH_WORKERS = max(1, int(os.environ.get("SC_BATCH_WORKERS", "3")))
# G6：parsing 看门狗阈值（分钟）——超过 2× LLM TIMEOUT(120s) 视为卡死
BATCH_STALE_MINUTES = max(1, int(os.environ.get("SC_BATCH_STALE_MINUTES", "4")))
# G6：孤儿 tempfile 清扫上限（小时）——超过此时长且未被任何 item 引用即删
BATCH_TEMPFILE_MAX_AGE_HOURS = max(1, int(os.environ.get("SC_BATCH_TEMPFILE_MAX_AGE_HOURS", "24")))
# G5：LLM 熔断阈值（连续失败次数）/ 冷却秒数
LLM_CB_THRESHOLD = max(1, int(os.environ.get("SC_LLM_CB_THRESHOLD", "5")))
LLM_CB_COOLDOWN_SECONDS = max(1, int(os.environ.get("SC_LLM_CB_COOLDOWN_SECONDS", "60")))

# 诊断单 LLM 生成层（diagnosis-sheet-redesign.md §2.6）：总开关，默认关。
# =1 时两张诊断单正文与班级改进意见由 LLM 基于证据包生成，模板渲染降为保底；
# =0（或 mock provider / 熔断开启）全模板。默认关的理由：.env 已配真实 provider/key，
# 默认开会让任何触发报告生成的测试/演示都真调 LLM 消耗额度；部署方显式置 1 开启。
LLM_PLAN_ENABLE = os.environ.get("SC_LLM_PLAN_ENABLE", "").lower() in ("1", "true", "yes")

# 干预闭环（intervention-loop-design.md §8）：建议生成与效果验证参数。
# ACTION_PLAN_ENABLE 默认开——纯计算层零 LLM、零外部调用，关闭仅用于试点回退
# （关闭时提交不生成干预建议；改进单仍随诊断单走 SC_LLM_PLAN_ENABLE 路径）。
# 效果判定阈值首期不断言标准（试点无先验）：先度量、后校准，与有效性验证纪律一致。
ACTION_PLAN_ENABLE = os.environ.get("SC_ACTION_PLAN_ENABLE", "1").lower() in ("1", "true", "yes")
INTERVENTION_MIN_DELTA = float(os.environ.get("SC_INTERVENTION_MIN_DELTA", "0.10"))    # improved 判定阈值（基线调整后增量）
INTERVENTION_FLAT_FLOOR = float(os.environ.get("SC_INTERVENTION_FLAT_FLOOR", "-0.05")) # flat/declined 分界
ACTION_GROUP_MIN = int(os.environ.get("SC_ACTION_GROUP_MIN", "3"))                     # 同根源成组最低人数


@dataclass(frozen=True)
class Settings:
    """运行配置。"""

    # G10/G2：env 驱动 DB URL，迁 PG 只改 SC_DATABASE_URL（derive-on-read 不受影响）
    database_url: str = os.environ.get("SC_DATABASE_URL", "sqlite:///./sc.db")
    # 部署（容器化）：逗号分隔的允许来源；空 = 回落本地开发默认（见 main.py CORS）。
    # 生产同源经 nginx 反代不需要跨域，留空即可。
    cors_origins: str = os.environ.get("SC_CORS_ORIGINS", "")
    kb_dir: str = "kb"
    output_dir: str = "output"
    allow_sync_batch: bool = False  # 批量录入 sync=true 守卫（仅测试/演示开）
    batch_workers: int = BATCH_WORKERS  # G12：并发数可配置


settings = Settings(
    allow_sync_batch=os.environ.get("SC_ALLOW_SYNC_BATCH", "").lower() in ("1", "true", "yes")
)
