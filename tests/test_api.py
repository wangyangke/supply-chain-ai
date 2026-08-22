"""FastAPI endpoint tests: happy paths, filtering, error responses."""


class TestMeta:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["dataset"] == "nvidia"
        assert body["companies"] == 22
        assert body["relationships"] == 21
        assert body["evidence"] == 29

    def test_stats(self, client):
        resp = client.get("/api/v1/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["research_target"] == "nvidia"
        assert body["relationships_by_type"]["supplier"] == 4
        assert body["relationships_by_type"]["customer"] == 5
        assert body["relationships_by_type"]["partner"] == 5
        assert body["relationships_by_status"]["confirmed"] == 17
        assert body["relationships_by_status"]["inferred"] == 4


class TestCompanies:
    def test_list(self, client):
        resp = client.get("/api/v1/companies", params={"page_size": 100})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 22
        assert len(body["items"]) == 22
        assert body["has_next"] is False

    def test_list_filter_and_pagination(self, client):
        resp = client.get(
            "/api/v1/companies",
            params={"name": "micro", "page_size": 1},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3  # amd, micron, microsoft all contain 'micro'
        assert len(body["items"]) == 1
        assert body["has_next"] is True

    def test_get(self, client):
        resp = client.get("/api/v1/companies/nvidia")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "NVIDIA Corporation"
        assert body["stock_code"] == "NVDA"
        assert body["entity_type"] == "target"

    def test_get_404(self, client):
        resp = client.get("/api/v1/companies/nope")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "company_not_found"


class TestRelationships:
    def test_list_all(self, client):
        resp = client.get("/api/v1/relationships", params={"page_size": 100})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 21
        assert len(body["items"]) == 21

    def test_filter_by_type_and_score(self, client):
        resp = client.get(
            "/api/v1/relationships",
            params={"relationship_type": "supplier", "min_confidence": 70, "page_size": 100},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3  # asml (60) excluded
        assert all(
            i["type"] == "supplier" and i["confidence_score"] >= 70
            for i in body["items"]
        )

    def test_filter_invalid_type_422(self, client):
        resp = client.get("/api/v1/relationships", params={"relationship_type": "banana"})
        assert resp.status_code == 422

    def test_filter_invalid_status_422(self, client):
        resp = client.get("/api/v1/relationships", params={"status": "maybe"})
        assert resp.status_code == 422

    def test_min_greater_than_max_422(self, client):
        resp = client.get(
            "/api/v1/relationships",
            params={"min_confidence": 90, "max_confidence": 50},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "invalid_range"

    def test_confidence_out_of_bounds_422(self, client):
        resp = client.get("/api/v1/relationships", params={"min_confidence": 101})
        assert resp.status_code == 422

    def test_valid_as_of_filter(self, client):
        resp = client.get(
            "/api/v1/relationships",
            params={"valid_as_of": "2024-06-30", "page_size": 100},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 20
        ids = {i["id"] for i in body["items"]}
        assert "rel_par_004" not in ids  # valid_from 2025-02-01, after 2024-06-30
        assert "rel_par_005" in ids      # oracle partnership valid since 2023-03-21

    def test_invalid_valid_as_of_422(self, client):
        resp = client.get("/api/v1/relationships", params={"valid_as_of": "not-a-date"})
        assert resp.status_code == 422

    def test_detail_with_score_breakdown(self, client):
        resp = client.get("/api/v1/relationships/rel_inv_001")
        assert resp.status_code == 200
        body = resp.json()
        assert body["confidence_score"] == 86
        assert body["status"] == "confirmed"
        assert len(body["evidence"]) == 3
        bd = body["score_breakdown"]
        assert bd["band"] == "confirmed"
        assert set(bd["dimensions"].keys()) == {
            "authority", "evidence_quality", "recency", "specificity", "quantifiability",
        }
        assert abs(bd["total"] - sum(bd["dimensions"].values())) < 1e-6

    def test_detail_404(self, client):
        resp = client.get("/api/v1/relationships/nope")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "relationship_not_found"

    def test_relationship_evidence(self, client):
        resp = client.get("/api/v1/relationships/rel_sup_001/evidence")
        assert resp.status_code == 200
        body = resp.json()
        assert body["relationship_id"] == "rel_sup_001"
        assert len(body["evidence"]) == 1
        assert body["evidence"][0]["id"] == "ev_sup_001"

    def test_relationship_evidence_404(self, client):
        resp = client.get("/api/v1/relationships/nope/evidence")
        assert resp.status_code == 404


class TestEvidence:
    def test_get(self, client):
        resp = client.get("/api/v1/evidence/ev_sup_001")
        assert resp.status_code == 200
        body = resp.json()
        assert body["relationship_id"] == "rel_sup_001"
        assert body["source_type"] == "sec_filing"
        assert body["access_restriction"] == "public"
        assert body["source_url"].startswith("https://")

    def test_get_404(self, client):
        resp = client.get("/api/v1/evidence/nope")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "evidence_not_found"


class TestGraph:
    def test_graph(self, client):
        resp = client.get("/api/v1/graph")
        assert resp.status_code == 200
        body = resp.json()
        assert body["research_target"] == "nvidia"
        assert len(body["nodes"]) == 22
        assert len(body["edges"]) == 21
        node_ids = {n["id"] for n in body["nodes"]}
        assert node_ids == {
            "nvidia", "tsmc", "sk_hynix", "micron", "asml", "microsoft",
            "meta", "amazon", "alphabet", "dell", "accenture", "servicenow",
            "snowflake", "cisco", "coreweave", "recursion", "soundhound",
            "amd", "intel", "broadcom", "qualcomm", "oracle",
        }
        # Every edge references known nodes.
        for e in body["edges"]:
            assert e["source"] in node_ids
            assert e["target"] in node_ids


class TestDashboard:
    def test_dashboard_root(self, client):
        """GET / should serve the interactive HTML dashboard."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "<html" in resp.text.lower()

    def test_dashboard_alias(self, client):
        """GET /dashboard should also serve the dashboard."""
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")


class TestMultiTarget:
    """Multi-target registry: /api/v1/targets + ?target= switching."""

    def test_list_targets(self, client):
        resp = client.get("/api/v1/targets")
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_target"] == "nvidia"
        ids = {t["id"] for t in body["targets"]}
        assert {"nvidia", "unitree"} <= ids
        nv = next(t for t in body["targets"] if t["id"] == "nvidia")
        assert nv["is_default"] is True
        assert nv["stock_code"] == "NVDA"

    def test_switch_to_unitree(self, client):
        resp = client.get("/api/v1/stats", params={"target": "unitree"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["research_target"] == "unitree"
        assert body["companies"] == 6
        assert body["relationships"] == 5
        assert body["evidence"] == 9

    def test_unitree_relationship_detail(self, client):
        resp = client.get("/api/v1/relationships/rel_par_001", params={"target": "unitree"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_company_id"] == "nvidia"
        assert body["target_company_id"] == "unitree"
        assert body["type"] == "partner"
        assert len(body["evidence"]) == 3
        assert body["score_breakdown"]["band"] == body["status"]

    def test_default_target_unaffected(self, client):
        resp = client.get("/api/v1/stats")
        assert resp.json()["research_target"] == "nvidia"

    def test_unknown_target_404(self, client):
        resp = client.get("/api/v1/stats", params={"target": "nope"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "target_not_found"

    def test_unitree_graph(self, client):
        resp = client.get("/api/v1/graph", params={"target": "unitree"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["research_target"] == "unitree"
        assert len(body["nodes"]) == 6
        assert len(body["edges"]) == 5
        node_ids = {n["id"] for n in body["nodes"]}
        assert node_ids == {"unitree", "nvidia", "meituan", "tencent", "alibaba", "ubtech"}


class TestResearchAgent:
    """Online research endpoints: POST /api/v1/research + polling."""

    def test_zero_config_accepted_202(self, client, monkeypatch):
        """Without any API keys the endpoint still accepts the job (DuckDuckGo
        + rule-based fallback).  It no longer hard-fails with 503."""
        for var in ("SCR_TAVILY_API_KEY", "SCR_BRAVE_API_KEY",
                    "SCR_LLM_BASE_URL", "SCR_LLM_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        resp = client.post("/api/v1/research", json={"query": "ZeroConfigTestCorp"})
        assert resp.status_code == 202
        assert "job_id" in resp.json()

    def test_invalid_query_422(self, client):
        resp = client.post("/api/v1/research", json={"query": "x"})
        assert resp.status_code == 422

    def test_existing_target_409(self, client, monkeypatch):
        monkeypatch.setenv("SCR_TAVILY_API_KEY", "dummy")
        monkeypatch.setenv("SCR_LLM_BASE_URL", "http://localhost:1/v1")
        monkeypatch.setenv("SCR_LLM_API_KEY", "dummy")
        resp = client.post("/api/v1/research", json={"query": "nvidia"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "target_exists"

    def test_unknown_job_404(self, client):
        resp = client.get("/api/v1/research/deadbeef0000")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "job_not_found"

    def test_full_job_lifecycle(self, client, monkeypatch, tmp_path):
        """Mock the agent class -> job runs -> new target becomes servable."""
        import json as _json
        import time

        import src.api as api_mod

        # tmp data root: registry + symlinked real targets
        (tmp_path / "targets").mkdir()
        real_root = api_mod.get_registry().root
        registry = _json.loads((real_root / "targets.json").read_text())
        (tmp_path / "targets.json").write_text(_json.dumps(registry))
        for t in registry["targets"]:
            (tmp_path / "targets" / t["id"]).symlink_to(real_root / "targets" / t["id"])

        monkeypatch.setenv("SCR_TAVILY_API_KEY", "dummy")
        monkeypatch.setenv("SCR_LLM_BASE_URL", "http://localhost:1/v1")
        monkeypatch.setenv("SCR_LLM_API_KEY", "dummy")
        monkeypatch.setenv("SCR_DATA_ROOT", str(tmp_path))

        class FakeAgent:
            def __init__(self, data_root, on_step=None):
                self.data_root = data_root
                self.on_step = on_step or (lambda n, d: None)

            def run(self, query):
                from pathlib import Path
                root = Path(self.data_root)
                tdir = root / "targets" / "testco"
                (tdir / "staging").mkdir(parents=True)
                (tdir / "dataset.json").write_text(_json.dumps(
                    {"schema_version": "1.0", "as_of": "2026-08-21", "research_target": "testco"}))
                (tdir / "companies.json").write_text(_json.dumps([{
                    "id": "testco", "name": "Test Co", "stock_code": "TEST",
                    "exchange": "NASDAQ", "isin": "", "country": "US",
                    "entity_type": "target", "sector": "testing",
                    "description": "Mock target from test."}]))
                (tdir / "relationships.json").write_text("[]")
                (tdir / "evidence.json").write_text("[]")
                reg = _json.loads((root / "targets.json").read_text())
                reg["targets"].append(
                    {"id": "testco", "name": "Test Co", "stock_code": "TEST",
                     "exchange": "NASDAQ", "path": "targets/testco", "description": "mock"})
                (root / "targets.json").write_text(_json.dumps(reg))
                self.on_step("done", "mock finished")
                return {"target_id": "testco", "name": "Test Co",
                        "companies": 1, "relationships": 0, "evidence": 0}

        monkeypatch.setattr(api_mod, "_load_research_agent_class", lambda: FakeAgent)
        monkeypatch.setattr(api_mod, "_registry", None)
        monkeypatch.setattr(api_mod, "_stores", {})

        try:
            resp = client.post("/api/v1/research", json={"query": "Test Co"})
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]

            for _ in range(100):
                job = client.get(f"/api/v1/research/{job_id}").json()
                if job["status"] != "running":
                    break
                time.sleep(0.05)
            assert job["status"] == "done", job
            assert job["result"]["target_id"] == "testco"
            assert any(s["step"] == "done" for s in job["steps"])

            # new target is registered and servable
            targets = client.get("/api/v1/targets").json()
            assert "testco" in {t["id"] for t in targets["targets"]}
            stats = client.get("/api/v1/stats", params={"target": "testco"}).json()
            assert stats["research_target"] == "testco"
            assert stats["companies"] == 1
            # dataset snapshot endpoint (dashboard lazy-load)
            snap = client.get("/api/v1/targets/testco/dataset").json()
            assert snap["dataset"]["research_target"] == "testco"
            assert "testco" in snap["companies"]
        finally:
            monkeypatch.setattr(api_mod, "_registry", None)
            monkeypatch.setattr(api_mod, "_stores", {})

    def test_dataset_endpoint_404(self, client):
        resp = client.get("/api/v1/targets/nope/dataset")
        assert resp.status_code == 404
