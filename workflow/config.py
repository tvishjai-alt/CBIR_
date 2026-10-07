"""
config.py
Central Configuration for Tele-Radiology Workflow Intelligence Module (Member 3).

Contains:
- Database connection paths and schema table/column aliases
- Dynamic priority scoring weights, formulas, and thresholds
- SLA turnaround expectations and alert margins
- Workload-balanced routing and capacity constraints
- CBIR adapter settings (Mock vs. Live API)
- Simulation parameters and empirical review time configurations
"""

import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "telemed_workflow.db"
STATIC_DIR = BASE_DIR / "static"
IMAGES_DIR = STATIC_DIR / "images"
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUTS_DIR = BASE_DIR / "outputs"

# Ensure output and static image directories exist
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Shared Entity Mapping (allows quick renaming to match Member 2's platform schema)
TABLE_NAMES = {
    "clinics": "clinics",
    "cases": "cases",
    "radiologists": "radiologists",
    "assignments": "assignments",
    "reports": "reports",
    "events": "events",
    "retrieval_log": "retrieval_log",
    "sla_config": "sla_config",
}

# Priority Scoring Configuration
# Total Priority Score = urgency_term + modality_body_term + ai_triage_term + waiting_term
PRIORITY_WEIGHTS = {
    "urgency_flag": 40.0,
    "modality_body_part": {
        ("CT", "Head"): 20.0,
        ("CT", "Brain"): 20.0,
        ("MRI", "Brain"): 15.0,
        ("MRI", "Head"): 15.0,
        ("X-ray", "Chest"): 10.0,
        ("CT", "Chest"): 12.0,
        ("default"): 5.0,
    },
    "ai_triage_max": 20.0,
    "waiting_rate_per_min": 0.05,  # +3.0 pts per hour of waiting
    "waiting_max_cap": 20.0,       # Capped at 20 pts (~400 mins) to prevent routine starvation
}

PRIORITY_THRESHOLDS = {
    "CRITICAL": 70.0,  # Score >= 70.0 -> Critical
    "HIGH": 40.0,      # 40.0 <= Score < 70.0 -> High
    # Below 40.0 -> Routine
}

# Service Level Agreement (SLA) Windows in minutes
SLA_LIMITS_MINUTES = {
    "Critical": 60,     # 1 hour
    "High": 240,        # 4 hours
    "Routine": 1440,    # 24 hours
}

# SLA At-Risk Margin: Flag case if remaining time < 25% of SLA window
SLA_AT_RISK_RATIO = 0.25

# Routing & Load Balancing Configuration
ROUTING_LOAD_WEIGHTS = {
    "Critical": 3,
    "High": 2,
    "Routine": 1,
}

ROUTING_SUBSPECIALTY_MAP = {
    "brain": "Neuro",
    "head": "Neuro",
    "spine": "Neuro",
    "neck": "Neuro",
    "chest": "Chest",
    "lung": "Chest",
    "lungs": "Chest",
    "cardiac": "Chest",
    "thorax": "Chest",
    "msk": "MSK",
    "knee": "MSK",
    "bone": "MSK",
    "extremity": "MSK",
    "shoulder": "MSK",
    "ankle": "MSK",
    "hip": "MSK",
}

ROUTING_FALLBACK_SUBSPECIALTY = "General"
ROUTING_REBALANCE_THRESHOLD = 4  # Load point difference threshold to trigger rebalancing

# State Machine Legal Transitions
LEGAL_STATUS_TRANSITIONS = {
    "Uploaded": ["Stored"],
    "Stored": ["Retrieved"],
    "Retrieved": ["Assigned"],
    "Assigned": ["In_Review", "Reassigned"],
    "Reassigned": ["Assigned"],
    "In_Review": ["Reported"],
    "Reported": ["Returned_To_Clinic"],
    "Returned_To_Clinic": [],
}

# CBIR Adapter Configuration (Member 1 Integration)
USE_MOCK_CBIR = os.getenv("USE_MOCK_CBIR", "1") != "0"
CBIR_BASE_URL = os.getenv("CBIR_BASE_URL", "http://127.0.0.1:8000")
CBIR_MOCK_LATENCY_RANGE_MS = (32.0, 78.0)
CBIR_DEFAULT_K = 10

# Simulation Experiment Parameters (simulate.py)
SIMULATION_CONFIG = {
    "random_seed": 42,
    "num_cases": 100,
    "arrival_rate_per_hour": 20.0,  # Poisson lambda; targets ~70% baseline utilization with 6 radiologists
    "cbir_time_reduction_factor": 0.15,       # 15% faster with visual CBIR
    "cbir_rerank_time_reduction_factor": 0.25, # 25% faster with context-aware re-ranking
    "measured_times_file": OUTPUTS_DIR / "measured_review_times.csv",
    "experiment_results_csv": OUTPUTS_DIR / "experiment_results.csv",
    "experiment_chart_png": OUTPUTS_DIR / "experiment_comparison.png",
    # Base review minutes by (Modality, Priority)
    "base_review_minutes": {
        ("CT", "Critical"): 8.0,
        ("CT", "High"): 12.0,
        ("CT", "Routine"): 15.0,
        ("MRI", "Critical"): 12.0,
        ("MRI", "High"): 16.0,
        ("MRI", "Routine"): 20.0,
        ("X-ray", "Critical"): 4.0,
        ("X-ray", "High"): 6.0,
        ("X-ray", "Routine"): 8.0,
    }
}
