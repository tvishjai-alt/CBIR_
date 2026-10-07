"""
app.py
Flask Application for Tele-Radiology Workflow Management System (Member 3).

Exposes:
- Core Workflow JSON APIs:
    - POST /api/cases/new          (Platform Webhook Intake)
    - GET  /api/stats              (Overall Workflow KPIs)
    - GET  /api/worklist/<rad_id>  (Prioritized Specialist Worklist)
    - POST /api/case/<id>/start    (Transition to In_Review)
    - POST /api/case/<id>/report   (Submit Report and Turnaround)
    - POST /api/case/<id>/return   (Return Case to Referring Clinic)
    - POST /api/rebalance          (Balance Specialist Queues)
    - GET  /api/health             (Service Health Check)
- Frontend Web View Routes:
    - GET  /                       (Role Selection Landing Page)
    - GET  /worklist/<rad_id>      (Radiologist Worklist View)
    - GET  /case/<case_id>         (Radiologist Diagnostic & CBIR View)
    - GET  /hospital               (Hospital Management & Chart.js Dashboard)
    - GET  /clinic/<clinic_id>     (Clinic Status Portal)
    - GET  /experiment             (CBIR Efficiency Experiment Results)
"""

import json
import logging
from html import escape
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
import pandas as pd

from flask import Flask, Response, jsonify, render_template, request, abort

from config import (
    BASE_DIR,
    DATABASE_PATH,
    SLA_LIMITS_MINUTES,
    SLA_AT_RISK_RATIO,
    USE_MOCK_CBIR,
    SIMULATION_CONFIG,
)
from database import get_db_connection, query_all, query_one
from adapters.platform_adapter import ingest_platform_case, get_platform_case_details, get_clinic_cases_summary
from adapters.cbir_adapter import get_similar_cases
from cbir_context import save_context
import workflow_engine
import routing
import priority

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("flask_app")

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static")
)
app.config["JSON_SORT_KEYS"] = False


@app.errorhandler(404)
def not_found_handler(err):
    """Missing pool thumbnails under /static/images/ get a placeholder instead of a broken icon."""
    if request.path.startswith("/static/images/"):
        label = escape(Path(request.path).stem)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="224" height="224">'
            '<rect width="224" height="224" fill="#0b0b0f"/>'
            f'<text x="112" y="108" fill="#f9a8d4" font-size="13" text-anchor="middle" font-family="sans-serif">{label}</text>'
            '<text x="112" y="130" fill="#a98799" font-size="11" text-anchor="middle" font-family="sans-serif">not in local pool</text>'
            '</svg>'
        )
        return Response(svg, mimetype="image/svg+xml")
    return err


# ============================================================================
# JSON API ROUTES (Member 1 & Member 2 Integration + Internal AJAX)
# ============================================================================

@app.route("/api/case/<case_id>/info", methods=["GET"])
def api_case_info(case_id: str):
    """Returns full case details and completed diagnostic report if available."""
    details = get_platform_case_details(case_id)
    if not details:
        return jsonify({"status": "error", "message": f"Case '{case_id}' not found."}), 404
    return jsonify({"status": "success", "case": details}), 200

