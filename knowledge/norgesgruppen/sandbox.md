# NorgesGruppen — sandbox & submission

**Router:** [`../INDEX.md`](../INDEX.md) · **Training / submissions:** [`model.md`](model.md) · **Long-form:** [`../norgesgruppen.md`](../norgesgruppen.md)

## Environment (organiser sandbox)

| Item | Value |
|------|--------|
| Python | **3.11** |
| GPU | **NVIDIA L4**, **24 GB** VRAM |
| CUDA | **12.4** |
| torch | **2.6.0** |
| torchvision | **0.21.0** |
| ultralytics | **8.1.0** |
| Network | **None** |
| Timeout | **300 s** |

**Invocation:** `python3 run.py --input /data/images --output /output/predictions.json`

## Blocked imports

Using these (directly or indirectly in ways the scanner catches) can cause **silent failure** or **crash**:

`os`, `sys`, `subprocess`, `socket`, `ctypes`, `builtins`, `importlib`, `pickle`, `marshal`, `shelve`, `shutil`, `yaml`, `requests`, `urllib`, `http.client`, `multiprocessing`, `threading`, `signal`, `gc`

## Blocked calls

`eval`, `exec`, `compile`

## Safe alternatives

- **`pathlib`** instead of **`os`** for paths.
- **`json`** instead of **`yaml`** / **`pickle`** for config and embedding stores.
- **`product_embeddings.json`** + **`json.load`** instead of **`np.save` / `np.load(..., allow_pickle=True)`** for embedding dicts.

## ZIP rules

- **`run.py` MUST be at zip root** — not `norgesgruppen/run.py` or any subfolder-only layout.
- **No** `.DS_Store`, `__MACOSX`, or stray **hidden** files — use **`-x`** exclusions below.
- **Max 3** weight files (`.pt`, `.pth`, `.onnx`, `.safetensors`, `.npy`) — **420 MB** total weights / uncompressed zip budget per organiser table.
- **Max 1000** files total in zip.
- **Max 10** `.py` files.
- **Allowed extensions:** `.py`, `.json`, `.yaml`, `.yml`, `.cfg`, `.pt`, `.pth`, `.onnx`, `.safetensors`, `.npy`  
  - **`.bin`:** not allowed as-is — rename to **`.pt`** or use **`.safetensors`**.

**Do not** bundle **`NM_NGD_product_images/``** if the platform rejects it (“disallowed files”); use **`product_embeddings.json`** + bundled ResNet weights instead.

## ZIP command (verified working)

From repo (adjust path if your cwd differs):

```bash
cd nmai2026/norgesgruppen
zip -r ../submission.zip run.py model.pt resnet18-f37072fd.pth \
  product_embeddings.json train/annotations.json metadata.json \
  -x ".*" "__MACOSX/*" "*/__pycache__/*"
```

## Verify ZIP before submitting

```bash
unzip -l ../submission.zip | head -10
# run.py must appear at root, NOT norgesgruppen/run.py
```

## Check for blocked content (local)

```bash
grep -n "^import\|^from" run.py
grep -n "allow_pickle\|pickle\|import os\|import sys" run.py
```

## Submission limits (platform)

- **Max 3** submissions **per day** for NorgesGruppen (confirm on current rules UI).
- **Best** score is typically **kept** (not last-only).
- **Deadline (snapshot):** **22. mars 2026 kl. 15:00 CET** — confirm on competition site.

## Known issues

- **`torch.load`** for **`.pt` / `.pth`** uses pickle internally, but **`torch.serialization`** is **not** blocked the same way — YOLO weights usually **load**.
- **`np.load(..., allow_pickle=True)`** **is** blocked — use **`product_embeddings.json`**.
- **`metadata.json`** must be at **ZIP root** (or next to `run.py` in flat layout); do **not** rely only on `NM_NGD_product_images/metadata.json` inside a disallowed tree.
- **`resnet18-f37072fd.pth`** must be **bundled** — no download in sandbox.

## File layout that works in sandbox

```
run.py                    ← at zip root
model.pt                  ← YOLO weights
resnet18-f37072fd.pth     ← ResNet18 (bundled)
product_embeddings.json   ← pre-computed embeddings (JSON, no pickle)
train/annotations.json    ← COCO annotations for category mapping
metadata.json             ← product metadata at ROOT
```

## `run.py` style

Prefer **`pathlib`** + **`json`**; avoid importing blocked modules even if “sometimes works” locally.
