# NorgesGruppen — object detection (NM i AI 2026)

Detect grocery products on store shelves. Upload **model code as a `.zip`**; it runs in a **sandboxed Docker** container on the organisers’ servers (**no network**).

## How it works

1. **Download** training data from the **competition website** (login).
2. **Train** an object detector **locally**.
3. Implement **`run.py`**: shelf images in → predictions JSON out.
4. **Zip** code + weights; **upload** on the **Submit** page.
5. **Scoring** → leaderboard.

## Submission zip structure

**`run.py` must be at the zip root** (not inside `my_submission/run.py`).

```
submission.zip
├── run.py          # required entry point
├── model.onnx      # optional weights (.pt, .onnx, .safetensors, .npy)
└── utils.py        # optional helpers (max 10 .py files total — see limits)
```

### Limits

| Limit | Value |
| --- | ---: |
| Max zip size (**uncompressed**) | **420 MB** |
| Max **files** | **1000** |
| Max **Python** files (`.py`) | **10** |
| Max **weight** files (`.pt`, `.pth`, `.onnx`, `.safetensors`, `.npy`) | **3** |
| Max **total** weight size | **420 MB** |
| **Allowed extensions** | `.py`, `.json`, `.yaml`, `.yml`, `.cfg`, `.pt`, `.pth`, `.onnx`, `.safetensors`, `.npy` |

*(**Security:** some extensions are allowed by the packer but **imports** like **`yaml`** are blocked at scan time — use **`json`** for config. **`.bin`** is **not** allowed; rename HuggingFace **`.bin`** → **`.pt`** or use **`.safetensors`**.)*

### Creating the zip

**`run.py` at root is the most common submission error.**

**Linux / macOS (Terminal):**

```bash
cd my_submission/
zip -r ../submission.zip . -x ".*" "__MACOSX/*"
```

**Windows (PowerShell):**

```powershell
cd my_submission
Compress-Archive -Path .\* -DestinationPath ..\submission.zip
```

Do **not** rely on Finder “Compress” or “Send to → Compressed folder” — they nest paths wrong.

**Verify:**

```bash
unzip -l submission.zip | head -10
```

You should see **`run.py`** at the top level — **not** `my_submission/run.py`.

## `run.py` contract

**Invocation** — use **`python3`** locally; the sandbox image may call the same entrypoint via the `python` alias (both are Python **3.11**).

```bash
python3 run.py --input /data/images --output /output/predictions.json
```

### Input

- **`/data/images/`** — JPEG shelf images, names **`img_XXXXX.jpg`** (e.g. **`img_00042.jpg`**).

### Output

Write a **JSON array** to **`--output`**:

```json
[
  {
    "image_id": 42,
    "category_id": 0,
    "bbox": [120.5, 45.0, 80.0, 110.0],
    "score": 0.923
  }
]
```

### Prediction fields

| Field | Type | Description |
| --- | --- | --- |
| **`image_id`** | **int** | From filename: **`img_00042.jpg`** → **`42`** (`int` after last **`_`**) |
| **`category_id`** | **int** | Product id **0–355** (see **`categories`** in **`annotations.json`**) |
| **`bbox`** | **`[x, y, w, h]`** | **COCO** XYWH in **pixels** |
| **`score`** | **float** | Confidence **0–1** |

**Training data** also lists **`id`: 356** as **`unknown_product`** in **`annotations.json`**. For **detection-only** (see **Scoring** below), the documented baseline is **`category_id: 0`** on **every** prediction.

## Scoring (hybrid)

Final score is a weighted blend of two **mAP@0.5** terms (Mean Average Precision at **IoU = 0.5**):

**Score = 0.7 × detection_mAP + 0.3 × classification_mAP**

**Range:** **0.0** (worst) → **1.0** (perfect).

### Detection mAP (70% of final score)

Measures **whether products were found** — **class labels are ignored**.

- Each prediction is matched to the **closest** ground-truth box (per the evaluator’s matching rules).
- A prediction counts as a **true positive** if **IoU ≥ 0.5** with that GT box (**`category_id` does not matter**).
- Rewards **tight, well-localized** boxes.

### Classification mAP (30% of final score)

Measures **correct product identity** once localization is good enough:

- A prediction is a **true positive** only if **IoU ≥ 0.5** **and** **`category_id`** equals the ground-truth category.
- **356** product classes — ids **0–355** from **`annotations.json`** (training data).

### Detection-only submissions

