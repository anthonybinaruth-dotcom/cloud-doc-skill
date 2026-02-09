"""数据存储模块"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey,
    create_engine, Index
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session

from .models import Document as DocModel


Base = declarative_base()


class DocumentDB(Base):
    """文档表模型"""
    __tablename__ = 'documents'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String(500), unique=True, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    content_hash = Column(String(64), nullable=False)
    last_modified = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    
    # 关系
    versions = relationship("DocumentVersionDB", back_populates="document", cascade="all, delete-orphan")
    changes = relationship("ChangeDB", back_populates="document", cascade="all, delete-orphan")


class DocumentVersionDB(Base):
    """文档版本历史表模型"""
    __tablename__ = 'document_versions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey('documents.id'), nullable=False, index=True)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    
    # 关系
    document = relationship("DocumentDB", back_populates="versions")
    
    # 索引
    __table_args__ = (
        Index('idx_doc_version', 'document_id', 'version'),
    )


class ScanRecordDB(Base):
    """扫描记录表模型"""
    __tablename__ = 'scan_records'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(50), nullable=False)  # running, completed, failed
    documents_scanned = Column(Integer, nullable=True)
    changes_detected = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    
    # 关系
    changes = relationship("ChangeDB", back_populates="scan", cascade="all, delete-orphan")
    notifications = relationship("NotificationDB", back_populates="scan", cascade="all, delete-orphan")


class ChangeDB(Base):
    """变更记录表模型"""
    __tablename__ = 'changes'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_id = Column(Integer, ForeignKey('scan_records.id'), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey('documents.id'), nullable=False, index=True)
    change_type = Column(String(50), nullable=False)  # added, modified, deleted
    diff = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    
    # 关系
    scan = relationship("ScanRecordDB", back_populates="changes")
    document = relationship("DocumentDB", back_populates="changes")


class NotificationDB(Base):
    """通知记录表模型"""
    __tablename__ = 'notifications'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_id = Column(Integer, ForeignKey('scan_records.id'), nullable=False, index=True)
    channel = Column(String(50), nullable=False)  # webhook, file, email
    status = Column(String(50), nullable=False)  # pending, sent, failed
    sent_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    
    # 关系
    scan = relationship("ScanRecordDB", back_populates="notifications")


class DocumentStorage:
    """文档存储类"""
    
    def __init__(self, database_url: str):
        """
        初始化存储
        
        Args:
            database_url: 数据库连接URL
        """
        self.engine = create_engine(database_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
    
    def init_db(self) -> None:
        """初始化数据库（创建所有表）"""
        Base.metadata.create_all(self.engine)
    
    def get_session(self) -> Session:
        """获取数据库会话"""
        return self.SessionLocal()
    
    def save_document(self, doc: DocModel) -> int:
        """
        保存文档
        
        Args:
            doc: 文档对象
        
        Returns:
            文档ID
        """
        session = self.get_session()
        try:
            # 检查文档是否已存在
            existing = session.query(DocumentDB).filter_by(url=doc.url).first()
            
            if existing:
                # 更新现有文档
                existing.title = doc.title
                existing.content_hash = doc.content_hash
                existing.last_modified = doc.last_modified
                existing.updated_at = datetime.now()
                session.commit()
                return existing.id
            else:
                # 创建新文档
                db_doc = DocumentDB(
                    url=doc.url,
                    title=doc.title,
                    content_hash=doc.content_hash,
                    last_modified=doc.last_modified
                )
                session.add(db_doc)
                session.commit()
                return db_doc.id
        finally:
            session.close()
    
    def get_document(self, url: str) -> Optional[DocModel]:
        """
        获取文档
        
        Args:
            url: 文档URL
        
        Returns:
            文档对象，如果不存在则返回None
        """
        session = self.get_session()
        try:
            db_doc = session.query(DocumentDB).filter_by(url=url).first()
            if not db_doc:
                return None
            
            # 获取最新版本内容
            latest_version = session.query(DocumentVersionDB)\
                .filter_by(document_id=db_doc.id)\
                .order_by(DocumentVersionDB.version.desc())\
                .first()
            
            content = latest_version.content if latest_version else ""
            
            return DocModel(
                url=db_doc.url,
                title=db_doc.title,
                content=content,
                content_hash=db_doc.content_hash,
                last_modified=db_doc.last_modified,
                crawled_at=db_doc.updated_at
            )
        finally:
            session.close()
    
    def get_all_documents(self) -> List[DocModel]:
        """
        获取所有文档
        
        Returns:
            文档列表
        """
        session = self.get_session()
        try:
            db_docs = session.query(DocumentDB).all()
            documents = []
            
            for db_doc in db_docs:
                # 获取最新版本内容
                latest_version = session.query(DocumentVersionDB)\
                    .filter_by(document_id=db_doc.id)\
                    .order_by(DocumentVersionDB.version.desc())\
                    .first()
                
                content = latest_version.content if latest_version else ""
                
                documents.append(DocModel(
                    url=db_doc.url,
                    title=db_doc.title,
                    content=content,
                    content_hash=db_doc.content_hash,
                    last_modified=db_doc.last_modified,
                    crawled_at=db_doc.updated_at
                ))
            
            return documents
        finally:
            session.close()
    
    def save_version(self, doc_id: int, content: str, content_hash: str) -> int:
        """
        保存文档版本
        
        Args:
            doc_id: 文档ID
            content: 文档内容
            content_hash: 内容哈希
        
        Returns:
            版本ID
        """
        session = self.get_session()
        try:
            # 获取当前最大版本号
            max_version = session.query(DocumentVersionDB.version)\
                .filter_by(document_id=doc_id)\
                .order_by(DocumentVersionDB.version.desc())\
                .first()
            
            next_version = (max_version[0] + 1) if max_version else 1
            
            # 创建新版本
            version = DocumentVersionDB(
                document_id=doc_id,
                content=content,
                content_hash=content_hash,
                version=next_version
            )
            session.add(version)
            session.commit()
            return version.id
        finally:
            session.close()
    
    def get_latest_version(self, doc_id: int) -> Optional[str]:
        """
        获取最新版本内容
        
        Args:
            doc_id: 文档ID
        
        Returns:
            文档内容，如果不存在则返回None
        """
        session = self.get_session()
        try:
            version = session.query(DocumentVersionDB)\
                .filter_by(document_id=doc_id)\
                .order_by(DocumentVersionDB.version.desc())\
                .first()
            
            return version.content if version else None
        finally:
            session.close()
    
    def save_scan_record(self, started_at: datetime, status: str = "running") -> int:
        """
        保存扫描记录
        
        Args:
            started_at: 开始时间
            status: 状态
        
        Returns:
            扫描记录ID
        """
        session = self.get_session()
        try:
            scan = ScanRecordDB(
                started_at=started_at,
                status=status
            )
            session.add(scan)
            session.commit()
            return scan.id
        finally:
            session.close()
    
    def update_scan_record(
        self,
        scan_id: int,
        completed_at: Optional[datetime] = None,
        status: Optional[str] = None,
        documents_scanned: Optional[int] = None,
        changes_detected: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> None:
        """
        更新扫描记录
        
        Args:
            scan_id: 扫描记录ID
            completed_at: 完成时间
            status: 状态
            documents_scanned: 扫描文档数
            changes_detected: 检测到的变更数
            error_message: 错误消息
        """
        session = self.get_session()
        try:
            scan = session.query(ScanRecordDB).filter_by(id=scan_id).first()
            if scan:
                if completed_at:
                    scan.completed_at = completed_at
                if status:
                    scan.status = status
                if documents_scanned is not None:
                    scan.documents_scanned = documents_scanned
                if changes_detected is not None:
                    scan.changes_detected = changes_detected
                if error_message:
                    scan.error_message = error_message
                session.commit()
        finally:
            session.close()
    
    def save_change(
        self,
        scan_id: int,
        document_id: int,
        change_type: str,
        diff: Optional[str] = None,
        summary: Optional[str] = None
    ) -> int:
        """
        保存变更记录
        
        Args:
            scan_id: 扫描记录ID
            document_id: 文档ID
            change_type: 变更类型
            diff: 差异内容
            summary: 摘要
        
        Returns:
            变更记录ID
        """
        session = self.get_session()
        try:
            change = ChangeDB(
                scan_id=scan_id,
                document_id=document_id,
                change_type=change_type,
                diff=diff,
                summary=summary
            )
            session.add(change)
            session.commit()
            return change.id
        finally:
            session.close()
    
    def save_notification(
        self,
        scan_id: int,
        channel: str,
        status: str = "pending"
    ) -> int:
        """
        保存通知记录
        
        Args:
            scan_id: 扫描记录ID
            channel: 通知渠道
            status: 状态
        
        Returns:
            通知记录ID
        """
        session = self.get_session()
        try:
            notification = NotificationDB(
                scan_id=scan_id,
                channel=channel,
                status=status
            )
            session.add(notification)
            session.commit()
            return notification.id
        finally:
            session.close()
    
    def update_notification(
        self,
        notification_id: int,
        status: str,
        sent_at: Optional[datetime] = None,
        error_message: Optional[str] = None
    ) -> None:
        """
        更新通知记录
        
        Args:
            notification_id: 通知记录ID
            status: 状态
            sent_at: 发送时间
            error_message: 错误消息
        """
        session = self.get_session()
        try:
            notification = session.query(NotificationDB).filter_by(id=notification_id).first()
            if notification:
                notification.status = status
                if sent_at:
                    notification.sent_at = sent_at
                if error_message:
                    notification.error_message = error_message
                session.commit()
        finally:
            session.close()
