"""
PDF 下载与文本提取模块

本模块负责从 arXiv 下载论文 PDF 文件并提取纯文本内容。
主要功能：
- 使用令牌桶算法进行下载速率限制，避免对 arXiv 服务器造成过大压力
- 自动缓存已下载的 PDF 文件，避免重复下载
- 使用 PyMuPDF 提取 PDF 文本内容，支持多页文档
- 自动清理提取的文本（去除多余空行、空格等）

依赖：
- PyMuPDF (fitz): PDF 文本提取
- requests: HTTP 下载
- config.py: 提供下载速率限制参数
"""

import os
import re
import time
import threading
import logging
import requests
import fitz  # PyMuPDF
from config import DB_DIR, PDF_DOWNLOAD_RATE, PDF_DOWNLOAD_CAPACITY
from settings import get_proxy_config

# 模块日志记录器
logger = logging.getLogger(__name__)

# PDF 文件缓存目录路径
PDF_CACHE_DIR = os.path.join(DB_DIR, "pdf_cache")


class TokenBucket:
    """
    令牌桶算法实现
    
    令牌桶算法是一种常用的流量控制算法，用于限制请求的速率。
    工作原理：
    1. 桶以固定速率（rate）向桶中添加令牌
    2. 桶有最大容量（capacity），超过容量的令牌会被丢弃
    3. 每个请求需要消耗一定数量的令牌（默认1个）
    4. 如果桶中有足够令牌，请求立即通过
    5. 如果令牌不足，请求需要等待直到有足够令牌
    
    该算法的特点：
    - 允许突发流量：桶满时可以连续处理多个请求
    - 长期速率限制：平均速率不超过设定的 rate
    - 平滑限流：不会出现突然的流量峰值
    
    在本项目中用于限制 PDF 下载速率，避免对 arXiv 服务器造成过大压力。
    """
    
    def __init__(self, rate=1.0, capacity=2):
        """
        初始化令牌桶
        
        Args:
            rate (float): 令牌添加速率（个/秒），即允许的平均请求速率
            capacity (int): 桶的最大容量，决定允许的最大突发请求数
        """
        self.rate = rate  # 令牌添加速率（个/秒）
        self.capacity = capacity  # 桶最大容量
        self.tokens = capacity  # 当前令牌数，初始为满
        self.last_time = time.monotonic()  # 上次更新时间，使用单调时钟避免系统时间调整影响
        self.lock = threading.Lock()  # 线程锁，确保多线程安全

    def acquire(self, tokens=1):
        """
        获取指定数量的令牌
        
        如果令牌充足，立即返回0（无需等待）。
        如果令牌不足，计算需要等待的时间并返回。
        
        Args:
            tokens (int): 需要获取的令牌数量，默认为1
            
        Returns:
            float: 需要等待的时间（秒）。0表示可以立即执行，正数表示需要等待的秒数
        """
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_time
            
            # 根据经过的时间添加新令牌
            # 公式：新令牌数 = 经过时间 × 速率
            # 使用 min() 确保不超过桶容量
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_time = now

            if self.tokens >= tokens:
                # 令牌充足，直接消耗令牌，无需等待
                self.tokens -= tokens
                return 0
            else:
                # 令牌不足，计算需要等待的时间
                # 等待时间 = (需要的令牌数 - 当前令牌数) / 添加速率
                wait_time = (tokens - self.tokens) / self.rate
                self.tokens = 0  # 消耗所有可用令牌
                self.last_time += wait_time  # 调整时间，模拟等待期间添加的令牌
                return wait_time


# 全局令牌桶实例，用于控制 PDF 下载速率
# 参数从 config.py 中读取
_pdf_bucket = TokenBucket(rate=PDF_DOWNLOAD_RATE, capacity=PDF_DOWNLOAD_CAPACITY)


def _ensure_cache_dir():
    """
    确保 PDF 缓存目录存在
    
    如果目录不存在则自动创建。使用 exist_ok=True 避免目录已存在时报错。
    """
    os.makedirs(PDF_CACHE_DIR, exist_ok=True)


def _get_proxy_dict():
    """读取运行时代理配置，供 PDF 下载请求使用。"""
    proxy = get_proxy_config()
    if not proxy.get("enabled"):
        return None
    proxies = {}
    if proxy.get("http"):
        proxies["http"] = proxy["http"]
    if proxy.get("https"):
        proxies["https"] = proxy["https"]
    return proxies or None


