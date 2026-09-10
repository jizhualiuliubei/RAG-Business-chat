import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_siliconflow_reranker_reorders_hits(monkeypatch):
    from app.core import reranker

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "results": [
                    {"index": 1, "relevance_score": 0.96},
                    {"index": 0, "relevance_score": 0.21},
                ]
            }).encode("utf-8")

    captured = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(reranker, "ENABLE_RERANK", True)
    monkeypatch.setattr(reranker, "SILICONFLOW_API_KEY", "sk-test")
    monkeypatch.setattr(reranker.request, "urlopen", fake_urlopen)

    hits = [
        {"distance": 0.9, "entity": {"text": "试用期导师反馈", "source": "员工手册.docx"}},
        {"distance": 0.5, "entity": {"text": "身份核验、学历核验和保密承诺签署", "source": "员工手册.docx"}},
    ]

    result = reranker.rerank_hits("入职前需要完成什么？", hits, top_k=1)

    assert result[0]["entity"]["text"].startswith("身份核验")
    assert result[0]["entity"]["rerank_score"] == 0.96
    assert captured["url"] == "https://api.siliconflow.cn/v1/rerank"
    assert captured["payload"]["model"] == "BAAI/bge-reranker-v2-m3"


def test_reranker_falls_back_to_original_order_on_error(monkeypatch):
    from app.core import reranker

    monkeypatch.setattr(reranker, "ENABLE_RERANK", True)
    monkeypatch.setattr(reranker, "SILICONFLOW_API_KEY", "sk-test")
    monkeypatch.setattr(reranker.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")))

    hits = [
        {"distance": 0.9, "entity": {"text": "A"}},
        {"distance": 0.5, "entity": {"text": "B"}},
    ]

    assert reranker.rerank_hits("query", hits, top_k=1) == hits[:1]
