"""
模型包入口
================
作用：集中导入所有 ORM 模型类。

为什么要这样写？
- main.py 里调用 Base.metadata.create_all() 建表时，
  SQLAlchemy 只会创建"已经被 import 过的模型"对应的表。
- 如果某个模型文件没被导入，它的表就不会被创建（一个常见坑）。
- 所以这里把所有模型都 import 一遍，确保建表时一个不漏。
"""
from app.models.knowledge_base import KnowledgeBase
from app.models.document import Document
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user_profile import UserProfile
from app.models.attachment import ConversationAttachment, ConversationAttachmentChunk
from app.models.user import User
from app.models.audit_log import AuditLog
from app.models.enterprise import Enterprise, EnterpriseModelKey
from app.models.evaluation import EvaluationRun, EvaluationCaseResult, EvaluationDataset, EvaluationDatasetCase

__all__ = [
    "KnowledgeBase", "Document", "Conversation", "Message", "UserProfile",
    "ConversationAttachment", "ConversationAttachmentChunk", "User", "AuditLog", "Enterprise", "EnterpriseModelKey", "EvaluationRun",
    "EvaluationCaseResult", "EvaluationDataset", "EvaluationDatasetCase",
]
