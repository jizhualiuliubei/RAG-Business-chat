from datetime import datetime

from app.schemas.conversation import MessageOut


def test_message_sources_keep_knowledge_base_name():
    msg = MessageOut(
        id=1,
        role="assistant",
        content="回答",
        sources=[
            {
                "text": "片段",
                "source": "制度.txt",
                "chunk_id": 0,
                "score": 0.9,
                "kb_name": "默认知识库",
            }
        ],
        created_at=datetime.now(),
    )

    assert msg.model_dump()["sources"][0]["kb_name"] == "默认知识库"
