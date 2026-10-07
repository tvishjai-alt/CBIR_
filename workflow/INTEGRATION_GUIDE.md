# Integration Guide (2-minute read)

How the three modules connect. **Status: Member 1's AI is integrated and running. Member 2's portal connects through one webhook (details in `MEMBER2_HANDOFF.md`).**

```
Member 2: Upload portal --POST /api/cases/new--> Member 3: Workflow app (:5000)
                                                        |
                                    GET /retrieve/<case_id>?k=10
                                                        v
                                    Member 3's wrapper (:8000) around Member 1's AI engine
                                                        |
      Member 3 scores priority, routes the case, shows radiologist worklist, dashboards
```

---

## Agreed conventions

| Item | Decision |
|---|---|
| Dataset | NIH ChestX-ray14, 5,000-image pool (Member 1's `database_pool_active.csv` + `faiss_database.index`). Chest X-rays only. |
| `case_id` | `CASE-####`, unique. `0001` to `0115` demo data, `2001` to `2040` demo uploads, **`5000+` for Member 2**. |
| Patient IDs | Fake only (`PT-####`). |
| Ports | Member 3 app `5000`, Member 1 AI wrapper `8000`. |
| Query image | A readable PNG/JPEG at `workflow/static/images/<case_id>.png`. |

---

## Member 1 (AI / CBIR): how your engine is used

Your `cbir_service.py` is used **unmodified**. Member 3 wraps it in `member1/cbir_server.py`, which exposes `GET /retrieve/<case_id>?k=10` and calls your `retrieve_similar_cases(image_path, patient_age, patient_gender, view_position, alpha=0.85, top_k=20)`.

Mapping to what the dashboard shows:

| Your field | Dashboard field |
|---|---|
| `image_index` (no extension) | `similar_case_id` |
| `visual_score` | `score` |
| `final_relevance_score` | `reranked_score` (results are ordered by this) |
| `pathologies`, `patient_age`, `patient_gender`, `view_position` | `finding`, e.g. `Cardiomegaly, Effusion · 58y M · PA` |

Details to know:
- The query image itself and any other image **of the same patient** are removed from results (follow-up scans would otherwise fill the top 10).
- The engine loads once at startup and checks that the index and CSV sizes match (5000 = 5000).
- `alpha` defaults to your 0.85, changeable with the `CBIR_ALPHA` environment variable.

**Still needed from Member 1 for the report:** Precision@10 and mAP for visual-only vs context re-ranked retrieval, and the 5,000-image pool folder (`images-224`) if you want real thumbnails in the demo.

---

## Member 2 (upload platform)

See **`MEMBER2_HANDOFF.md`**: the webhook payload, image requirements, responses and how to fetch the report.

---

## What the workflow app does automatically after a case arrives

Store case -> retrieve similar cases -> compute priority score (urgency, modality/body part, waiting time) -> assign radiologist (subspecialty, lowest load, capacity) -> radiologist worklist -> report -> hospital dashboard and clinic status.

Status lifecycle: `Uploaded -> Stored -> Retrieved -> Assigned -> In_Review -> Reported -> Returned_To_Clinic`.

---

## Live vs mock retrieval

- Set `USE_MOCK_CBIR=0` (environment variable) to use the live AI. Default is mock.
- Every result carries `source`: `live`, `mock`, or `mock-fallback`.
- If the live call fails (AI process down, image missing, unknown case), the app falls back to mock results and prefixes each finding with **`[MOCK]`**, so fake results are never mistaken for real ones.
- Cases are only retrieved live if they were created through the webhook (it saves their age, gender, view and image path). Old demo cases stay mock.

---

## Running everything (three PowerShell windows, from the `workflow` folder)

1. AI: `member1\venv\Scripts\activate` then `python member1\cbir_server.py` (wait for "Engine ready")
2. App: `venv\Scripts\activate`, `$env:USE_MOCK_CBIR="0"`, `python app.py`
3. Optional demo load: `python make_demo_cases.py --images member1\query_images --n 40`

Health checks: `http://127.0.0.1:8000/health` (AI) and `http://127.0.0.1:5000/api/health` (app).

---

## Limitations to state in the report

- Retrieval covers chest X-rays only (the dataset). Neuro and MSK routing exists in the design but is not exercised with real images.
- Workflow timings, radiologist profiles and review-time reductions are simulated assumptions, because hospital operational data is not public.
- Demo urgency flags are seeded random (about 25%) as a stand-in for clinic triage.
- A few demo images are not in the pool CSV and use default age/gender/view.