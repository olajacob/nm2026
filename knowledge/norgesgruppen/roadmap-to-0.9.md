# NorgesGruppen — Roadmap to 0.9+

**Related:** [`model.md`](model.md) (training history) · [`sandbox.md`](sandbox.md) (ZIP rules)

## Current baseline (March 22, 2026)
- Model: YOLOv8m, 150 epochs, batch=8, cos-lr, imgsz=640
- mAP50: 0.603 (validation), mAP50-95: 0.416
- Score: 0.6663
- Classification: ResNet18 embeddings (marginal gain ~0.0003)
- Weakness: ~40% categories score 0 (too few training examples)

## Gap analysis
| Component | Our score | Target | Gap |
|-----------|-----------|--------|-----|
| Detection (70%) | ~0.60 | ~0.85 | 0.25 |
| Classification (30%) | ~0.01 | ~0.20 | 0.19 |

---

## Phase 1: Better detection model (Week 1)
**Goal: mAP50 0.60 → 0.72**
**Time: ~8 hours training on GCP T4**

### 1A: YOLOv8l with higher resolution
```bash
python3 train.py --data train --model l --epochs 200 \
  --batch 4 --imgsz 1280 --cos-lr
```
Expected: mAP50 ~0.65-0.68
Reason: Larger model + higher resolution sees more label detail

### 1B: YOLOv8x (extra large)
```bash
python3 train.py --data train --model x --epochs 200 \
  --batch 2 --imgsz 1280 --cos-lr
```
Expected: mAP50 ~0.68-0.72
Reason: Most parameters, best feature extraction
Time: ~6-8 hours on T4

### 1C: Stronger augmentation in train.py
Add to YOLO training config (data.yaml or train.py):
```python
augment=True,
degrees=15,        # rotate up to 15 degrees
translate=0.1,     # shift up to 10%
scale=0.5,         # zoom 50-150%
shear=2.0,         # shear 2 degrees
perspective=0.0001,
flipud=0.0,        # don't flip upside down (shelves are oriented)
fliplr=0.5,        # flip left-right OK
mosaic=1.0,        # keep mosaic
mixup=0.15,        # mix images
copy_paste=0.1     # copy-paste augmentation
```
Expected: +3-5% mAP50

---

## Phase 2: Better classification (Week 1-2)
**Goal: classification contribution 0.01 → 0.15**

### 2A: Replace ResNet18 with CLIP
CLIP understands product names semantically — far better than visual similarity alone.
```python
# Install
pip install transformers torch

# In run.py — replace ResNet18 with CLIP
from transformers import CLIPProcessor, CLIPModel

model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

# Encode all product names as text embeddings at startup
product_names = list(code_to_name.values())  # "KNEKKEBRØD HAVRE 300G WASA" etc
text_inputs = processor(text=product_names, return_tensors="pt", padding=True)
text_embeddings = model.get_text_features(**text_inputs)

# For each detected box: encode crop as image embedding
image_inputs = processor(images=crop, return_tensors="pt")
image_embedding = model.get_image_features(**image_inputs)

# Find nearest product name by cosine similarity
similarity = cosine_similarity(image_embedding, text_embeddings)
best_match = product_names[similarity.argmax()]
category_id = code_to_cat[name_to_code[best_match]]
```
Expected: classification contribution +10-15%

### 2B: Lower SIM_THRESHOLD
Current: 0.3 — too strict, classification rarely fires
Try: 0.15 first, then 0.10
Expected: +2-3% from more classification hits

### 2C: Fine-tune CLIP on product images
```python
# Fine-tune CLIP on NM_NGD_product_images + category names
# Few-shot learning: use product images as positive examples
# Contrastive learning: push correct product-name pairs closer
```
Expected: +5-8% additional classification gain
Time: ~4-6 hours

---

## Phase 3: Test-time augmentation (TTA) (Week 2)
**Goal: +3-5% mAP50 with no additional training**

