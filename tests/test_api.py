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
