# NorgesGruppen Data — Examples

**URI:** `challenge://norgesgruppen-data/examples`  
**Source:** NM i AI docs via **nmiai** MCP — tool `search_docs` (full `read_doc` not exposed). Below: merged excerpts whose text includes this URI.

---

## challenge://norgesgruppen-data/examples


Using YOLOv8n with GPU auto-detection. **Important:** The pretrained COCO model outputs COCO class IDs (0-79), not product IDs (0-355). For correct product classification, fine-tune on the competition training data with `nc=357`. Detection-only submissions (wrong category_ids) still score up to 70%.

```python

========

---

## challenge://norgesgruppen-data/examples


Minimal `run.py` that generates random predictions (use to verify your setup):

```python

---


**Inference (in your `run.py`):**

```python

---

|---|---|
| `run.py not found at zip root` | Zip the **contents**, not the folder. See "Creating Your Zip" in submission docs. |
| `Disallowed file type: __MACOSX/...` | macOS Finder resource forks. Use terminal: `zip -r ../sub.zip . -x ".*" "__MACOSX/*"` |
| `Disallowed file type: .bin` | Rename `.bin` → `.pt` (same format) or convert to `.safetensors` |

========

---

## challenge://norgesgruppen-data/examples


Using YOLOv8n with GPU auto-detection. **Important:** The pretrained COCO model outputs COCO class IDs (0-79), not product IDs (0-355). For correct product classification, fine-tune on the competition training data with `nc=357`. Detection-only submissions (wrong category_ids) still score up to 70%.

```python

---


Include `yolov8n.pt` in your zip. This pretrained COCO model serves as a baseline — fine-tune on the competition training data for better results. With GPU available, larger models like YOLOv8m/l/x are also feasible within the timeout.

## ONNX Inference Example

---


ONNX works with any model framework. Use `CUDAExecutionProvider` for GPU acceleration:

**Export (on your training machine):**

========

---

## challenge://norgesgruppen-data/examples


    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = YOLO("yolov8n.pt")
    predictions = []

---


    session = ort.InferenceSession("model.onnx", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    predictions = []

---

- **GPU is available** — larger models (YOLOv8m/l/x, custom transformers) are feasible within the 300s timeout
- Use `torch.cuda.is_available()` to write code that works both locally (CPU) and on the server (GPU)
- FP16 quantization is recommended — smaller weights, faster GPU inference
- ONNX with `CUDAExecutionProvider` gives good GPU performance for any framework

========

---

## challenge://norgesgruppen-data/examples

                "category_id": random.randint(0, 356),
                "bbox": [
                    round(random.uniform(0, 1500), 1),
                    round(random.uniform(0, 800), 1),

---

                    "category_id": int(r.boxes.cls[i].item()),
                    "bbox": [round(x1, 1), round(y1, 1), round(x2 - x1, 1), round(y2 - y1, 1)],
                    "score": round(float(r.boxes.conf[i].item()), 3),
                })

========

---

## challenge://norgesgruppen-data/examples


## Random Baseline

Minimal `run.py` that generates random predictions (use to verify your setup):

---


- Start with the random baseline to verify your setup works
- **GPU is available** — larger models (YOLOv8m/l/x, custom transformers) are feasible within the 300s timeout
- Use `torch.cuda.is_available()` to write code that works both locally (CPU) and on the server (GPU)

---

## challenge://norgesgruppen-data/examples

|---|---|
| `run.py not found at zip root` | Zip the **contents**, not the folder. See "Creating Your Zip" in submission docs. |
| `Disallowed file type: __MACOSX/...` | macOS Finder resource forks. Use terminal: `zip -r ../sub.zip . -x ".*" "__MACOSX/*"` |
| `Disallowed file type: .bin` | Rename `.bin` → `.pt` (same format) or convert to `.safetensors` |

========

---

## challenge://norgesgruppen-data/examples

| `Security scan found violations` | Remove imports of subprocess, socket, os, etc. Use pathlib instead. |
| `No predictions.json in output` | Make sure run.py writes to the `--output` path |
| `Timed out after 300s` | Ensure GPU is used (`model.to("cuda")`), or use a smaller model |
| `Exit code 137` | Out of memory (8 GB limit). Reduce batch size or use FP16 |

========

---

## challenge://norgesgruppen-data/examples

| `Disallowed file type: .bin` | Rename `.bin` → `.pt` (same format) or convert to `.safetensors` |
| `Security scan found violations` | Remove imports of subprocess, socket, os, etc. Use pathlib instead. |
| `No predictions.json in output` | Make sure run.py writes to the `--output` path |
| `Timed out after 300s` | Ensure GPU is used (`model.to("cuda")`), or use a smaller model |

---

## challenge://norgesgruppen-data/examples

| `No predictions.json in output` | Make sure run.py writes to the `--output` path |
| `Timed out after 300s` | Ensure GPU is used (`model.to("cuda")`), or use a smaller model |
| `Exit code 137` | Out of memory (8 GB limit). Reduce batch size or use FP16 |
| `Exit code 139` | Segfault — likely model weight version mismatch. Re-export with matching package version or use ONNX. |