def download_pdf(pdf_url, arxiv_id):
    """
    下载论文 PDF 文件
    
    下载流程：
    1. 检查本地缓存是否已有该文件，如有则直接返回缓存路径
    2. 通过令牌桶获取下载许可，必要时等待
    3. 发送 HTTP GET 请求下载 PDF
    4. 将下载的内容保存到缓存目录
    5. 返回缓存文件路径
    
    Args:
        pdf_url (str): PDF 文件的下载 URL
        arxiv_id (str): 论文的 arXiv ID（如 "2401.12345"）
        
    Returns:
        str 或 None: 下载成功返回本地文件路径，失败返回 None
    """
    _ensure_cache_dir()
    
    # 构造缓存文件路径，将 arxiv_id 中的 '/' 替换为 '_' 以避免路径问题
    cache_path = os.path.join(PDF_CACHE_DIR, f"{arxiv_id.replace('/', '_')}.pdf")

    # 检查缓存，如果文件已存在则直接返回
    if os.path.exists(cache_path):
        return cache_path

    # 从令牌桶获取下载许可，如果需要等待则休眠
    wait = _pdf_bucket.acquire()
    if wait > 0:
        logger.debug(f"Rate limited, waiting {wait:.1f}s for {arxiv_id}")
        time.sleep(wait)

    try:
        # 设置请求头，模拟浏览器访问
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ArxivPaperDB/1.0)"}
        resp = requests.get(pdf_url, headers=headers, proxies=_get_proxy_dict(), timeout=60)
        resp.raise_for_status()  # 如果状态码不是 2xx 则抛出异常
        
        # 将下载内容写入缓存文件
        with open(cache_path, "wb") as f:
            f.write(resp.content)
        logger.info(f"Downloaded PDF: {arxiv_id} ({len(resp.content) / 1024:.0f} KB)")
        return cache_path
    except Exception as e:
        logger.error(f"Failed to download PDF for {arxiv_id}: {e}")
        return None


def extract_text_from_pdf(pdf_path):
    """
    从 PDF 文件中提取纯文本内容
    
    提取流程：
    1. 使用 PyMuPDF 打开 PDF 文件
    2. 遍历每一页，提取文本内容
    3. 跳过空白页面
    4. 合并所有页面的文本
    5. 清理文本：去除多余空行、多余空格
    
    Args:
        pdf_path (str): PDF 文件的本地路径
        
    Returns:
        str 或 None: 提取成功返回纯文本内容，失败返回 None
        
    Note:
        - 扫描版 PDF（图片）无法提取文本，会返回 None
        - 提取的文本会进行清理，去除多余空白字符
    """
    try:
        doc = fitz.open(pdf_path)
        pages = []
        
        # 遍历每一页，提取文本
        for i, page in enumerate(doc):
            text = page.get_text("text")
            # 只保留非空页面
            if text.strip():
                pages.append(text)
        doc.close()

        # 使用双换行符连接所有页面，模拟段落分隔
        full_text = "\n\n".join(pages)

        # 文本清理：去除多余空行和空格
        # 将3个及以上连续换行符替换为2个（保持段落分隔）
        full_text = re.sub(r'\n{3,}', '\n\n', full_text)
        # 将2个及以上连续空格替换为1个
        full_text = re.sub(r' {2,}', ' ', full_text)
        # 去除首尾空白字符
        full_text = full_text.strip()

        return full_text
    except Exception as e:
        logger.error(f"Failed to extract text from {pdf_path}: {e}")
        return None


def get_paper_full_text(pdf_url, arxiv_id, max_chars=6000000):
    """
    获取论文的完整文本内容
    
    这是一个便捷函数，整合了下载和提取两个步骤。
    如果文本超过最大字符限制，会自动截断并添加提示信息。
    
    Args:
        pdf_url (str): PDF 文件的下载 URL
        arxiv_id (str): 论文的 arXiv ID
        max_chars (int): 最大字符数限制，默认 6000000（约 6MB 文本）
                        设置过大会影响 AI 分析的 token 消耗
        
    Returns:
        str 或 None: 成功返回论文文本，失败返回 None
        
    Note:
        - 如果 PDF 下载失败或文本提取失败，会返回 None
        - 超长文本会被截断，截断后会在末尾添加提示信息
    """
    # 第一步：下载 PDF 文件
    pdf_path = download_pdf(pdf_url, arxiv_id)
    if not pdf_path:
        return None

    # 第二步：提取文本内容
    text = extract_text_from_pdf(pdf_path)
    if not text:
        return None

    # 第三步：检查文本长度，必要时截断
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... 文本已截断，以上为论文前部分内容 ...]"
        logger.info(f"Truncated paper text for {arxiv_id} to {max_chars} chars")

    return text