TTA runs inference multiple times with different augmentations
and averages the predictions.
```python
# In run.py — enable TTA
model = YOLO("model.pt")
results = model.predict(
    img,
    augment=True,  # enables TTA
    conf=0.25,
    iou=0.45
)
```
Expected: mAP50 +3-5% with same model

---

## Phase 4: Ensemble multiple models (Week 2-3)
**Goal: mAP50 0.72 → 0.82**

Train multiple models and combine predictions:
```python
# Train 3 models with different seeds/configs
models = [
    YOLO("best_yolov8m.pt"),
    YOLO("best_yolov8l.pt"), 
    YOLO("best_yolov8x.pt")
]

# Weighted Box Fusion (WBF) ensemble
from ensemble_boxes import weighted_boxes_fusion

all_boxes, all_scores, all_labels = [], [], []
for model in models:
    result = model.predict(img)
    all_boxes.append(result.boxes.xyxyn.tolist())
    all_scores.append(result.boxes.conf.tolist())
    all_labels.append(result.boxes.cls.tolist())

boxes, scores, labels = weighted_boxes_fusion(
    all_boxes, all_scores, all_labels,
    iou_thr=0.55, skip_box_thr=0.1
)
```
Expected: mAP50 +5-8% over best single model

---

## Phase 5: More training data (Week 3-4)
**Goal: mAP50 0.82 → 0.88+**

### 5A: External grocery datasets
- Open Images Dataset (Google) — has grocery/product categories
- COCO dataset — some relevant categories
- Grozi-120 dataset — specifically for retail shelves

### 5B: Synthetic data generation
Use DALL-E or Stable Diffusion to generate additional
product images for underrepresented categories (those scoring 0).

### 5C: Self-training / pseudo-labeling
1. Run best model on unlabeled shelf images
2. Keep high-confidence predictions as pseudo-labels
3. Train next generation model on original + pseudo-labeled data

---

## Phase 6: Fine-tuning at higher resolution (Week 4)
**Goal: mAP50 0.88 → 0.90+**
```bash
# Start from best ensemble checkpoint
# Fine-tune at imgsz=1536 with small LR
python3 train.py --data train \
  --model best_ensemble.pt \
  --epochs 50 \
  --batch 1 \
  --imgsz 1536 \
  --cos-lr \
  --lr0 0.0001
```

---

## Realistic score progression

| Phase | Action | Time | Expected score |
|-------|--------|------|----------------|
| Baseline | YOLOv8m | Done | 0.6663 |
| 1A | YOLOv8l imgsz 1280 | 8h | ~0.72 |
| 1B | YOLOv8x imgsz 1280 | 8h | ~0.75 |
| 2A | CLIP classification | 2d | ~0.79 |
| 3 | TTA | 1h | ~0.82 |
| 4 | Ensemble | 3d | ~0.86 |
| 5B | Synthetic data | 1w | ~0.88 |
| 6 | Fine-tune 1536 | 2d | ~0.90+ |

---

## Infrastructure notes
- GCP VM: ng-trainer, zone=us-central1-a, Tesla T4 14GB VRAM
- For YOLOv8x + imgsz 1280: may need A100 (80GB) — upgrade VM
- Upgrade: gcloud compute instances set-machine-type ng-trainer \
    --machine-type a2-highgpu-1g --zone us-central1-a
- A100 cost: ~$3.67/hour vs T4 $0.35/hour
- REMEMBER: Delete VM when done to avoid charges

## Key files
- train.py: training script (add augmentation params)
- run.py: inference script (add CLIP, TTA, ensemble)
- generate_embeddings.py: pre-compute embeddings
- knowledge/norgesgruppen/model.md: training history
- knowledge/norgesgruppen/sandbox.md: ZIP rules

## Quick wins to try first (highest ROI, lowest effort)
1. Lower SIM_THRESHOLD to 0.15 in run.py (5 min)
2. Enable TTA: augment=True in predict() (5 min)
3. YOLOv8l with imgsz 1280 (8h training)
4. Replace ResNet18 with CLIP (1-2 days coding)

After implementing each phase, update this file with
actual results vs expected.
