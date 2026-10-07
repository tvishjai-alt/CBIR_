"""
simulate.py
Empirical Workflow-Efficiency Experiment Runner (Member 3).

Project Claim:
"Context-aware similar-case retrieval reduces case-review time
and improves workflow efficiency in a simulated tele-radiology workflow."

Compares 3 operational modes on the EXACT SAME set of N cases (fixed random seed):
1. baseline: Standard workflow without similar-case visual assistance
2. cbir: Standard visual Content-Based Image Retrieval (Top-10 visual matches)
3. cbir_rerank: Context-aware re-ranked CBIR (Top-10 visual + anatomical re-ranking)

Outputs:
- outputs/experiment_results.csv (macro summary per mode)
- outputs/experiment_cases.csv (paired per-case measurements)
- static/images/experiment_charts.png & outputs/experiment_comparison.png (matplotlib charts)
- Statistical validation via Wilcoxon signed-rank test (scipy.stats) and 95% bootstrap CIs
"""

import argparse
import csv
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # Non-interactive headless backend
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

from config import (
    BASE_DIR,
    OUTPUTS_DIR,
    STATIC_DIR,
    IMAGES_DIR,
    SIMULATION_CONFIG,
    SLA_LIMITS_MINUTES,
    ROUTING_LOAD_WEIGHTS,
    ROUTING_SUBSPECIALTY_MAP,
    ROUTING_FALLBACK_SUBSPECIALTY,
)
from priority import compute_priority

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("simulate")

SIM_RADIOLOGISTS = [
    {"rad_id": "RAD-001", "name": "Dr. Sarah Chen", "subspecialty": "Neuro", "max_capacity": 6, "avg_report_minutes": 15.0},
    {"rad_id": "RAD-002", "name": "Dr. Marcus Vance", "subspecialty": "Chest", "max_capacity": 7, "avg_report_minutes": 12.0},
    {"rad_id": "RAD-003", "name": "Dr. Elena Rostova", "subspecialty": "MSK", "max_capacity": 5, "avg_report_minutes": 18.0},
    {"rad_id": "RAD-004", "name": "Dr. David Kim", "subspecialty": "General", "max_capacity": 8, "avg_report_minutes": 14.0},
    {"rad_id": "RAD-005", "name": "Dr. Priya Patel", "subspecialty": "Chest", "max_capacity": 6, "avg_report_minutes": 11.0},
    {"rad_id": "RAD-006", "name": "Dr. James Wilson", "subspecialty": "Neuro", "max_capacity": 5, "avg_report_minutes": 16.0},
]


def determine_subspec(body_part: str) -> str:
    clean = body_part.strip().lower()
    for kw, spec in ROUTING_SUBSPECIALTY_MAP.items():
        if kw in clean:
            return spec
    return ROUTING_FALLBACK_SUBSPECIALTY


def generate_experiment_cases(num_cases: int, seed: int) -> List[Dict[str, Any]]:
    """
    Generates a deterministic cohort of N imaging cases with Poisson arrivals
    and simulated CBIR retrieval precision scores.
    """
    rng = np.random.default_rng(seed)
    modalities_pool = [
        ("CT", "Head"),
        ("CT", "Chest"),
        ("MRI", "Brain"),
        ("MRI", "Spine"),
        ("X-ray", "Chest"),
        ("X-ray", "Knee"),
    ]

    arrival_rate = SIMULATION_CONFIG["arrival_rate_per_hour"]  # e.g., 6 cases/hr -> 10 mins inter-arrival
    mean_inter_arrival = 60.0 / arrival_rate

    cases = []
    current_time = 0.0

    for i in range(1, num_cases + 1):
        inter_arrival = float(rng.exponential(scale=mean_inter_arrival))
        current_time += inter_arrival

        modality, body_part = modalities_pool[rng.integers(0, len(modalities_pool))]
        urgency = 1 if rng.random() < 0.25 else 0
        ai_conf = float(rng.uniform(0.65, 0.95)) if (urgency == 1 and rng.random() < 0.6) else float(rng.uniform(0.0, 0.35))

        # Precision@10 quality factor (0.5 to 1.0)
        retrieval_precision = float(rng.uniform(0.60, 0.98))

        # Initial static priority calculation
        case_dict = {
            "case_id": f"SIM-{i:04d}",
            "modality": modality,
            "body_part": body_part,
            "urgency_flag": urgency,
            "ai_triage_score": ai_conf,
            "upload_time": "2026-10-01T00:00:00Z",
        }
        score, label, _ = compute_priority(case_dict)

        cases.append({
            "case_id": f"SIM-{i:04d}",
            "arrival_minute": round(current_time, 2),
            "modality": modality,
            "body_part": body_part,
            "urgency_flag": urgency,
            "ai_triage_score": round(ai_conf, 2),
            "priority_score": score,
            "priority_label": label,
            "retrieval_precision": round(retrieval_precision, 3),
        })

    return cases