If **`category_id: 0`** (or effectively detection-only behaviour per organiser rules) is used **for all** predictions, you can score up to **0.70** from the **detection** term alone. Correct **product ids** unlock the remaining **30%**.

Plain **COCO** checkpoints output wrong class ids (**0–79**) unless fine-tuned on this dataset.

## Submission quotas (platform)

Limits apply per **team**. **Reset at midnight UTC.**

| Limit | Value |
| --- | ---: |
| **Submissions in-flight** | **2** |
| **Submissions per day** | **3** |
| **Infrastructure failure “freebies”** | **2** / day *(do not count against the **3**)* |

If the platform fault bucket is exhausted, further **infrastructure** failures consume a **normal** daily slot.

## Leaderboard & final ranking

- **Public leaderboard:** scores on the **public** test set.
- **Final ranking:** uses a **private** test set — **never** shown to participants.

### Select for final evaluation

By default, your **best public score** is used for **final private** evaluation. You can **override** this: in submission history, use **“Select for final”** on any **completed** run you trust (even if it is **not** the top leaderboard score). You may **change** this choice any time **before** the competition ends.

## Sandbox environment

| Resource | Limit |
| --- | --- |
| **Python** | **3.11** |
| **CPU** | **4 vCPU** |
| **Memory** | **8 GB** (RAM) |
| **GPU** | **NVIDIA L4**, **24 GB VRAM** |
| **CUDA** | **12.4** |
| **Network** | **None** (offline) |
| **Timeout** | **300 s** |

**No `pip install` at runtime.**

### GPU

- **L4** is **always** present — **`torch.cuda.is_available()`** is **`True`** in the sandbox.
- No special flag to “enable” GPU.
- **ONNX:** `providers=["CUDAExecutionProvider", "CPUExecutionProvider"]`

**OOM / exit 137:** respect **8 GB system RAM** as well as VRAM — process images **one at a time**, avoid huge batches, prefer **FP16** / smaller models.

### Pre-installed packages (pinned)

PyTorch **2.6.0+cu124**, torchvision **0.21.0+cu124**, ultralytics **8.1.0**, onnxruntime-gpu **1.20.0**, opencv-python-headless **4.9.0.80**, albumentations **1.3.1**, Pillow **10.2.0**, numpy **1.26.4**, scipy **1.12.0**, scikit-learn **1.4.0**, pycocotools **2.0.7**, ensemble-boxes **1.0.9**, timm **0.9.12**, supervision **0.18.0**, safetensors **0.4.2**

Train with **matching** versions when submitting **native** `.pt` weights; otherwise **export ONNX**.

### Models in sandbox vs not

**Pre-installed (pin versions for `.pt` submit):**

| Framework | Models (examples) | Pin |
| --- | --- | --- |
| **ultralytics 8.1.0** | YOLOv8 n/s/m/l/x, YOLOv5u, RT-DETR-l/x | `ultralytics==8.1.0` |
| **torchvision 0.21.0** | Faster R-CNN, RetinaNet, SSD, FCOS, Mask R-CNN | `torchvision==0.21.0` |
| **timm 0.9.12** | ResNet, EfficientNet, ViT, Swin, ConvNeXt, … | `timm==0.9.12` |

**Not installed:** YOLOv9/v10/v11, RF-DETR, Detectron2, MMDetection, HuggingFace Transformers, etc.

**Options:** **ONNX** export (**opset ≤ 20**; **onnxruntime 1.20** — use **≤17** if unsure) + **`onnxruntime`** in **`run.py`**; or **model class in `.py`** + **`state_dict`** **`.pt`**. **`torch.save(state_dict)`** preferred over full-model pickle across versions.

**Weights > 420 MB:** quantize (**FP16** recommended on L4).

### Version compatibility (risks)

| Risk | What happens | Fix |
| --- | --- | --- |
| **ultralytics 8.2+** weights on **8.1.0** | Load / class head mismatch | Pin **8.1.0** or **ONNX** |
| **torch 2.7+** full model on **2.6.0** | New ops | **`state_dict`** only, or **ONNX** |
| **timm 1.0+** weights on **0.9.12** | Renamed layers | Pin **0.9.12** or **ONNX** |
| **ONNX opset > 20** | **`onnxruntime`** load fails | Export **`opset_version ≤ 20`** (e.g. **17**) |

### Recommended weight formats

