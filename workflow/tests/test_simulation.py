"""
tests/test_simulation.py
Unit and integration tests for Workflow Efficiency Experiment and Simulation Engine.
"""

from pathlib import Path
import pytest
from app import app
from simulate import (
    generate_experiment_cases,
    sample_review_times,
    simulate_queue,
    run_experiment,
)
from config import OUTPUTS_DIR, IMAGES_DIR


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_generate_experiment_cases():
    """Verifies deterministic cohort creation with Poisson arrivals."""
    cases = generate_experiment_cases(num_cases=20, seed=42)
    assert len(cases) == 20
    assert cases[0]["arrival_minute"] > 0
    # Check arrivals are strictly increasing
    arrivals = [c["arrival_minute"] for c in cases]
    assert arrivals == sorted(arrivals)
    assert all("retrieval_precision" in c for c in cases)


def test_sample_review_times_reduction():
    """
    Verifies that for identical cases, CBIR modes achieve systematic reductions in review time.
    """
    cases = generate_experiment_cases(num_cases=30, seed=42)
    times_base = sample_review_times(cases, mode="baseline", seed=42)
    times_cbir = sample_review_times(cases, mode="cbir", seed=42)
    times_rerank = sample_review_times(cases, mode="cbir_rerank", seed=42)

    # For each case, base >= cbir >= cbir_rerank
    for c in cases:
        cid = c["case_id"]
        assert times_base[cid] >= times_cbir[cid]
        assert times_cbir[cid] >= times_rerank[cid]


def test_run_experiment_and_exports():
    """
    Verifies full experiment execution, CSV creation, chart rendering, and statistical test output.
    """
    summary_df, cases_df, stats_dict = run_experiment(num_cases=50, seed=42)

    assert len(summary_df) == 3
    assert set(summary_df["mode"]) == {"baseline", "cbir", "cbir_rerank"}
    assert len(cases_df) == 50

    # Check files exist
    assert (OUTPUTS_DIR / "experiment_results.csv").exists()
    assert (OUTPUTS_DIR / "experiment_cases.csv").exists()
    assert (IMAGES_DIR / "experiment_charts.png").exists()

    # Check Wilcoxon test statistics
    assert "wilcoxon_cbir" in stats_dict
    assert stats_dict["wilcoxon_cbir"]["p_value"] < 0.001
    assert stats_dict["wilcoxon_rerank"]["p_value"] < 0.001


def test_experiment_page_renders(client):
    """Verifies GET /experiment renders table, charts, and methodology."""
    resp = client.get("/experiment")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Workflow Efficiency Experiment" in html
    assert "Context-aware similar-case retrieval reduces case-review time" in html
    assert "Comparative Performance Metrics" in html
    assert "experiment_charts.png" in html
    assert "Wilcoxon p-value" in html


def test_api_experiment_run(client):
    """Verifies POST /api/experiment/run endpoint returns 200 with summary and stats."""
    resp = client.post("/api/experiment/run", json={"cases": 30, "seed": 42})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert "summary" in data
    assert "stats" in data
