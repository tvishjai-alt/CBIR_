# Handoff for Member 2: connecting the upload portal to the workflow system

Your portal does upload, compression, encryption and storage. After a case is saved, make **one HTTP call** to Member 3's app. Everything after that happens automatically: similar-case search (Member 1's AI), priority score, radiologist assignment, worklist, report, dashboards. **You never call Member 1.**

```
Your portal  --POST /api/cases/new-->  Member 3 app (:5000)  --GET /retrieve-->  Member 1 AI (:8000)
```

Member 3 keeps its own database (`telemed_workflow.db`). Yours stays separate. The only link between the two systems is this call.

---

## 1. The one call

```
POST http://127.0.0.1:5000/api/cases/new
Content-Type: application/json
```

```json
{
  "case_id": "CASE-5001",
  "clinic_id": "CLN-001",
  "patient_ref": "PT-5001",
  "modality": "X-ray",
  "body_part": "Chest",
  "urgency_flag": 0,
  "image_path": "static/images/CASE-5001.png",
  "patient_age": 58,
  "patient_gender": "M",
  "view_position": "PA",
  "upload_time": "2026-10-08T10:15:00+00:00"
}
```

| Field | Required | Rules |
|---|---|---|
| `clinic_id` | yes | Must be an existing clinic (the home page of Member 3's app lists them, e.g. `CLN-001`). An unknown clinic returns an error. |
| `modality` | yes | Exactly `X-ray`, `CT` or `MRI` (case-sensitive). |
| `body_part` | yes | e.g. `Chest`. Decides which radiologist subspecialty gets the case. |
| `case_id` | **yes, in practice** | Any unique string, we use `CASE-####`. Without it the AI search has no context for the case and falls back to fake results. Existing IDs: `CASE-0001` to `CASE-0115` (demo data) and `CASE-2001` to `CASE-2040`. **Please use `CASE-5000` and up.** |
| `image_path` | yes, in practice | See section 2. |
| `patient_age` | recommended | Whole number, 0 to 120. Default 50. |
| `patient_gender` | recommended | `M` or `F`. Default `M`. |
| `view_position` | recommended | `PA` or `AP`. Default `PA`. |
| `patient_ref` | recommended | **Fake IDs only**, like `PT-5001`. |
| `urgency_flag` | optional | `1` = clinic marks urgent, `0` = not. Default `0`. |
| `upload_time` | optional | ISO 8601. Defaults to now (UTC). |

The age, gender and view position feed the AI's context re-ranking. Defaults work but make that part less meaningful.

---

## 2. The image (important)

The similar-case search **reads the image file from disk**. So after your portal decrypts and processes the upload:

- Save a **readable copy as PNG or JPEG** at `<workflow folder>/static/images/<case_id>.png` (this is the same machine that runs Member 3's app). Any other absolute path also works if the AI process can read it.
- It must be **decrypted and decompressed**. If you use JPEG2000, convert the viewer copy to PNG or JPEG first, because the AI library may not open it.
- Chest X-rays are what the AI is trained for. Other body parts will still be routed, but they get no meaningful similar cases.

If the file is missing or unreadable, the case still gets created and routed, but the similar-case panel shows fake results, each marked `[MOCK]`.

---

## 3. Responses

| Code | Meaning | Body |
|---|---|---|
| `201` | Created, scored and assigned | `{"status":"success","data":{"case_id":"...","assigned_rad_id":"RAD-002", ...}}` |
| `400` | Missing or invalid field | `{"status":"error","message":"..."}` |
| `500` | Server error | `{"status":"error","message":"..."}` |

`assigned_rad_id` can be empty if every matching radiologist is at capacity. The case then waits in the queue, which is normal.

---

## 4. Getting the report back for the clinic

```
GET http://127.0.0.1:5000/api/case/<case_id>/info
```

Returns `{"status":"success","case":{...}}`. Useful fields in `case`:

`case_id`, `clinic_id`, `patient_ref`, `modality`, `body_part`, `status`, `priority_label`, `assigned_rad_id`, `radiologist_name`, `report_text`, `report_submit_time`.

The report is ready when `status` is `Reported` or `Returned_To_Clinic` and `report_text` is not empty. Unknown case returns `404`.

Status lifecycle: `Uploaded -> Stored -> Retrieved -> Assigned -> In_Review -> Reported -> Returned_To_Clinic`.

---

## 5. Test it without your portal (PowerShell)

1. Copy any chest X-ray PNG to `workflow\static\images\CASE-5001.png`.
2. With Member 3's app running:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/api/cases/new -ContentType "application/json" -Body '{"case_id":"CASE-5001","clinic_id":"CLN-001","patient_ref":"PT-5001","modality":"X-ray","body_part":"Chest","urgency_flag":0,"image_path":"static/images/CASE-5001.png","patient_age":58,"patient_gender":"M","view_position":"PA"}'
```

3. Open `http://127.0.0.1:5000/case/CASE-5001`. You should see the case, its priority, and similar cases with real findings.

Python version of the same call:

```python
import requests
payload = {"case_id": "CASE-5001", "clinic_id": "CLN-001", "patient_ref": "PT-5001",
           "modality": "X-ray", "body_part": "Chest", "urgency_flag": 0,
           "image_path": "static/images/CASE-5001.png",
           "patient_age": 58, "patient_gender": "M", "view_position": "PA"}
r = requests.post("http://127.0.0.1:5000/api/cases/new", json=payload, timeout=60)
print(r.status_code, r.json())
```

Use a generous timeout: the AI search runs during this call.

---

## 6. If your portal and Member 3's app are on different laptops

- Member 3 starts the app with `host="0.0.0.0"` and `debug=False`.
- Both laptops must be on the same Wi-Fi or phone hotspot (college Wi-Fi often blocks this).
- Replace `127.0.0.1` with Member 3's IPv4 address (`ipconfig`), e.g. `http://192.168.1.23:5000/api/cases/new`.

---

## 7. Rules

- Fake patient data only. No real patient information anywhere.
- `case_id` must be unique. Re-sending the same one will fail.
- Member 3's AI process must be running for real similar cases (Member 3 handles this).

## 8. Please answer these (Member 3 needs them)

1. Which `clinic_id` values will your portal send?
2. Where does your portal save the decrypted image, and can it write into `workflow\static\images\`?
3. Will the portal run on the same laptop as Member 3's app for the demo?
4. Does your portal collect patient age, gender and view position at upload time?
5. How will clinic users fetch the finished report: poll `/api/case/<id>/info`, or a button in your portal?