def sample_review_times(
    cases: List[Dict[str, Any]],
    mode: str,
    seed: int,
    measured_csv_path: Optional[Path] = None
) -> Dict[str, float]:
    """
    Computes review duration in minutes for each case.
    If measured_csv_path exists, overrides with empirical values.
    Otherwise draws from log-normal distribution parameterized in config.py.
    """
    # 1. Check for empirical measured review times override
    if measured_csv_path and measured_csv_path.exists():
        logger.info(f"Loading measured review times from {measured_csv_path}...")
        try:
            df_measured = pd.read_csv(measured_csv_path)
            mode_subset = df_measured[df_measured["mode"] == mode]
            if not mode_subset.empty:
                # Check whether column is seconds or minutes
                if "seconds" in mode_subset.columns:
                    return dict(zip(mode_subset["case_id"], mode_subset["seconds"] / 60.0))
                elif "minutes" in mode_subset.columns:
                    return dict(zip(mode_subset["case_id"], mode_subset["minutes"]))
        except Exception as e:
            logger.warning(f"Failed to read measured times CSV ({e}). Using synthetic lognormal model.")

    # 2. Synthetic log-normal sampling
    rng = np.random.default_rng(seed)
    base_means = SIMULATION_CONFIG["base_review_minutes"]
    sigma = 0.30  # Standard lognormal shape parameter for reading tasks

    reduction_factor = 0.0
    if mode == "cbir":
        reduction_factor = SIMULATION_CONFIG["cbir_time_reduction_factor"]
    elif mode == "cbir_rerank":
        reduction_factor = SIMULATION_CONFIG["cbir_rerank_time_reduction_factor"]

    review_times = {}
    for c in cases:
        key = (c["modality"], c["priority_label"])
        base_mean = base_means.get(key, 10.0)

        # Lognormal parameters: mu = ln(mean) - sigma^2 / 2
        mu = np.log(base_mean) - (sigma ** 2) / 2.0
        raw_duration = rng.lognormal(mean=mu, sigma=sigma)

        # In CBIR modes, reduction is modulated by retrieval precision
        if mode in ["cbir", "cbir_rerank"]:
            effective_reduction = reduction_factor * c["retrieval_precision"]
            adjusted_duration = raw_duration * (1.0 - effective_reduction)
        else:
            adjusted_duration = raw_duration

        # Ensure realistic minimum bounds (e.g. at least 2.5 minutes)
        review_times[c["case_id"]] = max(2.5, round(float(adjusted_duration), 2))

    return review_times


