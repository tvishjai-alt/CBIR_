# Workflow Intelligence Module: System Assumptions

Operational, statistical, architectural and data assumptions for the **Workflow Intelligence Module (Member 3)** of the AI-Assisted Intelligent Tele-Radiology Workflow Management System. Parameters live in `config.py` unless noted.

---

## 1. Data, privacy and what is real versus simulated

**Real**
- Images and their attributes (age, gender, view position, finding labels) come from the public NIH ChestX-ray14 dataset, via Member 1's 5,000-image pool.
- Similar-case retrieval is real: Member 1's DenseNet-121 + FAISS engine with context re-ranking runs on every case created through the webhook.
- Cases, assignments, reports and status events created in the demo are real records in the system.

**Simulated / hypothetical**
- Hospital operational data (arrival patterns, radiologist profiles, review durations, SLA timestamps) is proprietary and not public. Radiologist names, subspecialties and capacities, case arrival times, and review durations are simulated.
- The historical cases created by `seed.py` and the timings in the experiment are generated with stated random distributions. The parameter values are plausible choices made by the project team. They are **not calibrated against published data and not measured**.
- Clinic urgency flags for demo cases are seeded random (about 25%), a stand-in for a referring clinic's own triage.

**Privacy**
- No real patient health information is processed. Patients appear only as synthetic tokens (`PT-0001` and similar). Dataset patient IDs are converted to such tokens.

---

## 2. Priority scoring and explainability

`priority.py` computes `score = urgency + modality/body part + AI triage + waiting time`:

| Term | Value |
|---|---|
| Clinic urgency flag | +40 if `urgency_flag = 1` |
| CT Head / CT Brain | +20 |
| MRI Brain / MRI Head | +15 |
| CT Chest | +12 |
| X-ray Chest | +10 |
| Any other modality/body part | +5 |
| AI triage confidence | `confidence x 20`, 0 if unavailable |
| Waiting time (starvation prevention) | `min(waiting_minutes x 0.05, 20)`, i.e. +3 per hour, capped at 20 after about 6.7 hours |

Buckets: **Critical** >= 70, **High** 40 to 69.9, **Routine** < 40. The worklist shows the breakdown for every case. These weights are design choices, not clinically validated.

---

## 3. Service level agreements

Maximum turnaround from `Uploaded` to `Reported`: **Critical 60 min, High 240 min, Routine 1440 min**. A case is flagged **at risk** when less than 25% of its window remains.

---

## 4. Routing and capacity

- **Subspecialty mapping:** brain/head/spine/neck -> Neuro; chest/lung(s)/cardiac/thorax -> Chest; msk/knee/bone/extremity/shoulder/ankle/hip -> MSK; anything else -> General (also the fallback when no specialist is free).
- **Capacity:** each radiologist has `max_capacity` open cases. Anyone at capacity is skipped. The seeded defaults are 5 to 8. For the demo load, capacity is raised to 25 (see README). If everyone is full, the case waits unassigned.
- **Weighted load:** Critical 3, High 2, Routine 1 per open case. The lowest load wins; ties go to the radiologist with the smaller `avg_report_minutes`.
- **Rebalancing:** if the load gap between the busiest and least busy radiologist exceeds 4, Routine cases can be moved.
- The experiment uses its own 6 simulated radiologists with capacities 5 to 8, independent of the demo database.

---

## 5. Integration with teammates' modules

**Member 1 (AI / CBIR)**
- Engine: DenseNet-121 features + FAISS (top-20 candidates) + context re-ranking `0.5 x age similarity + 0.25 x gender match + 0.25 x view match`, fused with visual similarity at alpha = 0.85. Used unmodified.
- Accessed through `member1/cbir_server.py` (`GET /retrieve/<case_id>?k=10`). Results exclude the query image and other images of the same patient, to avoid trivial matches.
- Retrieval covers **chest X-rays only**. Age/gender/view come from the dataset CSV; the few demo images not in that CSV use defaults (50, M, PA).
- Mock mode is the default. In live mode, if the AI service is unreachable or the image is missing, the app falls back to mock results and prefixes each finding with `[MOCK]`.
- Mock results are random findings with descending scores starting near 0.94, only for development and for cases created before live mode.

**Member 2 (upload platform)**
- Cases arrive through `POST /api/cases/new` (see `MEMBER2_HANDOFF.md`). The portal must save a readable, decrypted PNG/JPEG copy of the image where the AI service can read it.
- The two systems use separate databases; the webhook is the only link. Shared-entity table names are mapped in `config.py`.

---

## 6. Simulation and efficiency experiment

- **Cases:** 100 simulated cases, fixed seed 42, drawn from six modality/body-part combinations; about 25% urgent; simulated AI triage score; simulated retrieval precision per case, uniform 0.60 to 0.98 (not taken from Member 1's measured results).
- **Arrivals:** Poisson process, **20 cases/hour**, 6 radiologists. The initial 6/hour left the system almost idle (about 16% utilization, no queueing), so the rate was raised. It was set from baseline utilization (about 53% at 20/hour), not tuned to the size of the improvement, and was fixed before comparing modes.
- **Review time (baseline):** log-normal, sigma 0.30, mean by modality and priority (examples: CT 8/12/15 min, MRI 12/16/20 min, X-ray 4/6/8 min for Critical/High/Routine), minimum 2.5 min.
- **CBIR modes:** review time is multiplied by `1 - factor x retrieval_precision`, with factor 0.15 for visual CBIR and 0.25 for context re-ranked CBIR (25% is the total factor, not an extra 10%). With simulated precision averaging about 0.79, observed reductions are about 11.8% and 19.8%.
- **Queueing:** discrete-event queue with the same routing rules as the application (subspecialty, General fallback, capacity, weighted load).
- **Measured override:** if `outputs/measured_review_times.csv` exists (columns `case_id`, `mode`, `seconds`; IDs `SIM-0001` to `SIM-0100`), it replaces the synthetic times for the rows it contains.
- **Interpretation:** the 15% and 25% factors are hypothetical assumptions. The experiment shows their downstream effect on turnaround, SLA breaches and throughput. Wilcoxon tests and bootstrap intervals on simulated data only show the simulated effect is consistent across cases; they are not evidence that CBIR reduces reading time in practice.

---

## 7. Known limitations

- Chest X-rays only for real retrieval; Neuro and MSK pathways untested on real images.
- No authentication, single site, local SQLite, no PACS/DICOM integration.
- Similar-case thumbnails only appear if the pool images are present under `static/images/`; otherwise a placeholder is shown.
- Routing and priority are rule-based and not clinically validated.