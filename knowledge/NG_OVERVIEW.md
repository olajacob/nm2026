# NorgesGruppen Data — Overview

**URI:** `challenge://norgesgruppen-data/overview`  
**Source:** NM i AI docs via **nmiai** MCP — tool `search_docs` (full `read_doc` not exposed). Below: merged excerpts whose text includes this URI.

---

## challenge://norgesgruppen-data/overview

6. Our server runs your code in a sandbox with GPU (NVIDIA L4, 24 GB VRAM) — no network access
7. Your predictions are scored: **70% detection** (did you find products?) + **30% classification** (did you identify the right product?)
8. Score appears on the leaderboard


========

---

## challenge://norgesgruppen-data/overview

2. Train your object detection model locally
3. Write a `run.py` that takes shelf images as input and outputs predictions
4. Zip your code + model weights
5. Upload at the submit page

========

---

## challenge://norgesgruppen-data/overview

5. Upload at the submit page
6. Our server runs your code in a sandbox with GPU (NVIDIA L4, 24 GB VRAM) — no network access
7. Your predictions are scored: **70% detection** (did you find products?) + **30% classification** (did you identify the right product?)
8. Score appears on the leaderboard

========

---

## challenge://norgesgruppen-data/overview

      "category_id": 42,
      "bbox": [141, 49, 169, 152],
      "area": 25688,
      "iscrowd": 0,

---


Key fields: `bbox` is `[x, y, width, height]` in pixels (COCO format). `product_code` is the barcode. `corrected` indicates manually verified annotations.

## What Annotations Look Like

========

---

## challenge://norgesgruppen-data/overview

# NorgesGruppen Data: Object Detection

Detect grocery products on store shelves. Upload your model code as a `.zip` file — it runs in a sandboxed Docker container on our servers.

========