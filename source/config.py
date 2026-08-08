"""
source/config.py - 硬编码配置

本文件包含所有不通过 Web 界面修改的硬编码配置。
如需修改运行时配置（如 API Key、模型），请通过 Web 设置页或编辑 data/settings.json。

配置分类：
- arXiv 分类配置（监控哪些分类）
- 数据库和输出目录配置
- Web 服务配置
- 定时任务配置
- AI 分析并发数
- arXiv 抓取延迟配置
- PDF 下载限速配置
- AI 标签候选列表
- 评级标准说明
"""

import os

# ==================== arxiv 分类配置 ====================
ARXIV_CATEGORIES = [
    # "cs.AI",   # Artificial Intelligence
    "cs.RO",   # Robotics
    # "cs.CV",   # Computer Vision and Pattern Recognition
    # "cs.LG",   # Machine Learning
    # "cs.CL",   # Computation and Language (NLP)
    # "cs.MA",   # Multiagent Systems
]

# 每个分类每次拉取的最大论文数
MAX_PAPERS_PER_CATEGORY = 50

# ==================== 数据库配置 ====================
# 基于文件位置上跳两级计算项目根路径，保证 data/ 始终位于项目根
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(REPO_ROOT, "data")
DB_PATH = os.path.join(DB_DIR, "papers.db")

# ==================== Web 服务配置 ====================
WEB_HOST = "0.0.0.0"
WEB_PORT = 5000

# ==================== 定时任务配置 ====================
# 每天几点执行抓取（24小时制，默认早上10点）
SCHEDULE_HOUR = 10
SCHEDULE_MINUTE = 0

# ==================== AI 分析并发数 ====================
# 同时调用 API 的线程数，可根据 API 速率限制调整
ANALYSIS_CONCURRENCY = 5

# ==================== arXiv 抓取配置 ====================
# API 请求间隔（秒），arXiv 官方建议 ≥ 3
FETCH_REQUEST_DELAY = 3.0
# 分批抓取时，每批的天数
FETCH_BATCH_DAYS = 30
# 分批抓取时，批次之间的间隔（秒）
FETCH_BATCH_DELAY = 5.0

# ==================== PDF 下载限速（令牌桶）====================
# rate: 每秒生成的令牌数（即每秒允许的下载次数）
# capacity: 令牌桶容量（允许的突发下载数）
PDF_DOWNLOAD_RATE = 1.0
PDF_DOWNLOAD_CAPACITY = 2

# ==================== AI 分析标签候选 ====================
# 用于提示 AI 生成标签时参考（力求精确，避免过于宽泛的标签）
TAG_CANDIDATES = [
    # 具体技术/方法
    "VLA", "World Model", "Diffusion Policy", "Imitation Learning",
    "Sim-to-Real", "RLHF", "Reward Model", "Flow Matching",
    "Mamba", "NeRF", "3D Gaussian Splatting",
    # 具体任务/能力
    "Grasping", "Dexterous Manipulation", "Bimanual Manipulation",
    "Locomotion", "Navigation", "Locomotion Control",
    "Language Grounding", "Affordance Prediction", "Pose Estimation",
    "Object Goal Navigation", "Visual Navigation",
    # 具体架构/模型
    "Vision-Language Model", "World Model", "Diffusion Model",
    "Mixture of Experts", "State Space Model",
    # 具体应用领域
    "Humanoid Robot", "Quadruped Robot", "Aerial Robot",
    "Surgical Robot", "Mobile Manipulator",
    # 具体技术方向
    "Sim-to-Real Transfer", "Domain Randomization",
    "Preference Optimization", "Direct Preference Optimization",
    "Chain-of-Thought", "Tool Use", "Code Generation",
    "Scene Reconstruction", "Semantic Mapping", "SLAM",
    "Point Cloud Processing", "Depth Estimation",
    "Task and Motion Planning", "Motion Planning",
    # 具体评测/工程
    "Benchmark", "Synthetic Data", "Data Augmentation",
    "Safety", "Robustness", "Zero-Shot Generalization",
    "Few-Shot Learning", "Continual Learning",
]

# ==================== 评级标准说明 ====================
RATING_CRITERIA = """
评级标准 (0-5星，采用校准后的绝对标准，不强制固定比例):
- 0星: 明显无关、不可用、质量极低，或摘要显示与AI/机器人主题基本不匹配。
- 1星: 弱相关或低新意，主要是简单套用/工程调参，实验或论证明显不足。
- 2星: 常规增量工作，有一定参考价值，但创新性、实验强度或影响范围有限。
- 3星: 扎实合格的论文，有清晰贡献、合理方法和足够实验，但未明显超出同类工作。
- 4星: 强工作，方法、系统、实验或数据资源明显高于日常论文，可能对方向产生较大影响。
- 5星: 非常罕见的领域级突破、重要基准/数据集/系统/范式，证据充分，值得优先精读。

校准要求:
- 不要把 3 星当作默认安全分。普通增量工作通常应给 1-2 星。
- 只有扎实且贡献清晰的工作给 3 星；需要强证据才给 4 星。
- 5 星应非常克制，仅用于可能改变研究方向或被广泛采用的工作。
- 如果摘要信息不足以支持高分，应降低评分而不是猜测。
"""
