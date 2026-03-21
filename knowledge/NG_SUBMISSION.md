# NorgesGruppen Data — Submission

**URI:** `challenge://norgesgruppen-data/submission`  
**Source:** NM i AI docs via **nmiai** MCP — tool `search_docs` (full `read_doc` not exposed). Below: merged excerpts whose text includes this URI.

---

## challenge://norgesgruppen-data/submission


Your score combines detection and classification:

- **70% detection mAP** — did you find the products? (bounding box IoU ≥ 0.5, category ignored)

---

- **70% detection mAP** — did you find the products? (bounding box IoU ≥ 0.5, category ignored)
- **30% classification mAP** — did you identify the right product? (IoU ≥ 0.5 AND correct category_id)

Detection-only submissions (`category_id: 0` for all predictions) score up to 70%. Product identification adds the remaining 30%.

---

## challenge://norgesgruppen-data/submission


Your `.zip` must contain `run.py` at the root. You may include model weights and Python helper files.

```

---

submission.zip
├── run.py          # Required: entry point
├── model.onnx      # Optional: model weights (.pt, .onnx, .safetensors, .npy)
└── utils.py        # Optional: helper code

---


## run.py Contract

Your script is executed as:

---

## challenge://norgesgruppen-data/submission

| Memory | 8 GB |
| GPU | NVIDIA L4 (24 GB VRAM) |
| CUDA | 12.4 |
| Network | None (fully offline) |

---


### GPU

An NVIDIA L4 GPU is always available in the sandbox. Your code auto-detects it:

---


An NVIDIA L4 GPU is always available in the sandbox. Your code auto-detects it:

- `torch.cuda.is_available()` returns `True`

---

## challenge://norgesgruppen-data/submission

| Python | 3.11 |
| CPU | 4 vCPU |
| Memory | 8 GB |
| GPU | NVIDIA L4 (24 GB VRAM) |

---

- No opt-in flag needed — GPU is always on
- For ONNX models, use `["CUDAExecutionProvider", "CPUExecutionProvider"]` as the provider list

### Pre-installed Packages

---


The sandbox has a GPU (NVIDIA L4 with CUDA 12.4), so GPU-trained weights run natively — no `map_location="cpu"` needed. Your code should auto-detect with `torch.cuda.is_available()`.

**Train anywhere:** You can train on any hardware — your laptop CPU, a cloud GPU, Google Colab, GCP VMs, etc. Models trained on any platform will run on the sandbox GPU. Use `state_dict` saves (not full model saves) or ONNX export for maximum compatibility.

---

## challenge://norgesgruppen-data/submission

    "category_id": 0,
    "bbox": [120.5, 45.0, 80.0, 110.0],
    "score": 0.923
  }

---

| `category_id` | int | Product category ID (0-355). See `categories` list in annotations.json |
| `bbox` | [x, y, w, h] | Bounding box in COCO format |
| `score` | float | Confidence score (0-1) |

---

## challenge://norgesgruppen-data/submission


- **70% detection mAP** — did you find the products? (bounding box IoU ≥ 0.5, category ignored)
- **30% classification mAP** — did you identify the right product? (IoU ≥ 0.5 AND correct category_id)


---

- **70% detection mAP** — did you find the products? (bounding box IoU ≥ 0.5, category ignored)
- **30% classification mAP** — did you identify the right product? (IoU ≥ 0.5 AND correct category_id)

Detection-only submissions (`category_id: 0` for all predictions) score up to 70%. Product identification adds the remaining 30%.

---

## challenge://norgesgruppen-data/submission


## run.py Contract

Your script is executed as:

---

## challenge://norgesgruppen-data/submission

```bash
python3 run.py --input /data/images --output /output/predictions.json
```

---

## challenge://norgesgruppen-data/submission


## Creating Your Zip

`run.py` must be at the **root** of the zip — not inside a subfolder. This is the most common submission error.

---

## challenge://norgesgruppen-data/submission


## Zip Structure

Your `.zip` must contain `run.py` at the root. You may include model weights and Python helper files.