| Approach | Format | When |
| --- | --- | --- |
| ONNX export | **`.onnx`** | Any framework; often fast on GPU |
| Ultralytics (pinned) | **`.pt`** | YOLO / RT-DETR quick path |
| Custom net + code | **`.pt`** `state_dict` | Standard **PyTorch** ops only |
| Safe format | **`.safetensors`** | No pickle |

## Security restrictions (scanner)

**Blocked imports** include: **`os`**, **`sys`**, **`subprocess`**, **`socket`**, **`ctypes`**, **`builtins`**, **`importlib`**, **`pickle`**, **`marshal`**, **`shelve`**, **`shutil`**, **`yaml`**, **`requests`**, **`urllib`**, **`http.client`**, **`multiprocessing`**, **`threading`**, **`signal`**, **`gc`**, **`code`**, **`codeop`**, **`pty`**, …

**Blocked calls:** **`eval`**, **`exec`**, **`compile`**, **`__import__`**, dangerous **`getattr`**, …

**Also:** no ELF/Mach-O/PE blobs, symlinks, path traversal.

Use **`pathlib`** (not **`os.path`**) and **`json`** (not **`yaml`**) for configs.

## Downloads

From the **Submit** page (login):

| Asset | File (typical) | Size (approx.) |
| --- | --- | --- |
| **COCO training set** | `NM_NGD_coco_dataset.zip` | ~864 MB |
| **Product reference photos** | `NM_NGD_product_images.zip` | ~60 MB |

### COCO dataset (`NM_NGD_coco_dataset.zip`)

- **248** shelf images (Norwegian grocery stores).
- **~22,700** COCO-format bounding-box annotations.
- **Categories:** ids **0–355** (products) plus **356** = **`unknown_product`** in **`annotations.json`** — **357** rows; **YOLO** training often uses **`nc=357`** so class indices match COCO **`category_id`**.
- Store sections: **Egg**, **Frokost**, **Knekkebrod**, **Varmedrikker**.

### Product reference images (`NM_NGD_product_images.zip`)

- **327** products; angles: **`main`**, **`front`**, **`back`**, **`left`**, **`right`**, **`top`**, **`bottom`**.
- Paths: **`{product_code}/main.jpg`**, …
- **`metadata.json`**: names, counts, etc.

## Annotation format (`annotations.json`)

**`bbox`** = **`[x, y, width, height]`** (XYWH). Extra fields: **`product_code`**, **`product_name`**, **`corrected`**.

```json
{
  "images": [
    {"id": 1, "file_name": "img_00001.jpg", "width": 2000, "height": 1500}
  ],
  "categories": [
    {"id": 0, "name": "VESTLANDSLEFSA TØRRE 10STK 360G", "supercategory": "product"},
    {"id": 1, "name": "COFFEE MATE 180G NESTLE", "supercategory": "product"},
    {"id": 356, "name": "unknown_product", "supercategory": "product"}
  ],
  "annotations": [
    {
      "id": 1,
      "image_id": 1,
      "category_id": 42,
      "bbox": [141, 49, 169, 152],
      "area": 25688,
      "iscrowd": 0,
      "product_code": "8445291513365",
      "product_name": "NESCAFE VANILLA LATTE 136G NESTLE",
      "corrected": true
    }
  ]
}
```

## Examples & tips

### Random baseline

Use **`category_id` in `0..355`** to match submission spec; **`img_00042` → 42**.

```python
import argparse
import json
import random
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    predictions = []
    for img in sorted(Path(args.input).iterdir()):
        if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        image_id = int(img.stem.split("_")[-1])
        for _ in range(random.randint(5, 20)):
            predictions.append({
                "image_id": image_id,
                "category_id": random.randint(0, 355),
                "bbox": [
                    round(random.uniform(0, 1500), 1),
                    round(random.uniform(0, 800), 1),
                    round(random.uniform(20, 200), 1),
                    round(random.uniform(20, 200), 1),
                ],
                "score": round(random.uniform(0.01, 1.0), 3),
            })

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(predictions, f)

if __name__ == "__main__":
    main()
```

**Detection-only smoke test:** use **`"category_id": 0`** for every box.

### YOLOv8 example

Fine-tune with **`nc=357`** so outputs align with **`annotations.json`**. Submit **native** `.pt` only if trained with **`ultralytics==8.1.0`**.

