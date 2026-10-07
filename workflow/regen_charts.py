"""
regen_charts.py  (put in the workflow/ folder)
Redraws the experiment charts in the pink theme from the EXISTING CSVs.
Does NOT re-run the simulation, so the numbers stay exactly the same.

Run:  python regen_charts.py
"""
import pandas as pd

from config import OUTPUTS_DIR
from simulate import generate_experiment_charts

cases_df = pd.read_csv(OUTPUTS_DIR / "experiment_cases.csv")
summary_df = pd.read_csv(OUTPUTS_DIR / "experiment_results.csv")
generate_experiment_charts(cases_df, summary_df)
print("Pink charts regenerated.")