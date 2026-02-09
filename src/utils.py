"""工具函数模块"""

import hashlib
import logging
import time
from functools import wraps
from typing import Callable, Any
from urllib.parse import urlparse, urljoin, urlunparse


def compute_content_hash(content: str) -> str:
    """
    计算内容的SHA256哈希值
    
    Args:
        content: 文档内容
    
    Returns:
        哈希值（十六进制字符串）
    """
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def normalize_url(url: str, base_url: str = "") -> str:
    """
    规范化URL
    
    - 移除URL片段（#后面的部分）
    - 移除查询参数（?后面的部分）
    - 转换为绝对URL
    - 统一URL格式
    
    Args:
        url: 原始URL
        base_url: 基础URL（用于相对URL转换）
    
    Returns:
        规范化后的URL
    """
    # 如果是相对URL，转换为绝对URL
    if base_url and not url.startswith(('http://', 'https://')):
        url = urljoin(base_url, url)
    
    # 解析URL
    parsed = urlparse(url)
    
    # 重建URL（移除fragment和query）
    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        '',  # params
        '',  # query
        ''   # fragment
    ))
    
    # 移除末尾的斜杠（除非是根路径）
    if normalized.endswith('/') and len(parsed.path) > 1:
        normalized = normalized[:-1]
    
    return normalized


def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """
    重试装饰器
    
    Args:
        max_attempts: 最大尝试次数
        delay: 初始延迟时间（秒）
        backoff: 延迟时间的倍增因子
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            current_delay = delay
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts:
                        logging.warning(
                            f"{func.__name__} 失败 (尝试 {attempt}/{max_attempts}): {e}. "
                            f"将在 {current_delay:.1f} 秒后重试..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logging.error(
                            f"{func.__name__} 在 {max_attempts} 次尝试后仍然失败: {e}"
                        )
            
            # 如果所有尝试都失败，抛出最后一个异常
            raise last_exception
        
        return wrapper
    return decorator


def setup_logging(
    level: str = "INFO",
    log_file: str = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5
) -> None:
    """
    配置日志系统
    
    Args:
        level: 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
        log_file: 日志文件路径（如果为None，只输出到控制台）
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的日志文件备份数量
    """
    from logging.handlers import RotatingFileHandler
    from pathlib import Path
    
    # 设置日志级别
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # 创建日志格式
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 获取根日志记录器
    logger = logging.getLogger()
    logger.setLevel(log_level)
    
    # 清除现有的处理器
    logger.handlers.clear()
    
    # 添加控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 如果指定了日志文件，添加文件处理器
    if log_file:
        # 确保日志目录存在
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 创建轮转文件处理器
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    logging.info(f"日志系统已初始化，级别: {level}")


def parse_size_string(size_str: str) -> int:
    """
    解析大小字符串（如 "10MB", "1GB"）为字节数
    
    Args:
        size_str: 大小字符串
    
    Returns:
        字节数
    """
    size_str = size_str.strip().upper()
    
    units = {
        'B': 1,
        'KB': 1024,
        'MB': 1024 * 1024,
        'GB': 1024 * 1024 * 1024,
        'TB': 1024 * 1024 * 1024 * 1024
    }
    
    for unit, multiplier in units.items():
        if size_str.endswith(unit):
            try:
                number = float(size_str[:-len(unit)])
                return int(number * multiplier)
            except ValueError:
                raise ValueError(f"无效的大小字符串: {size_str}")
    
    # 如果没有单位，假设是字节
    try:
        return int(size_str)
    except ValueError:
        raise ValueError(f"无效的大小字符串: {size_str}")


def deduplicate_urls(urls: list) -> list:
    """
    URL去重（保持顺序）
    
    Args:
        urls: URL列表
    
    Returns:
        去重后的URL列表
    """
    seen = set()
    result = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result
