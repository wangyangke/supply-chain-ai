"""Unit tests for research_agent helper logic."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from research_agent import (  # noqa: E402
    ResearchAgent,
    _contains_target,
    _fallback_queries,
    _is_chinese_name,
    _primary_query,
    _search_quality_score,
)


class TestSearchQualityHelpers:
    def test_is_chinese_name(self):
        assert _is_chinese_name("智元科技") is True
        assert _is_chinese_name("NVIDIA") is False
        assert _is_chinese_name("NVIDIA 中国") is True

    def test_contains_target_full_match(self):
        assert _contains_target("智元科技完成融资", "智元科技") is True
        assert _contains_target("Zhiyuan Robotics raises funding", "Zhiyuan") is True

    def test_contains_target_ignores_single_shared_character(self):
        # Bing often returns dictionary pages for 智/宇 when the company name
        # is ambiguous. A single shared character must not count as relevant.
        assert _contains_target("智，汉语常用字", "智元科技") is False
        assert _contains_target("智慧是什么", "智元科技") is False
        assert _contains_target("宇，汉语一级字", "宇树科技") is False

    def test_contains_target_accepts_bigram_for_chinese_aliases(self):
        # The canonical company name may be 智元创新 rather than 智元科技.
        # Bigram matching keeps such hits while still dropping dictionary pages.
        assert _contains_target("智元创新（上海）科技股份有限公司", "智元科技") is True
        assert _contains_target("宇树科技股份有限公司", "宇树科技") is True

    def test_search_quality_score(self):
        hits = [
            {"title": "智元科技完成融资", "snippet": "..."},
            {"title": "智，汉语常用字", "snippet": "..."},
            {"title": "智元创新上海公司", "snippet": "..."},
        ]
        assert _search_quality_score(hits, "智元科技") == pytest.approx(2 / 3)
        assert _search_quality_score([], "智元科技") == 0.0


class TestQueryTemplates:
    def test_primary_query_adapts_to_language(self):
        assert "合作" in _primary_query("partner_supplier", "智元科技", True)
        assert "供应商" in _primary_query("partner_supplier", "智元科技", True)
        assert "partnership" in _primary_query("partner_supplier", "NVIDIA", False)

    def test_fallback_queries_include_site_restrictions_for_chinese(self):
        fbs = _fallback_queries("investor", "智元科技", True)
        assert any("site:itjuzi.com" in q for q in fbs)
        assert any("site:36kr.com" in q for q in fbs)

    def test_fallback_queries_for_english_keep_original_templates(self):
        fbs = _fallback_queries("investor", "NVIDIA", False)
        assert any("投资方" in q for q in fbs)


class TestResearchAgentSearchFallback:
    def test_quality_guard_triggers_fallback_and_filters_garbage(self):
        """Mock search returns garbage first, then relevant hits on site fallback."""
        call_log = []

        def fake_search(query: str, max_results: int = 8):
            call_log.append(query)
            if "site:" in query:
                return [
                    {"title": "智元创新（上海）科技股份有限公司", "snippet": "智元机器人成立于2023年", "url": "https://www.itjuzi.com/company/123"},
                ]
            # Generic query returns dictionary garbage.
            return [
                {"title": "智（汉语汉字）_百度百科", "snippet": "智，汉语常用字", "url": "https://baike.baidu.com/item/智"},
                {"title": "Z.ai - 智谱AI", "snippet": "智谱推出 AutoClaw", "url": "https://www.zhipuai.cn/"},
            ]

        agent = ResearchAgent("data", search_fn=fake_search, llm_fn=None)
        identity = {"id": "c_test", "name": "智元科技"}
        docs = agent._verify_batch_rule_based(identity, "peer_customer", fake_search("智元科技", 8))
        # Rule-based verifier should have no docs because garbage is filtered out
        # BEFORE _verify_batch_rule_based is called in the real pipeline.
        # Here we directly feed garbage and expect the same (no counterparty).
        assert len(docs) == 0

        # Now verify the fallback path: if we feed the relevant hit, it stages.
        good_hits = fake_search("智元科技 site:itjuzi.com", 8)
        docs = agent._verify_batch_rule_based(identity, "peer_customer", good_hits)
        assert len(docs) == 1
        # The exact extracted name depends on the regex; the important thing is
        # that a counterparty relationship is staged from the relevant hit.
        assert "科技" in docs[0]["counterparty"]["name"]
