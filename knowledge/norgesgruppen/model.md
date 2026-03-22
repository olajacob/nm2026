# NorgesGruppen — model & training notes

**Router:** [`../INDEX.md`](../INDEX.md) · **Sandbox / zip:** [`sandbox.md`](sandbox.md) · **Long-form track doc:** [`../norgesgruppen.md`](../norgesgruppen.md)

## Scoring breakdown (official + observed)

- **70%** detection mAP @ IoU≥0.5 (class ignored at detection stage).
- **30%** classification @ IoU≥0.5 with correct **`category_id`** in **0–355**.
- **248** shelf images, **22700** annotations, **356** categories (**0–355**).
- Detection-only baseline: **`category_id": 0`** everywhere → up to **~0.70** on the classification component ignored; composite **floor** observed with YOLO-only / failed ResNet: **~0.0846**.
- **Theoretical composite** with stronger detector + classification often discussed in **~0.15–0.25** range (depends on grader); verify on leaderboard.

## Models trained

| Model | Architecture | Epochs | mAP50 | Notes |
|-------|----------------|--------|-------|-------|
| ng_model (Mac MPS) | YOLOv8n | ~39 | unstable | MPS crash, batch too high |
| ng_model (GCP T4) | YOLOv8n | 100 | 0.031 | First good model; `best.pt` ≈ **6.8 MB** |
| ng_model2 (GCP T4) | YOLOv8s | 150 | **0.487** | ~15× better than v8n; `best.pt` ≈ **22 MB** |
| ng_model3 (GCP T4) | YOLOv8m | 150 | *in progress* | batch=8, cos-lr (e.g. started 10:02) |

## Submission history (team)

| Submission | Score | Notes |
|------------|-------|-------|
| submission_v1 | 0.0024 | Wrong ZIP structure — `run.py` not at root |
| submission_v2 | 0.0846 | YOLOv8n; YOLO-only (ResNet18 path failed) |
| submission_ng2 | 0.0846 | ResNet18 failed: `np.load(..., allow_pickle=True)` blocked |
| submission_ng3 | 0.0846 | ResNet18 failed: `metadata.json` missing / wrong path |
| submission_ng_final | 0.0849 | ResNet18 working; marginal gain |

## What worked

- **YOLOv8s ≫ YOLOv8n** (mAP50 **0.487** vs **0.031**).
- **GCP T4 ≫ Mac MPS** — stable training, faster.
- **cos-lr** scheduler helps (`train.py` — `--cos-lr`).
- **Pre-computed embeddings** as **`product_embeddings.json`** avoids pickle / sandbox issues.
- **`metadata.json` at ZIP root** (not only under `NM_NGD_product_images/`) so `run.py` finds it with fallback layout.

## What failed

- **`np.load(..., allow_pickle=True)`** → **blocked** in sandbox (use **JSON** embeddings).
- **`NM_NGD_product_images/`** folder inside ZIP → **“no disallowed files”** / rejection.
- **ResNet18 download** in sandbox → **no internet** — bundle **`resnet18-f37072fd.pth`**.
- ZIP with **`.DS_Store`**, **`__MACOSX`**, hidden junk → rejected — exclude with **`-x ".*" "__MACOSX/*"`**.
- **`model.pt` from unstable Mac MPS** run → **~41 MB**, poor reliability.

## GCP VM (training)

- **VM:** `ng-trainer`, **zone:** `us-central1-a`, **machine:** n1-standard-4, **GPU:** Tesla T4.
- **SSH:** `gcloud compute ssh ng-trainer --zone=us-central1-a`
- **Weights (example):** `~/nm2026/norgesgruppen/runs/detect/ng_model2/weights/best.pt`
- **After competition — delete VM** to stop charges:  
  `gcloud compute instances delete ng-trainer --zone=us-central1-a`

## Repo commands (local)

- Data: **`nmai2026/norgesgruppen/train/`** (`annotations.json` + `images/`).
- Train:  
  `cd nmai2026/norgesgruppen && python3 train.py --data train --model s --epochs 150 --cos-lr`  
  (adjust `--model m` for ng_model3-style run.)
- Ship: copy **`runs/detect/<run>/weights/best.pt`** → **`model.pt`** next to **`run.py`**.
- Embeddings: **`python3 generate_embeddings.py`** → **`product_embeddings.json`** (see **`sandbox.md`**).

## Torch / Ultralytics (inference)

- **`run.py`** / **`train.py`**: `torch.serialization.add_safe_globals([DetectionModel])` and **`torch.load`** wrapper (`weights_only=False` when unspecified) for Ultralytics on PyTorch **2.6+**.
- ResNet18 load in **`run.py`**: use **`weights_only=True`** for **`resnet18-f37072fd.pth`** where passed explicitly.

## Classification add-on

- **`run.py`**: loads **`product_embeddings.json`**, maps **product_code → category_id** via **`metadata.json`** + **`train/annotations.json`**, nearest neighbour on crop if similarity ≥ **`SIM_THRESHOLD`** (default **0.3** in code — consider **0.15** for more overrides; see **Next steps**).

## Next steps to improve

1. Submit **ng_model2** (YOLOv8s, mAP50 **0.487**) — expect score **> 0.10** (validate on platform).
2. Submit **ng_model3** (YOLOv8m) if training finishes before cutoff (e.g. **13:00** on submit day).
3. Lower **`SIM_THRESHOLD`** in **`run.py`** from **0.3** → **~0.15** for more classification hits (watch false positives).
4. Regenerate **`product_embeddings.json`** after material model / crop behaviour changes.

## Deeper reference

- **`nmai2026/knowledge/NG_SCORING.md`**, **`norgesgruppen/README.md`**.