@app.route("/api/hospital/analytics", methods=["GET"])
def api_hospital_analytics():
    """Aggregates all clinical analytics for the hospital dashboard."""
    import analytics
    try:
        workloads = analytics.get_radiologist_workloads()
        sla_perf = analytics.get_sla_performance()
        turnaround = analytics.get_turnaround_statistics()
        throughput = analytics.get_daily_throughput()
        pending_priority = analytics.get_pending_queue_by_priority()

        return jsonify({
            "status": "success",
            "workloads": workloads,
            "sla": sla_perf,
            "turnaround": turnaround,
            "throughput": throughput,
            "pending_priority": pending_priority,
        }), 200
    except Exception as exc:
        logger.error(f"Error fetching hospital analytics: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500

@app.route("/api/health", methods=["GET"])
def api_health():
    """Health check verifying database accessibility and system status."""
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1;").fetchone()
        conn.close()
        db_healthy = True
    except Exception as e:
        logger.error(f"Health check database failure: {e}")
        db_healthy = False

    return jsonify({
        "status": "healthy" if db_healthy else "unhealthy",
        "database_connected": db_healthy,
        "mock_cbir_enabled": USE_MOCK_CBIR,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), (200 if db_healthy else 503)


@app.route("/api/cases/new", methods=["POST"])
def api_case_new():
    """
    Webhook endpoint called by Member 2's clinic upload portal.
    Payload: {case_id, clinic_id, patient_ref, modality, body_part, urgency_flag, image_path, upload_time}
    Triggers automated pipeline: Upload -> Store -> CBIR -> Priority -> Routing.
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Request body must be application/json"}), 400

    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Missing JSON payload"}), 400

    required_fields = ["clinic_id", "modality", "body_part"]
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        return jsonify({
            "status": "error",
            "message": f"Missing required fields: {', '.join(missing)}"
        }), 400

    try:
        save_context(data)  # remembers age/gender/view/image for Member 1's CBIR
        case_result = ingest_platform_case(data)
        logger.info(f"Webhook created case {case_result['case_id']} assigned to {case_result.get('assigned_rad_id')}")
        return jsonify({
            "status": "success",
            "message": "Case ingested, prioritized, and routed successfully.",
            "data": case_result
        }), 201
    except ValueError as val_err:
        logger.warning(f"Validation error on /api/cases/new: {val_err}")
        return jsonify({"status": "error", "message": str(val_err)}), 400
    except Exception as exc:
        logger.error(f"Unexpected error creating case: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": f"Server error: {str(exc)}"}), 500


@app.route("/api/stats", methods=["GET"])
def api_stats():
    """
    Returns high-level operational statistics and SLA performance metrics.
    """
    conn = get_db_connection()
    try:
        total_cases = conn.execute("SELECT COUNT(*) as c FROM cases;").fetchone()["c"]
        completed_cases = conn.execute(
            "SELECT COUNT(*) as c FROM cases WHERE status IN ('Reported', 'Returned_To_Clinic');"
        ).fetchone()["c"]
        pending_cases = total_cases - completed_cases

        critical_pending = conn.execute(
            """
            SELECT COUNT(*) as c FROM cases
            WHERE priority_label = 'Critical'
              AND status NOT IN ('Reported', 'Returned_To_Clinic');
            """
        ).fetchone()["c"]

        active_rads = conn.execute(
            "SELECT COUNT(*) as c FROM radiologists WHERE is_active = 1;"
        ).fetchone()["c"]

        # Calculate SLA breaches and turnaround averages for completed cases
        turnaround_query = """
            SELECT
                c.case_id,
                c.priority_label,
                s.max_minutes as sla_max,
                (julianday(e_rep.timestamp) - julianday(e_up.timestamp)) * 1440.0 as turnaround_mins
            FROM cases c
            JOIN events e_up ON c.case_id = e_up.case_id AND e_up.status = 'Uploaded'
            JOIN events e_rep ON c.case_id = e_rep.case_id AND e_rep.status = 'Reported'
            JOIN sla_config s ON c.priority_label = s.priority_label
            WHERE c.status IN ('Reported', 'Returned_To_Clinic');
        """
        rows = conn.execute(turnaround_query).fetchall()

        if rows:
            turnarounds = [r["turnaround_mins"] for r in rows]
            avg_turnaround = round(sum(turnarounds) / len(turnarounds), 1)
            breaches = sum(1 for r in rows if r["turnaround_mins"] > r["sla_max"])
            breach_rate = round((breaches / len(rows)) * 100.0, 1)
        else:
            avg_turnaround = 0.0
            breaches = 0
            breach_rate = 0.0

        return jsonify({
            "status": "success",
            "stats": {
                "total_cases": total_cases,
                "pending_cases": pending_cases,
                "completed_cases": completed_cases,
                "critical_pending": critical_pending,
                "active_radiologists": active_rads,
                "avg_turnaround_minutes": avg_turnaround,
                "sla_breach_count": breaches,
                "sla_breach_rate_pct": breach_rate,
            }
        }), 200
    finally:
        conn.close()


@app.route("/api/worklist/<rad_id>", methods=["GET"])
def api_worklist(rad_id: str):
    """
    Returns prioritized case list for a specific radiologist, sorted by priority_score descending.
    Includes SLA countdown, time waiting, and priority explainability.
    """
    conn = get_db_connection()
    try:
        rad = conn.execute("SELECT * FROM radiologists WHERE rad_id = ?;", (rad_id,)).fetchone()
        if not rad:
            return jsonify({"status": "error", "message": f"Radiologist '{rad_id}' not found."}), 404

        cases_rows = conn.execute(
            """
            SELECT c.*, cl.name as clinic_name, s.max_minutes as sla_max
            FROM cases c
            LEFT JOIN clinics cl ON c.clinic_id = cl.clinic_id
            LEFT JOIN sla_config s ON c.priority_label = s.priority_label
            WHERE c.assigned_rad_id = ?
              AND c.status IN ('Assigned', 'In_Review')
            ORDER BY c.priority_score DESC, c.upload_time ASC;
            """,
            (rad_id,)
        ).fetchall()

        now = datetime.now(timezone.utc)
        result_cases = []

        for row in cases_rows:
            case_data = dict(row)
            # Recompute priority explainability dynamically with current waiting term
            _, _, breakdown = priority.compute_priority(case_data, now=now)

            upload_dt = datetime.fromisoformat(case_data["upload_time"])
            if upload_dt.tzinfo is None:
                upload_dt = upload_dt.replace(tzinfo=timezone.utc)
            waiting_mins = round((now - upload_dt).total_seconds() / 60.0, 1)

            sla_limit = case_data.get("sla_max") or SLA_LIMITS_MINUTES.get(case_data["priority_label"], 1440)
            remaining_mins = round(sla_limit - waiting_mins, 1)
            is_at_risk = (remaining_mins <= (sla_limit * SLA_AT_RISK_RATIO)) and remaining_mins > 0
            is_breached = remaining_mins <= 0

            case_data.update({
                "waiting_minutes": waiting_mins,
                "sla_limit_minutes": sla_limit,
                "sla_remaining_minutes": remaining_mins,
                "is_at_risk": is_at_risk,
                "is_breached": is_breached,
                "priority_breakdown": breakdown,
            })
            result_cases.append(case_data)

        return jsonify({
            "status": "success",
            "radiologist": dict(rad),
            "count": len(result_cases),
            "worklist": result_cases,
        }), 200
    finally:
        conn.close()


@app.route("/api/case/<case_id>/start", methods=["POST"])
def api_case_start_review(case_id: str):
    """Transitions case to In_Review. Requires rad_id in JSON."""
    data = request.get_json(silent=True) or {}
    rad_id = data.get("rad_id")

    # If rad_id not provided in body, check query param or existing assigned_rad_id
    if not rad_id:
        case_row = query_one("SELECT assigned_rad_id FROM cases WHERE case_id = ?;", (case_id,))
        if case_row and case_row["assigned_rad_id"]:
            rad_id = case_row["assigned_rad_id"]
        else:
            return jsonify({"status": "error", "message": "Field 'rad_id' is required to start review."}), 400

    try:
        result = workflow_engine.start_review(case_id, rad_id)
        return jsonify({"status": "success", "data": result}), 200
    except (ValueError, workflow_engine.IllegalStateTransitionError) as err:
        return jsonify({"status": "error", "message": str(err)}), 400
    except Exception as exc:
        logger.error(f"Error starting review for {case_id}: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/case/<case_id>/report", methods=["POST"])
def api_case_submit_report(case_id: str):
    """
    Submits diagnostic report text and CBIR usage metrics.
    Payload: {rad_id, report_text, similar_cases_viewed, used_similar_cases}
    """
    data = request.get_json(silent=True) or {}
    rad_id = data.get("rad_id")
    report_text = data.get("report_text")
    similar_viewed = int(data.get("similar_cases_viewed", 0))
    used_similar = data.get("used_similar_cases")

    if not report_text or not report_text.strip():
        return jsonify({"status": "error", "message": "Diagnostic report_text cannot be empty."}), 400

    if not rad_id:
        case_row = query_one("SELECT assigned_rad_id FROM cases WHERE case_id = ?;", (case_id,))
        if case_row and case_row["assigned_rad_id"]:
            rad_id = case_row["assigned_rad_id"]
        else:
            return jsonify({"status": "error", "message": "Field 'rad_id' is required."}), 400

    try:
        result = workflow_engine.submit_report(
            case_id=case_id,
            rad_id=rad_id,
            text=report_text,
            similar_cases_viewed=similar_viewed,
            used_similar_cases=used_similar
        )
        return jsonify({"status": "success", "data": result}), 200
    except (ValueError, workflow_engine.IllegalStateTransitionError) as err:
        return jsonify({"status": "error", "message": str(err)}), 400
    except Exception as exc:
        logger.error(f"Error submitting report for {case_id}: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/case/<case_id>/return", methods=["POST"])
def api_case_return_clinic(case_id: str):
    """Transitions case to Returned_To_Clinic."""
    try:
        result = workflow_engine.return_to_clinic(case_id)
        return jsonify({"status": "success", "data": result}), 200
    except (ValueError, workflow_engine.IllegalStateTransitionError) as err:
        return jsonify({"status": "error", "message": str(err)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/rebalance", methods=["POST"])
def api_rebalance():
    """Triggers workload rebalancing between radiologists."""
    try:
        transfers = routing.rebalance()
        return jsonify({
            "status": "success",
            "count": len(transfers),
            "transfers": transfers,
            "message": f"Rebalanced {len(transfers)} case(s)." if transfers else "Workload already balanced."
        }), 200
    except Exception as exc:
        logger.error(f"Error during rebalance: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


# ============================================================================
# FRONTEND TEMPLATE PLACEHOLDERS (To be populated in Phases 4, 5, 7)
# ============================================================================

@app.route("/", methods=["GET"])
def page_role_select():
    """Role selector landing page."""
    try:
        radiologists = query_all("SELECT rad_id, name, subspecialty FROM radiologists WHERE is_active = 1;")
        clinics = query_all("SELECT clinic_id, name FROM clinics;")
        return render_template("login_select.html", radiologists=radiologists, clinics=clinics)
    except Exception:
        return render_template("login_select.html", radiologists=[], clinics=[])


@app.route("/worklist/<rad_id>", methods=["GET"])
def page_worklist(rad_id: str):
    """Radiologist worklist page."""
    rad = query_one("SELECT * FROM radiologists WHERE rad_id = ?;", (rad_id,))
    if not rad:
        abort(404, description="Radiologist not found")
    return render_template("worklist.html", rad=rad)


@app.route("/case/<case_id>", methods=["GET"])
def page_case_view(case_id: str):
    """Diagnostic reading view with Top-K similar cases."""
    case = get_platform_case_details(case_id)
    if not case:
        abort(404, description="Case not found")
    
    ret_row = query_one(
        """
        SELECT top_k_json, retrieval_ms, mode, created_at
        FROM retrieval_log
        WHERE case_id = ?
        ORDER BY created_at DESC LIMIT 1;
        """,
        (case_id,)
    )

    if ret_row and ret_row["top_k_json"]:
        try:
            cbir_payload = json.loads(ret_row["top_k_json"])
        except Exception:
            cbir_payload = get_similar_cases(case_id, k=10)
        retrieval_meta = {"ms": ret_row["retrieval_ms"], "mode": ret_row["mode"]}
    else:
        cbir_payload = get_similar_cases(case_id, k=10)
        retrieval_meta = {"ms": cbir_payload.get("retrieval_ms", 45.0), "mode": "cbir"}

    return render_template("case_view.html", case=case, cbir=cbir_payload, retrieval_meta=retrieval_meta)


@app.route("/hospital", methods=["GET"])
def page_hospital_dashboard():
    """Hospital analytics dashboard with Chart.js."""
    return render_template("hospital_dashboard.html")


@app.route("/clinic/<clinic_id>", methods=["GET"])
def page_clinic_status(clinic_id: str):
    """Clinic case status tracking portal."""
    clinic = query_one("SELECT * FROM clinics WHERE clinic_id = ?;", (clinic_id,))
    if not clinic:
        abort(404, description="Clinic not found")
    cases = get_clinic_cases_summary(clinic_id)
    return render_template("clinic_status.html", clinic=clinic, cases=cases)


@app.route("/api/experiment/run", methods=["POST"])
def api_experiment_run():
    """Re-runs the workflow efficiency simulation experiment."""
    import simulate
    try:
        data = request.get_json(silent=True) or {}
        num_cases = int(data.get("cases", 100))
        seed = int(data.get("seed", 42))
        summary_df, _, stats_dict = simulate.run_experiment(num_cases=num_cases, seed=seed)
        return jsonify({
            "status": "success",
            "message": "Simulation experiment executed successfully.",
            "summary": summary_df.to_dict(orient="records"),
            "stats": stats_dict,
        }), 200
    except Exception as exc:
        logger.error(f"Error running experiment: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500

@app.route("/experiment", methods=["GET"])
def page_experiment_results():
    """Simulated CBIR workflow efficiency experiment results."""
    import simulate
    results_path = BASE_DIR / "outputs" / "experiment_results.csv"
    if not results_path.exists():
        simulate.run_experiment(num_cases=100, seed=42)
    
    summary_df = pd.read_csv(results_path)
    records = summary_df.to_dict(orient="records")
    
    # Re-run or read stats
    cases_path = BASE_DIR / "outputs" / "experiment_cases.csv"
    cases_df = pd.read_csv(cases_path) if cases_path.exists() else pd.DataFrame()
    
    from scipy import stats
    base_rev = cases_df["baseline_review_mins"].values
    cbir_rev = cases_df["cbir_review_mins"].values
    rerank_rev = cases_df["cbir_rerank_review_mins"].values
    
    w_cbir = stats.wilcoxon(base_rev, cbir_rev, alternative="greater")
    w_rerank = stats.wilcoxon(base_rev, rerank_rev, alternative="greater")
    
    stats_summary = {
        "p_val_cbir": f"{w_cbir.pvalue:.2e}",
        "p_val_rerank": f"{w_rerank.pvalue:.2e}",
    }
    
    return render_template("experiment_results.html", records=records, stats=stats_summary, sim_config=SIMULATION_CONFIG)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)