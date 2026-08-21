"""CLI tests via typer's CliRunner (same committed dataset)."""

import json

from src.cli import app


def combined(result) -> str:
    """stdout + stderr (typer CliRunner keeps them separate)."""
    return (result.stdout or "") + (result.stderr or "")


class TestHealthAndStats:
    def test_health(self, runner):
        result = runner.invoke(app, ["health"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "ok"
        assert payload["dataset"] == "nvidia"
        assert payload["companies"] == 22

    def test_stats(self, runner):
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0
        assert "companies" in result.stdout
        assert "relationships" in result.stdout


class TestCompanies:
    def test_list_json(self, runner):
        result = runner.invoke(app, ["companies", "--json", "--page-size", "100"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["total"] == 22

    def test_list_filter(self, runner):
        result = runner.invoke(app, ["companies", "--name", "micro", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["total"] == 3  # amd, micron, microsoft

    def test_get(self, runner):
        result = runner.invoke(app, ["company", "nvidia"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["name"] == "NVIDIA Corporation"
        assert payload["entity_type"] == "target"

    def test_get_unknown_exits_1(self, runner):
        result = runner.invoke(app, ["company", "nope"])
        assert result.exit_code == 1
        assert "Unknown company" in combined(result)


class TestRelationships:
    def test_list_json(self, runner):
        result = runner.invoke(
            app,
            ["relationships", "--type", "supplier", "--min-score", "70", "--json"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["total"] == 3
        ids = [i["id"] for i in payload["items"]]
        assert ids == ["rel_sup_001", "rel_sup_002", "rel_sup_003"]

    def test_list_valid_as_of(self, runner):
        result = runner.invoke(
            app,
            ["relationships", "--valid-as-of", "2024-06-30", "--json", "--page-size", "100"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["total"] == 20
        ids = {i["id"] for i in payload["items"]}
        assert "rel_par_004" not in ids
        assert "rel_par_005" in ids

    def test_list_invalid_date_exits_1(self, runner):
        result = runner.invoke(app, ["relationships", "--valid-as-of", "2024-13-99"])
        assert result.exit_code == 1
        assert "Invalid --valid-as-of" in combined(result)

    def test_detail_json(self, runner):
        result = runner.invoke(app, ["relationship", "rel_inv_001", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["id"] == "rel_inv_001"
        assert payload["confidence_score"] == 86
        assert len(payload["evidence"]) == 3
        assert payload["score_breakdown"]["band"] == "confirmed"

    def test_detail_unknown_exits_1(self, runner):
        result = runner.invoke(app, ["relationship", "nope"])
        assert result.exit_code == 1


class TestEvidenceAndScore:
    def test_evidence(self, runner):
        result = runner.invoke(app, ["evidence", "ev_sup_001"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["relationship_id"] == "rel_sup_001"
        assert payload["access_restriction"] == "public"

    def test_evidence_unknown_exits_1(self, runner):
        result = runner.invoke(app, ["evidence", "nope"])
        assert result.exit_code == 1

    def test_score_recompute_matches_stored(self, runner):
        result = runner.invoke(app, ["score", "rel_sup_004"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["stored_score"] == 60
        assert payload["stored_status"] == "inferred"
        assert payload["band"] == payload["stored_status"]
        assert round(payload["total"]) == payload["stored_score"]

    def test_score_unknown_exits_1(self, runner):
        result = runner.invoke(app, ["score", "nope"])
        assert result.exit_code == 1


class TestGraph:
    def test_graph_json(self, runner):
        result = runner.invoke(app, ["graph", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["research_target"] == "nvidia"
        assert len(payload["nodes"]) == 22
        assert len(payload["edges"]) == 21


class TestHumanReadableOutput:
    """Non-JSON table / detail output must still work (exit 0, key text).

    Rich tables truncate cell content to the terminal width, so assertions
    target the table title and the plain-text detail lines instead of
    individual cells.
    """

    def test_companies_table(self, runner):
        result = runner.invoke(app, ["companies", "--page-size", "5"])
        assert result.exit_code == 0
        assert "Companies (page 1/5)" in result.stdout
        assert "accenture" in result.stdout  # first company alphabetically

    def test_relationships_table(self, runner):
        result = runner.invoke(app, ["relationships", "--type", "supplier"])
        assert result.exit_code == 0
        assert "Relationships (page 1/1)" in result.stdout
        # 4 suppliers, all still active → the valid_until column shows 'active' 4 times.
        assert result.stdout.count("active") == 4

    def test_relationship_detail_text(self, runner):
        result = runner.invoke(app, ["relationship", "rel_inv_001"])
        assert result.exit_code == 0
        assert "breakdown:" in result.stdout
        assert "evidence ev_inv_001" in result.stdout
        assert "coreweave.com" in result.stdout

    def test_graph_table(self, runner):
        result = runner.invoke(app, ["graph"])
        assert result.exit_code == 0
        assert "tsmc" in result.stdout
        assert "supplier" in result.stdout

    def test_load_failure_exits_1(self, runner):
        result = runner.invoke(
            app, ["health"], env={"SCR_DATA_DIR": "/nonexistent/data"}
        )
        assert result.exit_code == 1
        assert "Failed to load dataset" in combined(result)


class TestMultiTarget:
    def test_targets_command(self, runner):
        result = runner.invoke(app, ["targets", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["default_target"] == "nvidia"
        assert {t["id"] for t in payload["targets"]} >= {"nvidia", "unitree"}

    def test_target_option_switch(self, runner):
        result = runner.invoke(app, ["--target", "unitree", "health"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["dataset"] == "unitree"
        assert payload["companies"] == 6

    def test_unknown_target_exits_1(self, runner):
        result = runner.invoke(app, ["--target", "nope", "health"])
        assert result.exit_code == 1
        assert "Failed to load dataset" in combined(result)