def simulate_queue(
    cases: List[Dict[str, Any]],
    review_times: Dict[str, float]
) -> List[Dict[str, Any]]:
    """
    Simulates discrete-event queueing flow with 6 radiologists:
    - Subspecialty routing with General fallback
    - Capacity constraints
    - Priority-weighted load minimization
    - Accurate tracking of waiting time, review time, and turnaround time
    """
    # Initialize radiologist state
    rad_state = {}
    for r in SIM_RADIOLOGISTS:
        rad_state[r["rad_id"]] = {
            "spec": r["subspecialty"],
            "capacity": r["max_capacity"],
            "avg_time": r["avg_report_minutes"],
            "busy_until": 0.0,
            "active_cases": [],  # list of (finish_time, priority_label)
            "total_busy_minutes": 0.0,
        }

    completed_simulations = []

    # Sort cases by arrival time
    sorted_cases = sorted(cases, key=lambda x: x["arrival_minute"])

    for c in sorted_cases:
        cid = c["case_id"]
        arr_time = c["arrival_minute"]
        duration = review_times[cid]
        target_spec = determine_subspec(c["body_part"])

        # Advance radiologist state up to current arrival time (clear finished cases)
        for rid, s in rad_state.items():
            s["active_cases"] = [item for item in s["active_cases"] if item[0] > arr_time]

        # Evaluate candidate radiologists
        def evaluate_rads(candidates):
            viable = []
            for rid in candidates:
                s = rad_state[rid]
                active_count = len(s["active_cases"])
                if active_count < s["capacity"]:
                    weighted_load = sum(ROUTING_LOAD_WEIGHTS.get(item[1], 1) for item in s["active_cases"])
                    viable.append((rid, weighted_load, s["avg_time"]))
            # Sort by lowest weighted_load, then lowest avg_time
            viable.sort(key=lambda x: (x[1], x[2]))
            return [x[0] for x in viable]

        specialists = [rid for rid, s in rad_state.items() if s["spec"] == target_spec]
        generalists = [rid for rid, s in rad_state.items() if s["spec"] == ROUTING_FALLBACK_SUBSPECIALTY]

        chosen_rad = None
        viable_spec = evaluate_rads(specialists)
        if viable_spec:
            chosen_rad = viable_spec[0]
        else:
            viable_gen = evaluate_rads(generalists)
            if viable_gen:
                chosen_rad = viable_gen[0]
            else:
                # If everyone is at capacity, route to the radiologist with earliest finishing case
                all_rads = list(rad_state.keys())
                all_rads.sort(key=lambda rid: (
                    min([item[0] for item in rad_state[rid]["active_cases"]])
                    if rad_state[rid]["active_cases"] else arr_time
                ))
                chosen_rad = all_rads[0]

        # Schedule reading start time
        s = rad_state[chosen_rad]
        start_time = max(arr_time, s["busy_until"])
        finish_time = start_time + duration

        s["busy_until"] = finish_time
        s["active_cases"].append((finish_time, c["priority_label"]))
        s["total_busy_minutes"] += duration

        waiting_mins = max(0.0, start_time - arr_time)
        turnaround_mins = finish_time - arr_time
        sla_limit = SLA_LIMITS_MINUTES[c["priority_label"]]
        is_breach = turnaround_mins > sla_limit

        completed_simulations.append({
            "case_id": cid,
            "arrival_time": arr_time,
            "start_time": round(start_time, 2),
            "finish_time": round(finish_time, 2),
            "waiting_minutes": round(waiting_mins, 2),
            "review_minutes": round(duration, 2),
            "turnaround_minutes": round(turnaround_mins, 2),
            "sla_limit_minutes": sla_limit,
            "is_breach": 1 if is_breach else 0,
            "priority_label": c["priority_label"],
            "modality": c["modality"],
            "assigned_rad": chosen_rad,
        })

    # Calculate overall utilization
    sim_span = max(item["finish_time"] for item in completed_simulations) - min(item["arrival_time"] for item in completed_simulations)
    total_capacity_minutes = sim_span * len(SIM_RADIOLOGISTS)
    total_busy = sum(s["total_busy_minutes"] for s in rad_state.values())
    utilization_pct = round((total_busy / max(1.0, total_capacity_minutes)) * 100.0, 1)

    return completed_simulations, utilization_pct, sim_span


