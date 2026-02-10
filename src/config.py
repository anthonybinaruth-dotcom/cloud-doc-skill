"""配置管理模块"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class ConfigError(Exception):
    """配置错误异常"""
    pass


class Config:
    """配置管理类"""
    
    # 内置默认配置，uvx 运行时无需 config.yaml
    DEFAULT_CONFIG: Dict[str, Any] = {
        "crawler": {
            "base_url": "https://help.aliyun.com",
            "request_delay": 1.0,
            "max_retries": 3,
            "timeout": 30,
            "user_agent": "AliyunDocMonitor/1.0",
        },
        "scheduler": {
            "enabled": False,
            "cron": "0 9 * * 1",
            "timezone": "Asia/Shanghai",
        },
        "llm": {
            "provider": "dashscope",
            "model": "qwen-turbo",
            "api_key": "${DASHSCOPE_API_KEY}",
            "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "max_tokens": 1000,
            "temperature": 0.3,
        },
        "notifications": [
            {"type": "file", "enabled": True, "output_dir": "./notifications"},
        ],
        "storage": {
            "type": "sqlite",
            "database": "./data/aliyun_docs.db",
            "keep_versions": 10,
        },
        "logging": {
            "level": "INFO",
            "file": "./logs/monitor.log",
            "max_size": "10MB",
            "backup_count": 5,
        },
    }

    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化配置管理器
        
        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path
        self._config: Dict[str, Any] = {}
        self.load()
    
    def load(self) -> None:
        """加载配置文件，不存在时使用内置默认配置"""
        if not Path(self.config_path).exists():
            # 无配置文件时使用默认配置
            import copy
            raw_config = copy.deepcopy(self.DEFAULT_CONFIG)
            self._config = self._replace_env_vars(raw_config)
            return
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                raw_config = yaml.safe_load(f)
            
            # 替换环境变量
            self._config = self._replace_env_vars(raw_config)
            
            # 验证配置
            self.validate()
            
        except yaml.YAMLError as e:
            raise ConfigError(f"配置文件格式错误: {e}")
        except Exception as e:
            raise ConfigError(f"加载配置文件失败: {e}")
    
    def _replace_env_vars(self, obj: Any) -> Any:
        """
        递归替换配置中的环境变量
        
        支持格式: ${VAR_NAME} 或 ${VAR_NAME:default_value}
        
        Args:
            obj: 配置对象（可以是dict、list、str等）
        
        Returns:
            替换后的配置对象
        """
        if isinstance(obj, dict):
            return {k: self._replace_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._replace_env_vars(item) for item in obj]
        elif isinstance(obj, str):
            # 匹配 ${VAR_NAME} 或 ${VAR_NAME:default}
            pattern = r'\$\{([^}:]+)(?::([^}]*))?\}'
            
            def replacer(match):
                var_name = match.group(1)
                default_value = match.group(2) if match.group(2) is not None else ""
                return os.environ.get(var_name, default_value)
            
            return re.sub(pattern, replacer, obj)
        else:
            return obj
    
    def validate(self) -> None:
        """验证配置的完整性和正确性"""
        required_sections = ['crawler', 'scheduler', 'llm', 'notifications', 'storage', 'logging']
        
        for section in required_sections:
            if section not in self._config:
                raise ConfigError(f"缺少必需的配置节: {section}")
        
        # 验证爬虫配置
        crawler = self._config['crawler']
        if 'base_url' not in crawler:
            raise ConfigError("爬虫配置缺少 base_url")
        
        # 验证大模型配置
        llm = self._config['llm']
        if 'provider' not in llm:
            raise ConfigError("大模型配置缺少 provider")
        if 'model' not in llm:
            raise ConfigError("大模型配置缺少 model")
        
        # 验证存储配置
        storage = self._config['storage']
        if 'type' not in storage:
            raise ConfigError("存储配置缺少 type")
        if 'database' not in storage:
            raise ConfigError("存储配置缺少 database")
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值（支持点号分隔的嵌套键）
        
        Args:
            key: 配置键，支持 "section.subsection.key" 格式
            default: 默认值
        
        Returns:
            配置值
        """
        keys = key.split('.')
        value = self._config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any) -> None:
        """
        设置配置值（支持点号分隔的嵌套键）
        
        Args:
            key: 配置键
            value: 配置值
        """
        keys = key.split('.')
        config = self._config
        
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        config[keys[-1]] = value
    
    def get_all(self) -> Dict[str, Any]:
        """获取所有配置"""
        return self._config.copy()


# 全局配置实例
_config_instance: Optional[Config] = None


def get_config(config_path: str = "config.yaml") -> Config:
    """
    获取全局配置实例（单例模式）
    
    Args:
        config_path: 配置文件路径
    
    Returns:
        Config实例
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = Config(config_path)
    return _config_instance
