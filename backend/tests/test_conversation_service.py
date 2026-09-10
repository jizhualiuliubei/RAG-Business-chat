from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.attachment import ConversationAttachment
from app.models.conversation import Conversation
from app.models.enterprise import Enterprise
from app.models.message import Message
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services import auth_service
from app.services import conversation_service
from app.services import enterprise_service


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def test_delete_conversation_removes_messages_attachments_profiles_and_files(tmp_path):
    db = _session()
    try:
        conv = Conversation(title="删除会话级联测试")
        db.add(conv)
        db.commit()
        db.refresh(conv)

        db.add(Message(conversation_id=conv.id, role="user", content="你好"))
        profile = UserProfile(conversation_id=conv.id)
        profile.set_fields({"name": "测试用户"})
        db.add(profile)
        attachment_path = tmp_path / "note.txt"
        attachment_path.write_text("临时附件内容", encoding="utf-8")
        db.add(
            ConversationAttachment(
                conversation_id=conv.id,
                filename="note.txt",
                stored_path=str(attachment_path),
                content="临时附件内容",
            )
        )
        db.commit()

        assert conversation_service.delete_conversation(db, conv.id) is True

        assert db.query(Message).filter(Message.conversation_id == conv.id).count() == 0
        assert db.query(ConversationAttachment).filter(
            ConversationAttachment.conversation_id == conv.id
        ).count() == 0
        assert db.query(UserProfile).filter(UserProfile.conversation_id == conv.id).count() == 0
        assert not attachment_path.exists()
        assert db.get(Conversation, conv.id) is None
    finally:
        db.close()


def test_legacy_conversations_are_migrated_to_enterprise_admin(tmp_path):
    db = _session()
    try:
        enterprise = Enterprise(name="测试企业", code="acme", status="active")
        db.add(enterprise)
        db.commit()
        db.refresh(enterprise)
        admin = User(
            enterprise_id=enterprise.id,
            username="admin",
            password_hash=auth_service.hash_password("Admin123456"),
            display_name="企业管理员",
            role="enterprise_admin",
            status="active",
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        conv = Conversation(enterprise_id=enterprise.id, title="旧会话")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        db.add(Message(enterprise_id=enterprise.id, conversation_id=conv.id, role="user", content="你好"))
        attachment_path = tmp_path / "legacy.txt"
        attachment_path.write_text("legacy", encoding="utf-8")
        db.add(
            ConversationAttachment(
                enterprise_id=enterprise.id,
                conversation_id=conv.id,
                filename="legacy.txt",
                stored_path=str(attachment_path),
                content="legacy",
            )
        )
        db.commit()

        enterprise_service.migrate_legacy_conversations_to_enterprise_admin(db)

        db.refresh(conv)
        assert conv.user_id == admin.id
        assert db.query(Message).filter(Message.conversation_id == conv.id).one().user_id == admin.id
        assert db.query(ConversationAttachment).filter(
            ConversationAttachment.conversation_id == conv.id
        ).one().user_id == admin.id
    finally:
        db.close()
