import os

# ==================== OpenAI API 配置 ====================
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "sk-bc3ecff7b6f54b9e84f0617ea414fd7a")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "https://api.deepseek.com")

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
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "papers.db")

# ==================== 输出目录配置 ====================
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
DAILY_DIR = os.path.join(OUTPUT_DIR, "daily")

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
评级标准 (0-5星):
- 0星: 与AI/机器人领域无关或质量极低
- 1星: 常规工作，增量改进，无显著创新
- 2星: 有一定价值，但创新性有限
- 3星: 有价值的工作，有明确的创新点
- 4星: 高质量工作，可能产生较大影响
- 5星: 里程碑式工作，将深刻影响领域发展
"""