```python
import argparse
import json
from pathlib import Path
import torch
from ultralytics import YOLO

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = YOLO("yolov8n.pt")
    predictions = []

    for img in sorted(Path(args.input).iterdir()):
        if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        image_id = int(img.stem.split("_")[-1])
        results = model(str(img), device=device, verbose=False)
        for r in results:
            if r.boxes is None:
                continue
            for i in range(len(r.boxes)):
                x1, y1, x2, y2 = r.boxes.xyxy[i].tolist()
                predictions.append({
                    "image_id": image_id,
                    "category_id": int(r.boxes.cls[i].item()),
                    "bbox": [round(x1, 1), round(y1, 1), round(x2 - x1, 1), round(y2 - y1, 1)],
                    "score": round(float(r.boxes.conf[i].item()), 3),
                })

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(predictions, f)

if __name__ == "__main__":
    main()
```

### ONNX export / inference

**Export (training machine):** `opset_version` **≤ 20** (e.g. **17**).

```python
from ultralytics import YOLO
model = YOLO("best.pt")
model.export(format="onnx", imgsz=640, opset=17)
```

**Inference in `run.py`:**

```python
import argparse
import json
import numpy as np
from pathlib import Path
from PIL import Image
import onnxruntime as ort

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    session = ort.InferenceSession("model.onnx", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    predictions = []

    for img_path in sorted(Path(args.input).iterdir()):
        if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        image_id = int(img_path.stem.split("_")[-1])

        img = Image.open(img_path).convert("RGB").resize((640, 640))
        arr = np.array(img).astype(np.float32) / 255.0
        arr = np.transpose(arr, (2, 0, 1))[np.newaxis, ...]

        outputs = session.run(None, {input_name: arr})
        # Decode outputs → predictions (xyxy → xywh, cls, conf)
        # ...

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(predictions, f)

if __name__ == "__main__":
    main()
```

## Common errors

| Error | Fix |
| --- | --- |
| **`run.py` not at zip root** | Zip **contents** from inside submission folder; **`unzip -l`** must show **`run.py`** first. |
| **`__MACOSX/`** / disallowed paths | `zip -r ../submission.zip . -x ".*" "__MACOSX/*"` |
| **`.bin` not allowed** | Rename → **`.pt`** or **`.safetensors`**. |
| **Security scan** | Remove blocked imports (**`os`**, **`subprocess`**, **`yaml`**, …); use **`pathlib`** + **`json`**. |
| **Empty / missing output file** | Write exactly to **`--output`**. |
| **Timeout 300s** | Smaller model, **ONNX**, **FP16**, single-image loop. |
| **Exit 137** | **RAM** **8 GB** + VRAM limits — reduce footprint, **FP16**, batch **1**. |
| **Exit 139** | Version mismatch — re-export **ONNX** or pin training versions. |
| **`ModuleNotFoundError`** | **ONNX** or inline code only — no new deps in sandbox. |

## Tips

- **`torch.no_grad()`** during inference; **`torch.cuda.is_available()`** for device.
- **Train anywhere** (Colab, laptop, cloud); weights run on sandbox **L4** without forced **`map_location="cpu"`** if versions match.
- Qualitative QA: compare full-GT visualization vs loose boxes / missed products.

## MCP (documentation server)

```bash
claude mcp add --transport http nmiai https://mcp-docs.ainm.no/mcp
```

## Local workspace

Repo layout often mirrors downloads under **`norgesgruppen/`** (e.g. `train/annotations.json`, `train/images/`, `NM_NGD_product_images/`). **Quick commands:** [`norgesgruppen/README.md`](../norgesgruppen/README.md). Local **`train.py`** writes **`dataset.yaml`** with both **`train:`** and **`val:`** ( **`val: images/train`** reuses the train folder until a real validation split exists — some Ultralytics versions expect **`val`** to be set). **Local training:** **`train.py`** uses **`cuda` → `mps` → `cpu`**; default batch **16** / **8** / **4**; default **`--model s`**, **`--epochs` 150**, **`patience` 20**, optional **`--cos-lr`**; **`imgsz`** **640**. On **non-CUDA**, Ultralytics still runs validation on the **last epoch** and **`final_eval`**; that can crash (**Half** / DFL) with many classes. **`train.py`** then applies a **temporary monkeypatch** (`BaseTrainer.validate` / **`final_eval`**) so training finishes; **fitness** is approximated from **train loss** — treat **`best.pt`** as a hint, prefer **`last.pt`** or re-validate on **CUDA**. **`--force-val`** disables the patch and runs real validation (may crash on **MPS**). **`run.py`** optionally loads **ResNet18** embeddings over **`NM_NGD_product_images/`** when **`annotations.json`** is co-located — see **Improvement plan — 2026-03-22** above.

