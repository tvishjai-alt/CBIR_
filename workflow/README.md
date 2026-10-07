# TeleRad Workflow: Workflow Intelligence Module (Member 3)

Part of the group project **AI-Assisted Intelligent Tele-Radiology Workflow Management System with Content-Based Image Retrieval**.

This module covers everything after a clinic uploads an image: finding similar past cases (Member 1's AI), scoring priority, routing to a radiologist, reporting, and hospital analytics.

```
Upload portal (Member 2) --webhook--> Workflow app (Member 3) --GET /retrieve--> AI engine (Member 1)
                                           |
              priority -> routing -> radiologist worklist -> report -> clinic + hospital dashboards
```

**What it provides**

- Explainable priority score (Critical / High / Routine)
- Subspecialty- and load-aware radiologist routing with capacity limits
- Status state machine with an `events` audit trail
- Radiologist worklist and case view (image, Top-10 similar cases, report form)
- Hospital dashboard (workload, pending, turnaround, SLA breaches) and clinic status page
- Workflow-efficiency experiment: baseline vs CBIR vs CBIR + re-ranking
- Live integration with Member 1's engine (DenseNet-121 + FAISS, 5,000-image NIH ChestX-ray14 pool)

---

## 1. Quick start (Windows PowerShell, mock retrieval)

```powershell
cd path\to\telemed_project\workflow
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python seed.py --reset        # creates telemed_workflow.db with demo data
python app.py                 # http://127.0.0.1:5000
python -m pytest -q           # 47 tests
```

If PowerShell blocks the activate script, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

---

## 2. Live retrieval with Member 1's AI engine

The AI engine runs as a small separate service (`member1/cbir_server.py`) in its **own venv**, because torch and faiss are large. The workflow app calls it over HTTP.

### One-time setup

```powershell
cd workflow
py -3.12 -m venv member1\venv          # or 3.11; use plain `python` if neither exists
member1\venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r member1\Requirements.txt
pip install torch torchvision
```

(Use plain `pip install torch torchvision`. The `--index-url` PyTorch CPU variant can fail on build dependencies.)

### Run (three windows, all from the `workflow` folder)

```powershell
# Window 1: AI service. Wait for "Engine ready: 5000 pool images."
member1\venv\Scripts\activate
python member1\cbir_server.py

# Window 2: workflow app with live retrieval
venv\Scripts\activate
$env:USE_MOCK_CBIR="0"
python app.py

# Window 3: load demo cases (real chest X-rays in member1\query_images)
venv\Scripts\activate
python make_demo_cases.py --images member1\query_images --n 40
```

Leave `USE_MOCK_CBIR` unset to use mock retrieval (no AI service needed).

### Fresh start before a demo

Stop the app, then:

```powershell
python seed.py --reset
python -c "import sqlite3; c=sqlite3.connect('telemed_workflow.db'); c.execute('UPDATE radiologists SET max_capacity=25'); c.commit(); print('ok')"
$env:USE_MOCK_CBIR="0"
python app.py
python make_demo_cases.py --images member1\query_images --n 40
```

Re-seeding refreshes the open-case timestamps (otherwise old seeded cases show SLA `EXPIRED`). The capacity update stops demo cases from queueing unassigned.

### How to tell retrieval is really live

- Similar-case findings read like `Cardiomegaly, Effusion · 58y M · PA`.
- No finding starts with `[MOCK]`. That prefix means the AI service was unreachable, the image was missing, or the case was not created through the webhook, and the app fell back to mock data.
- The AI window logs one request per case.
- Direct check: `Invoke-RestMethod "http://127.0.0.1:8000/retrieve/CASE-2001?k=10"`

### How it works

1. The upload webhook (`POST /api/cases/new`) saves the case's image path, age, gender and view position in a small side table, `case_context` (`cbir_context.py`).
2. During case creation the app calls `GET /retrieve/<case_id>?k=10` on the AI service.
3. The service reads the context, runs Member 1's `retrieve_similar_cases`, removes the query image and any other image of the same patient, and returns the agreed JSON.
4. Mapping: `visual_score` -> `score`, `final_relevance_score` -> `reranked_score` (results are ordered by it), pathologies + age/gender/view -> `finding`.
5. The result is stored in `retrieval_log`, so the case page shows the stored result. Cases created before live mode keep their old mock results.

---

## 3. Project structure

```
workflow/
├── app.py                  Flask app: pages + JSON APIs
├── config.py               All settings: weights, SLAs, routing, CBIR flag, simulation
├── database.py             SQLite helpers
├── schema.sql              Table definitions
├── seed.py                 Demo data (radiologists, clinics, historical + open cases)
├── priority.py             Priority scoring (with breakdown)
├── routing.py              Radiologist assignment + rebalance
├── workflow_engine.py      State machine, create_case, start_review, submit_report, return_to_clinic
├── analytics.py            SQL analytics for the dashboards
├── simulate.py             With-vs-without CBIR experiment
├── regen_charts.py         Redraws experiment charts from existing CSVs
├── cbir_context.py         Stores per-case CBIR context (age, gender, view, image)
├── make_demo_cases.py      Loads real chest X-rays through the webhook
├── adapters/
│   ├── cbir_adapter.py     Mock or live retrieval (source: live / mock / mock-fallback)
│   └── platform_adapter.py Intake from Member 2's portal
├── member1/
│   ├── cbir_service.py, database_pool_active.csv, faiss_database.index   (Member 1, unmodified)
│   ├── cbir_server.py      HTTP wrapper around Member 1's engine
│   ├── Requirements.txt    AI-service dependencies
│   └── query_images/       Demo chest X-rays
├── templates/              HTML pages (Jinja2)
├── static/css, js, images  White + pink theme, charts, case images
├── tests/                  pytest suites
├── outputs/                Experiment CSVs and charts
├── ASSUMPTIONS.md
├── INTEGRATION_GUIDE.md    For Members 1 and 2
└── MEMBER2_HANDOFF.md      Webhook details for Member 2
```

---

## 4. Pages and APIs

| Page | URL |
|---|---|
| Role selector | `/` |
| Radiologist worklist | `/worklist/<rad_id>` (e.g. `RAD-002`) |
| Case view | `/case/<case_id>` |
| Hospital dashboard | `/hospital` |
| Clinic status | `/clinic/<clinic_id>` (e.g. `CLN-001`) |
| Experiment results | `/experiment` |

A case only shows **Start Review** when its status is `Assigned`. Open cases from a radiologist's worklist.

| API | Purpose |
|---|---|
| `POST /api/cases/new` | Webhook from the upload portal (creates, retrieves, prioritizes, routes) |
| `GET /api/case/<id>/info` | Case details and the finished report |
| `GET /api/stats` | Overall KPIs |
| `GET /api/worklist/<rad_id>` | Prioritized worklist with SLA countdown |
| `POST /api/case/<id>/start` | Start review (`In_Review`) |
| `POST /api/case/<id>/report` | Submit report (`Reported`) |
| `POST /api/case/<id>/return` | Return to clinic (`Returned_To_Clinic`) |
| `POST /api/rebalance` | Rebalance radiologist queues |
| `GET /api/hospital/analytics` | Data behind the hospital charts |
| `POST /api/experiment/run` | Re-run the experiment |
| `GET /api/health` | Health check |

---

## 5. How the logic works

### Status flow (illegal transitions raise an error; every change writes an `events` row)

```
Uploaded -> Stored -> Retrieved -> Assigned -> In_Review -> Reported -> Returned_To_Clinic
                                      ^  |
                                      |  v
                                   Reassigned
```

### Priority score (`priority.py`, weights in `config.py`)

`score = urgency + modality/body part + AI triage + waiting time`

| Term | Value |
|---|---|
| Clinic urgency flag | +40 |
| CT Head / Brain | +20 |
| MRI Brain / Head | +15 |
| CT Chest | +12 |
| X-ray Chest | +10 |
| Other | +5 |
| AI triage confidence (0 to 1) | up to +20 (0 if absent) |
| Waiting time | +0.05 per minute, capped at +20 (so routine cases do not starve) |

Buckets: **>= 70 Critical**, **40 to 69 High**, **below 40 Routine**. The worklist shows the breakdown so every priority is explainable.

### SLA targets

| Priority | Target turnaround (upload to report) |
|---|---|
| Critical | 60 min |
| High | 240 min |
| Routine | 1440 min |

A case is **at risk** when less than 25% of its SLA window remains.

### Routing (`routing.py`)

1. Map body part to subspecialty (brain/head/spine/neck -> Neuro; chest/lung/cardiac/thorax -> Chest; knee/bone/hip/shoulder/ankle/extremity -> MSK; else General).
2. Keep active radiologists of that subspecialty (fallback: General).
3. Drop anyone at `max_capacity`.
4. Choose the lowest weighted load (Critical 3, High 2, Routine 1); tie-break on faster average report time.
5. Store a plain-English reason in `assignments.assignment_reason`.
6. If nobody is free, the case waits in the queue unassigned.

`POST /api/rebalance` moves Routine cases off overloaded radiologists when the load gap exceeds the threshold in `config.py`.

---

## 6. The experiment

```powershell
python simulate.py          # runs the simulation, writes CSVs + charts
python regen_charts.py      # redraws charts only, from existing CSVs
```

The same 100 simulated cases (seed 42, Poisson arrivals at 20/hour, 6 simulated radiologists with capacities 5 to 8) are pushed through three modes:

| Mode | Review time |
|---|---|
| `baseline` | Log-normal review time by modality and priority (sigma 0.30) |
| `cbir` | Baseline time reduced by `0.15 x retrieval precision` |
| `cbir_rerank` | Baseline time reduced by `0.25 x retrieval precision` |

Retrieval precision per case is **simulated** (uniform 0.60 to 0.98), so the average effective reduction is about 0.79 times the factor. The simulator uses the real `compute_priority` and the same routing rules as `routing.py`, re-implemented inside `simulate.py`'s queue.

Outputs: `outputs/experiment_results.csv`, `outputs/experiment_cases.csv`, `outputs/experiment_comparison.png`, `static/images/experiment_charts.png`, and the `/experiment` page.

### Results

| Mode | Mean review (min) | Mean turnaround (min) | p90 turnaround (min) | SLA breach % | Utilization % | Throughput / 8h |
|---|---|---|---|---|---|---|
| baseline | 11.21 | 31.08 | 66.68 | 4.0 | 52.8 | 135.8 |
| cbir | 9.88 | 25.68 | 62.77 | 3.0 | 49.6 | 144.6 |
| cbir_rerank | 8.99 | 22.62 | 52.43 | 1.0 | 47.9 | 153.3 |

Review time falls 11.8% (CBIR) and 19.8% (re-ranking). Turnaround falls 17.4% and 27.2%, more than review time alone, because shorter reads also shorten the queue.

### How to read these numbers

- The review-time reductions are **assumptions fed into the simulation**, not findings. The experiment shows what such reductions would do to turnaround, SLA breaches and throughput. It does not prove that CBIR saves reading time.
- The Wilcoxon p-values and bootstrap intervals are tiny because the same cases are run in every mode with a built-in reduction. Describe them as "the simulated effect is consistent across cases", not as clinical evidence.
- All workflow parameters are hypothetical and listed in `ASSUMPTIONS.md`.

### Optional: measured review times

If real timings are collected, save them as `outputs/measured_review_times.csv` and `simulate.py` uses them instead of the synthetic times:

```
case_id,mode,seconds
SIM-0001,baseline,95
SIM-0001,cbir,71
SIM-0001,cbir_rerank,66
```

`case_id` values must match the simulated IDs (`SIM-0001` to `SIM-0100`), and every mode needs rows for the cases you want overridden.

---

## 7. Connecting Member 2's portal

Full details are in `MEMBER2_HANDOFF.md`. In short: after an upload is saved, the portal sends one `POST /api/cases/new` with `case_id`, `clinic_id`, `modality`, `body_part`, `patient_age`, `patient_gender`, `view_position`, `image_path`, and saves a readable PNG/JPEG copy of the image under `static/images/<case_id>.png`. Test without the portal:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/api/cases/new -ContentType "application/json" -Body '{"case_id":"CASE-5001","clinic_id":"CLN-001","patient_ref":"PT-5001","modality":"X-ray","body_part":"Chest","urgency_flag":0,"image_path":"static/images/CASE-5001.png","patient_age":58,"patient_gender":"M","view_position":"PA"}'
```

A `201` response with an assigned radiologist means it works.

---

## 8. Demo on other devices

`127.0.0.1` only works on your own laptop. To let others on the same Wi-Fi or hotspot open the site, set the last line of `app.py` to `app.run(host="0.0.0.0", port=5000, debug=False)`, find your IPv4 address with `ipconfig`, and open `http://<your-ip>:5000`. Keep `debug=False` when the app is reachable by others.

---

## 9. Report-ready checklist

**Screenshots:** role selector; a radiologist worklist (priority badges, SLA countdown); case view with live similar cases; hospital dashboard; clinic status; `/experiment`.

**Tables and figures:** priority weights, SLA targets, routing rules, status diagram (section 5); experiment results and `outputs/experiment_comparison.png` (section 6); Member 1's Precision@10 and mAP.

**Limitations to state**
- Retrieval covers chest X-rays only (the dataset). Neuro and MSK routing exists but is not exercised with real images.
- Workflow timings, radiologist profiles, arrival rates and review-time reductions are simulated assumptions, because hospital operational data is not public.
- The simulation's retrieval precision is simulated, not taken from Member 1's measured values.
- Routing is rule-based; learning-based routing is future work.
- Local SQLite, single site, no real PACS/DICOM integration, no authentication.
- Thumbnails of similar cases appear only if the pool images are copied into `static/images/`; otherwise a placeholder is shown (labels and scores are still real).

---

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| `SyntaxError` on start | `python -m py_compile app.py` shows the line |
| Findings show `[MOCK]` | AI window not running, `USE_MOCK_CBIR` not `0`, image missing, or case not created through the webhook |
| `assigned to None` for many cases | Capacity full: re-seed, then run the capacity update (section 2) |
| Worklist full of `EXPIRED` | Old seed timestamps: `python seed.py --reset` |
| No Start Review button | The case is not `Assigned`; open it from a radiologist's worklist |
| Thumbnails say "not in local pool" | Expected without the pool images; labels are real |
| pip fails with `flit_core` / PyTorch index | Use `pip install torch torchvision` without `--index-url` |
| `Index/CSV mismatch` at AI startup | Member 1's files do not match; ask for a matching pair |
| CSS or charts not updating | Hard refresh with Ctrl+Shift+R |
| Port 5000 busy | Change the port at the bottom of `app.py` |