def run_experiment(
    num_cases: int = 100,
    seed: int = 42
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """
    Executes the 3-mode workflow comparison experiment.
    Returns:
        (summary_df, cases_df, stats_dict)
    """
    logger.info(f"Running tele-radiology workflow simulation with N={num_cases} cases, seed={seed}...")
    cases = generate_experiment_cases(num_cases=num_cases, seed=seed)

    modes = ["baseline", "cbir", "cbir_rerank"]
    mode_simulations = {}
    mode_utilization = {}
    mode_spans = {}

    for mode in modes:
        review_times = sample_review_times(
            cases,
            mode=mode,
            seed=seed,
            measured_csv_path=SIMULATION_CONFIG["measured_times_file"]
        )
        sim_results, util_pct, span = simulate_queue(cases, review_times)
        mode_simulations[mode] = sim_results
        mode_utilization[mode] = util_pct
        mode_spans[mode] = span

    # 1. Compile per-case paired comparison table
    case_rows = []
    for i, c in enumerate(cases):
        cid = c["case_id"]
        base_res = mode_simulations["baseline"][i]
        cbir_res = mode_simulations["cbir"][i]
        rerank_res = mode_simulations["cbir_rerank"][i]

        case_rows.append({
            "case_id": cid,
            "modality": c["modality"],
            "priority_label": c["priority_label"],
            "retrieval_precision": c["retrieval_precision"],
            "baseline_review_mins": base_res["review_minutes"],
            "cbir_review_mins": cbir_res["review_minutes"],
            "cbir_rerank_review_mins": rerank_res["review_minutes"],
            "baseline_tat_mins": base_res["turnaround_minutes"],
            "cbir_tat_mins": cbir_res["turnaround_minutes"],
            "cbir_rerank_tat_mins": rerank_res["turnaround_minutes"],
            "baseline_breach": base_res["is_breach"],
            "cbir_breach": cbir_res["is_breach"],
            "cbir_rerank_breach": rerank_res["is_breach"],
        })

    cases_df = pd.DataFrame(case_rows)

    # 2. Compile macro summary table per mode
    summary_rows = []
    base_mean_review = np.mean([r["review_minutes"] for r in mode_simulations["baseline"]])
    base_mean_tat = np.mean([r["turnaround_minutes"] for r in mode_simulations["baseline"]])

    for mode in modes:
        sim = mode_simulations[mode]
        rev_times = [r["review_minutes"] for r in sim]
        tat_times = [r["turnaround_minutes"] for r in sim]
        breaches = [r["is_breach"] for r in sim]

        mean_rev = round(float(np.mean(rev_times)), 2)
        med_rev = round(float(np.median(rev_times)), 2)
        p90_rev = round(float(np.percentile(rev_times, 90)), 2)

        mean_tat = round(float(np.mean(tat_times)), 2)
        med_tat = round(float(np.median(tat_times)), 2)
        p90_tat = round(float(np.percentile(tat_times, 90)), 2)

        breach_rate = round(float(np.mean(breaches) * 100.0), 1)

        # Percentage improvement relative to baseline
        rev_reduction_pct = round(((base_mean_review - mean_rev) / base_mean_review) * 100.0, 1)
        tat_reduction_pct = round(((base_mean_tat - mean_tat) / base_mean_tat) * 100.0, 1)

        # Throughput: cases reported per 8-hour shift
        shift_throughput = round((len(sim) / (mode_spans[mode] / 60.0)) * 8.0, 1)

        summary_rows.append({
            "mode": mode,
            "mean_review_mins": mean_rev,
            "median_review_mins": med_rev,
            "p90_review_mins": p90_rev,
            "review_reduction_pct": rev_reduction_pct,
            "mean_turnaround_mins": mean_tat,
            "median_turnaround_mins": med_tat,
            "p90_turnaround_mins": p90_tat,
            "turnaround_reduction_pct": tat_reduction_pct,
            "sla_breach_rate_pct": breach_rate,
            "radiologist_utilization_pct": mode_utilization[mode],
            "throughput_per_8h_shift": shift_throughput,
        })

    summary_df = pd.DataFrame(summary_rows)

    # 3. Statistical Hypothesis Testing (Paired Wilcoxon Signed-Rank Test)
    base_rev = cases_df["baseline_review_mins"].values
    cbir_rev = cases_df["cbir_review_mins"].values
    rerank_rev = cases_df["cbir_rerank_review_mins"].values

    wilcoxon_cbir = stats.wilcoxon(base_rev, cbir_rev, alternative="greater")
    wilcoxon_rerank = stats.wilcoxon(base_rev, rerank_rev, alternative="greater")

    # Bootstrap 95% Confidence Interval for % reduction
    def bootstrap_ci(arr_base, arr_exp, n_boot=2000):
        reductions = []
        n = len(arr_base)
        for _ in range(n_boot):
            idx = np.random.randint(0, n, size=n)
            diff = (np.mean(arr_base[idx]) - np.mean(arr_exp[idx])) / np.mean(arr_base[idx])
            reductions.append(diff * 100.0)
        return (round(np.percentile(reductions, 2.5), 1), round(np.percentile(reductions, 97.5), 1))

    ci_cbir = bootstrap_ci(base_rev, cbir_rev)
    ci_rerank = bootstrap_ci(base_rev, rerank_rev)

    stats_dict = {
        "wilcoxon_cbir": {"statistic": float(wilcoxon_cbir.statistic), "p_value": float(wilcoxon_cbir.pvalue)},
        "wilcoxon_rerank": {"statistic": float(wilcoxon_rerank.statistic), "p_value": float(wilcoxon_rerank.pvalue)},
        "bootstrap_ci_cbir": ci_cbir,
        "bootstrap_ci_rerank": ci_rerank,
    }

    # 4. Save CSV exports
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(OUTPUTS_DIR / "experiment_results.csv", index=False)
    cases_df.to_csv(OUTPUTS_DIR / "experiment_cases.csv", index=False)
    logger.info(f"Saved experiment CSVs to {OUTPUTS_DIR}")

    # 5. Generate Matplotlib Visualization
    generate_experiment_charts(cases_df, summary_df)

    return summary_df, cases_df, stats_dict


def generate_experiment_charts(cases_df: pd.DataFrame, summary_df: pd.DataFrame) -> None:
    """
    Renders a 4-panel comparison figure in the site's white + pink theme.
    Saves to static/images/experiment_charts.png and outputs/experiment_comparison.png.
    """
    INK, MUTED, GRID, PANEL = "#3a1f2e", "#7d5a6c", "#f7d3e2", "#fffafc"
    # Same hue family, light -> dark, so the order baseline -> CBIR -> re-rank reads at a glance.
    COLORS = ["#d4b5c6", "#f472b6", "#be185d"]
    EDGES = ["#a98799", "#db2777", "#831843"]
    modes = ["Baseline", "CBIR", "CBIR Re-rank"]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.patch.set_facecolor("#ffffff")

    for ax in axes.flat:
        ax.set_facecolor(PANEL)
        ax.tick_params(colors=MUTED, labelsize=10)
        ax.xaxis.label.set_color(MUTED)
        ax.yaxis.label.set_color(MUTED)
        ax.title.set_color(INK)
        ax.set_axisbelow(True)
        ax.grid(axis="y", linestyle="--", color=GRID, alpha=0.9)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)

    def label_bars(ax, bars, fmt, headroom=0.18):
        top = max(b.get_height() for b in bars)
        ax.set_ylim(0, top * (1 + headroom) if top > 0 else 1)
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2.0, h + top * 0.02, fmt.format(h),
                    ha="center", va="bottom", color=INK, fontweight="bold", fontsize=11)

    # Plot 1: review-time distribution
    ax1 = axes[0, 0]
    box_data = [cases_df["baseline_review_mins"], cases_df["cbir_review_mins"], cases_df["cbir_rerank_review_mins"]]
    bp = ax1.boxplot(
        box_data,
        tick_labels=["Baseline\n(No CBIR)", "CBIR\n(Top-10)", "CBIR Re-rank\n(Context-Aware)"],
        patch_artist=True,
        widths=0.5,
        flierprops=dict(marker="o", markerfacecolor="#f9a8d4", markeredgecolor="#be185d", markersize=5),
    )
    for patch, color, edge in zip(bp["boxes"], COLORS, EDGES):
        patch.set_facecolor(color)
        patch.set_edgecolor(edge)
        patch.set_linewidth(1.5)
    for median in bp["medians"]:
        median.set_color(INK)
        median.set_linewidth(2.2)
    for part in ("whiskers", "caps"):
        for line in bp[part]:
            line.set_color(MUTED)
    ax1.set_title("Diagnostic Review Time Distribution (Minutes)", fontsize=12, fontweight="bold", pad=12)
    ax1.set_ylabel("Review Duration (mins)")

    # Plot 2: mean turnaround
    ax2 = axes[0, 1]
    bars = ax2.bar(modes, summary_df["mean_turnaround_mins"].values, color=COLORS, edgecolor=EDGES, linewidth=1.5, width=0.55)
    ax2.set_title("Mean Turnaround Time (Upload to Report)", fontsize=12, fontweight="bold", pad=12)
    ax2.set_ylabel("Turnaround Duration (mins)")
    label_bars(ax2, bars, "{:.1f}m")

    # Plot 3: SLA breach rate
    ax3 = axes[1, 0]
    breach = summary_df["sla_breach_rate_pct"].values
    bars3 = ax3.bar(modes, breach, color=COLORS, edgecolor=EDGES, linewidth=1.5, width=0.55)
    ax3.set_title("SLA Breach Rate (%) Across Modes", fontsize=12, fontweight="bold", pad=12)
    ax3.set_ylabel("Breach Rate (%)")
    label_bars(ax3, bars3, "{:.1f}%", headroom=0.3)

    # Plot 4: throughput
    ax4 = axes[1, 1]
    bars4 = ax4.bar(modes, summary_df["throughput_per_8h_shift"].values, color=COLORS, edgecolor=EDGES, linewidth=1.5, width=0.55)
    ax4.set_title("Reporting Throughput (Cases / 8-Hour Shift)", fontsize=12, fontweight="bold", pad=12)
    ax4.set_ylabel("Completed Cases")
    label_bars(ax4, bars4, "{:.1f}")

    plt.tight_layout(pad=3.0)

    chart_out_1 = OUTPUTS_DIR / "experiment_comparison.png"
    chart_out_2 = IMAGES_DIR / "experiment_charts.png"
    plt.savefig(chart_out_1, dpi=180, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.savefig(chart_out_2, dpi=180, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    logger.info(f"Generated experiment charts at {chart_out_1} and {chart_out_2}")


def main():
    parser = argparse.ArgumentParser(description="Run the tele-radiology workflow efficiency simulation experiment.")
    parser.add_argument("--cases", type=int, default=100, help="Number of simulated cases (default: 100)")
    parser.add_argument("--seed", type=int, default=42, help="Fixed random seed (default: 42)")
    args = parser.parse_args()

    summary_df, _, stats_dict = run_experiment(num_cases=args.cases, seed=args.seed)

    print("\n================================================================================")
    print("      TELE-RADIOLOGY WORKFLOW EFFICIENCY EXPERIMENT: SUMMARY RESULTS            ")
    print("================================================================================")
    print(summary_df.to_string(index=False))
    print("\n--------------------------------------------------------------------------------")
    print("STATISTICAL SIGNIFICANCE (Wilcoxon Signed-Rank Test vs Baseline):")
    print(f"  - CBIR vs Baseline:         p-value = {stats_dict['wilcoxon_cbir']['p_value']:.2e} "
          f"(95% CI Reduction: {stats_dict['bootstrap_ci_cbir'][0]}% to {stats_dict['bootstrap_ci_cbir'][1]}%)")
    print(f"  - CBIR Re-Rank vs Baseline: p-value = {stats_dict['wilcoxon_rerank']['p_value']:.2e} "
          f"(95% CI Reduction: {stats_dict['bootstrap_ci_rerank'][0]}% to {stats_dict['bootstrap_ci_rerank'][1]}%)")
    print("================================================================================\n")


if __name__ == "__main__":
    main()