**PyTorch 2.6+ and Ultralytics:** `torch.load` defaults to **`weights_only=True`**, which breaks unpickling full **Ultralytics** **`.pt`** files (they reference many module types, not only **`DetectionModel`**). **`run.py`** and **`train.py`** register **`torch.serialization.add_safe_globals([DetectionModel])`** and wrap **`torch.load`** so **`weights_only`** defaults to **`False`** when the caller omits it (same idea as trusted-checkpoint loading), **before** any **`YOLO(...)`** call.

---

## Improvement plan — 2026-03-22

### PUNKT 1 — Product-image retrieval in `run.py` (done in repo)

**Problem:** Final score is **`0.7 × detection_mAP + 0.3 × classification_mAP`**. A plain YOLO head on **357** classes (or COCO-pretrained **80** classes) gives poor **classification_mAP** unless the detector is well fine-tuned.

**Approach:** At startup, **`run.py`** (when files are present) builds an in-memory **reference library**:

1. **`NM_NGD_product_images/metadata.json`** + **`train/annotations.json`** (or **`annotations.json`** next to **`run.py`**) → map **`product_code`** (folder name) → **`category_id`** by matching **`product_name`** to COCO **`categories[].name`** (normalized uppercase / spaces). One product in metadata needed **fuzzy** match (`difflib`, cutoff **0.82**); **326** exact + **1** fuzzy in local snapshot.
2. For each folder under **`NM_NGD_product_images/{product_code}/`**, load up to **3** images (priority: **`main`**, **`front`**, **`back`**, …).
3. **torchvision `resnet18`** (**`ResNet18_Weights.IMAGENET1K_V1`**, **`fc` → `Identity`**, **L2-normalized** **512**-D embedding). **ImageNet** **`Normalize`** mean/std are **hardcoded** in **`run.py`** (not **`weights.meta`**) for compatibility with older **torchvision** on **Python 3.9**. Weights load from the usual **Torch** cache (**`~/.cache/torch`** / hub) — **no network** in sandbox if weights are already cached (sandbox ships **torchvision 0.21.0**).
4. After each YOLO box, **crop XYXY** (clamped), embed, **cosine similarity** vs all reference vectors → **nearest neighbor** **`category_id`**. If **max similarity &lt; 0.3** (override with **`run.py --sim-threshold`**), keep the **YOLO class** (same as before).
5. Output JSON unchanged: **`[{image_id, category_id, bbox, score}]`**.

**Submission zip:** To enable this in the sandbox, include alongside **`run.py`** / **`model.pt`**:

- **`train/annotations.json`** (or **`annotations.json`** at zip root) — for **name → id** alignment only;  
- **`NM_NGD_product_images/`** tree (**`metadata.json`** + **`{product_code}/*.jpg`**).  
- **Offline sandbox:** place **`resnet18-f37072fd.pth`** (ImageNet **ResNet18** `state_dict`) next to **`run.py`** — **`run.py`** loads it with **`weights_only=True`**; if missing, it falls back to **`ResNet18_Weights.IMAGENET1K_V1`** (needs download — not available without network in sandbox).

Keep **uncompressed zip ≤ 420 MB** (reference pack ≈ **60 MB** — fits).

If those paths are missing, **`run.py`** skips the library and behaves like **YOLO-only** classification.

### PUNKT 2 — Train **YOLOv8s** instead of **YOLOv8n** (done in repo defaults)

**`train.py`** default **`--model`** is now **`s`** (was **`m`** in code / **`n`** in older docs). Larger backbone → better **detection_mAP** (the **0.7** term). **Run on GCP GPU** for throughput; reduce **`--batch`** if needed vs **`n`**.

### PUNKT 3 — Longer training + cosine LR (done in repo)

**`train.py`:** default **`--epochs`** **150**, **`patience=20`**, optional **`--cos-lr`** → passes **`cos_lr=True`** into **`model.train()`** (Ultralytics **8.1** **`default.yaml`**).

**Suggested GCP command:**

```bash
python3 train.py --data train --model s --epochs 150 --cos-lr
```

### Findings

- **Reference ↔ COCO alignment:** **`metadata.json`** **`product_name`** vs **`annotations.json`** **`categories[].name`** gives **~327** mapped products locally (exact + one fuzzy).  
- **Classification_mAP** should improve when **detection** crops align with **reference pack** photos (same SKU, different shelf lighting — **ResNet** retrieval is a **baseline**, not a ceiling).
