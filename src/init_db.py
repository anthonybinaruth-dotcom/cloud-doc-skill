"""数据库初始化脚本"""

import sys
from pathlib import Path

from .config import get_config
from .storage import DocumentStorage


def init_database():
    """初始化数据库"""
    try:
        # 加载配置
        config = get_config()
        
        # 获取数据库路径
        db_path = config.get('storage.database')
        if not db_path:
            print("错误: 配置文件中未找到数据库路径")
            sys.exit(1)
        
        # 确保数据目录存在
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 创建存储实例
        db_url = f"sqlite:///{db_path}"
        storage = DocumentStorage(db_url)
        
        # 初始化数据库
        print(f"正在初始化数据库: {db_path}")
        storage.init_db()
        print("数据库初始化成功！")
        
        # 显示表信息
        print("\n已创建以下数据表:")
        print("  - documents (文档表)")
        print("  - document_versions (文档版本历史表)")
        print("  - scan_records (扫描记录表)")
        print("  - changes (变更记录表)")
        print("  - notifications (通知记录表)")
        
    except Exception as e:
        print(f"数据库初始化失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    init_database()
