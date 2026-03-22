"""Inference entry (sandbox + local). Usage: python3 run.py --input ... --output ..."""
from __future__ import annotations

import argparse
import json
from difflib import get_close_matches
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torchvision import models, transforms
from ultralytics.nn.tasks import DetectionModel

# PyTorch 2.6+: default torch.load(weights_only=True) breaks Ultralytics checkpoints.
torch.serialization.add_safe_globals([DetectionModel])
_tl = torch.load


def _torch_load_compat(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _tl(*args, **kwargs)


torch.load = _torch_load_compat

from ultralytics import YOLO

# --- Embedding retrieval (ResNet18 ImageNet, no fine-tuning) ---
RESNET18_LOCAL_WEIGHTS = "resnet18-f37072fd.pth"
PRODUCT_EMBEDDINGS_JSON = "product_embeddings.json"
SIM_THRESHOLD = 0.3
REF_IMAGE_PRIORITY = ("main.jpg", "front.jpg", "back.jpg", "left.jpg", "right.jpg", "top.jpg", "bottom.jpg")
MAX_REF_IMAGES_PER_PRODUCT = 3


def get_image_id(img_path: Path) -> int:
    return int(img_path.stem.split("_")[-1])


def _normalize_product_name(name: str) -> str:
    return " ".join(name.upper().split())


def _find_annotations_path(model_dir: Path) -> Path | None:
    for rel in (
        Path("train") / "annotations.json",
        Path("annotations.json"),
    ):
        p = model_dir / rel
        if p.is_file():
            return p
    return None


def _build_product_code_to_category(
    annotations_path: Path, metadata_path: Path
) -> dict[str, int]:
    """Map folder name (product_code) → COCO category_id (0–355)."""
    with open(annotations_path, encoding="utf-8") as f:
        coco = json.load(f)
    with open(metadata_path, encoding="utf-8") as f:
        meta = json.load(f)

    name_to_id: dict[str, int] = {}
    for c in coco["categories"]:
        cid = int(c["id"])
        if cid >= 356:
            continue
        name_to_id[_normalize_product_name(c["name"])] = cid

    all_names = list(name_to_id.keys())
    out: dict[str, int] = {}
    for p in meta.get("products", []):
        if not p.get("has_images"):
            continue
        code = str(p["product_code"])
        key = _normalize_product_name(p["product_name"])
        cid = name_to_id.get(key)
        if cid is None:
            matches = get_close_matches(key, all_names, n=1, cutoff=0.82)
            if matches:
                cid = name_to_id[matches[0]]
        if cid is not None:
            out[code] = cid
    return out


def _make_resnet18_embedder(device: torch.device, model_dir: Path) -> tuple[nn.Module, transforms.Compose]:
    local = model_dir / RESNET18_LOCAL_WEIGHTS
    if local.is_file():
        print(f"Loading ResNet18 from local: {local}")
        net = models.resnet18(weights=None)
        net.load_state_dict(
            torch.load(str(local), map_location="cpu", weights_only=True)
        )
    else:
        print("Downloading ResNet18 weights...")
        net = models.resnet18(weights=models.ResNet18_Weights.IMAGENET1K_V1)
    net.fc = nn.Identity()
    net.eval()
    net.to(device)
    tfm = transforms.Compose(
        [
            transforms.Resize(256, interpolation=transforms.InterpolationMode.BILINEAR),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ]
    )
    return net, tfm


@torch.no_grad()
def _embed_pil(net: nn.Module, tfm: transforms.Compose, pil: Image.Image, device: torch.device) -> torch.Tensor:
    t = tfm(pil.convert("RGB")).unsqueeze(0).to(device)
    if device.type == "cuda":
        with torch.amp.autocast("cuda", dtype=torch.float16):
            e = net(t)
    else:
        e = net(t)
    e = e.float().squeeze(0)
    e = e / (e.norm(p=2) + 1e-8)
    return e


def _build_reference_index(
    ref_root: Path,
    code_to_cat: dict[str, int],
    net: nn.Module,
    tfm: transforms.Compose,
    device: torch.device,
) -> tuple[torch.Tensor, torch.Tensor] | None:
    """Returns (embeddings [N,D], category_ids [N]) or None if nothing loaded."""
    rows: list[torch.Tensor] = []
    cats: list[int] = []
    for code, cid in sorted(code_to_cat.items()):
        folder = ref_root / code
        if not folder.is_dir():
            continue
        chosen: list[Path] = []
        for fname in REF_IMAGE_PRIORITY:
            p = folder / fname
            if p.is_file():
                chosen.append(p)
                if len(chosen) >= MAX_REF_IMAGES_PER_PRODUCT:
                    break
        if not chosen:
            for p in sorted(folder.iterdir()):
                if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    chosen.append(p)
                    if len(chosen) >= MAX_REF_IMAGES_PER_PRODUCT:
                        break
        for img_path in chosen:
            try:
                pil = Image.open(img_path)
                e = _embed_pil(net, tfm, pil, device)
                rows.append(e.cpu())
                cats.append(cid)
            except Exception:
                continue
    if not rows:
        return None
    emb = torch.stack(rows, dim=0)
    labels = torch.tensor(cats, dtype=torch.long)
    return emb, labels


def _load_precomputed_embeddings(
    model_dir: Path, code_to_cat: dict[str, int]
) -> tuple[torch.Tensor, torch.Tensor] | None:
    """Load {product_code: 512-d vector} from product_embeddings.json (no pickle — sandbox-safe)."""
    path = model_dir / PRODUCT_EMBEDDINGS_JSON
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return None
    data = {str(k): v for k, v in raw.items()}
    rows: list[torch.Tensor] = []
    cats: list[int] = []
    for code, cid in sorted(code_to_cat.items()):
        if code not in data:
            continue
        arr = np.asarray(data[code], dtype=np.float32).reshape(-1)
        if arr.size == 0:
            continue
        arr = arr / (float(np.linalg.norm(arr)) + 1e-8)
        rows.append(torch.from_numpy(arr))
        cats.append(cid)
    if not rows:
        return None
    emb = torch.stack(rows, dim=0)
    labels = torch.tensor(cats, dtype=torch.long)
    return emb, labels


@torch.no_grad()
def _nearest_category(
    query: torch.Tensor,
    ref_emb: torch.Tensor,
    ref_labels: torch.Tensor,
    device: torch.device,
) -> tuple[int, float]:
    """Cosine similarity — ref rows and query are L2-normalized."""
    ref = ref_emb.to(device)
    q = query.to(device)
    sims = ref @ q
    best, idx = sims.max(dim=0)
    return int(ref_labels[idx].item()), float(best.item())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--sim-threshold",
        type=float,
        default=SIM_THRESHOLD,
        help="If max cosine sim to reference library is below this, keep YOLO class id",
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_path = Path(args.output)
    model_dir = Path(__file__).resolve().parent
    sim_threshold = float(args.sim_threshold)

    device_s = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(device_s)
    print(f"Device: {device_s}")

    weight_path = model_dir / "model.pt"
    if weight_path.exists():
        print(f"Loading fine-tuned weights: {weight_path}")
        model = YOLO(str(weight_path))
    else:
        print("No model.pt found — using yolov8n.pt pretrained baseline")
        model = YOLO("yolov8n.pt")

    # --- Optional: reference embedding library ---
    ref_root = model_dir / "NM_NGD_product_images"
    meta_path = ref_root / "metadata.json"
    if not meta_path.is_file():
        _meta_fallback = model_dir / "metadata.json"
        if _meta_fallback.is_file():
            meta_path = _meta_fallback
    ann_path = _find_annotations_path(model_dir)
    ref_emb_cpu: torch.Tensor | None = None
    ref_labels_cpu: torch.Tensor | None = None
    embedder: nn.Module | None = None
    tfm: transforms.Compose | None = None

    if ann_path and meta_path.is_file():
        try:
            code_to_cat = _build_product_code_to_category(ann_path, meta_path)
            print(f"Product code → category map: {len(code_to_cat)} entries (from metadata + COCO names)")

            precomputed = _load_precomputed_embeddings(model_dir, code_to_cat)
            if precomputed is not None:
                ref_emb_cpu, ref_labels_cpu = precomputed[0].cpu(), precomputed[1].cpu()
                print(
                    f"Loaded {ref_emb_cpu.shape[0]} pre-computed embeddings from {PRODUCT_EMBEDDINGS_JSON}"
                )
                embedder, tfm = _make_resnet18_embedder(device, model_dir)
            elif ref_root.is_dir():
                print("Building embeddings from images")
                embedder, tfm = _make_resnet18_embedder(device, model_dir)
                built = _build_reference_index(ref_root, code_to_cat, embedder, tfm, device)
                if built is not None:
                    ref_emb_cpu, ref_labels_cpu = built[0].cpu(), built[1].cpu()
                    print(
                        f"Reference embeddings: {ref_emb_cpu.shape[0]} vectors, dim={ref_emb_cpu.shape[1]}"
                    )
                else:
                    print("No reference images loaded — classification from embeddings disabled")
                    embedder, tfm = None, None
            else:
                print(
                    "No product_embeddings.json and no NM_NGD_product_images/ — "
                    "classification from embeddings disabled (YOLO only)"
                )
        except Exception as e:
            print(f"Embedding library setup failed ({e}) — using YOLO class ids only")
            embedder, tfm = None, None
            ref_emb_cpu, ref_labels_cpu = None, None
    else:
        print(
            "Skipping embedding library (need train/annotations.json or annotations.json, "
            "and metadata.json under NM_NGD_product_images/ or next to run.py)"
        )

    image_files = sorted(
        p
        for p in input_dir.iterdir()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )
    print(f"Found {len(image_files)} images")

    predictions = []

    with torch.no_grad():
        for i, img_path in enumerate(image_files):
            image_id = get_image_id(img_path)
            try:
                pil_full = Image.open(img_path)
                w_img, h_img = pil_full.size
                results = model(
                    str(img_path), device=device_s, verbose=False, conf=0.25
                )
                for r in results:
                    if r.boxes is None:
                        continue
                    for j in range(len(r.boxes)):
                        x1, y1, x2, y2 = r.boxes.xyxy[j].tolist()
                        yolo_cls = int(r.boxes.cls[j].item())
                        conf = float(r.boxes.conf[j].item())
                        cat_id = yolo_cls
                        if (
                            embedder is not None
                            and tfm is not None
                            and ref_emb_cpu is not None
                            and ref_labels_cpu is not None
                        ):
                            ix1 = max(0, int(x1))
                            iy1 = max(0, int(y1))
                            ix2 = min(w_img, int(x2))
                            iy2 = min(h_img, int(y2))
                            if ix2 > ix1 and iy2 > iy1:
                                crop = pil_full.crop((ix1, iy1, ix2, iy2))
                                q = _embed_pil(embedder, tfm, crop, device)
                                nn_cat, nn_sim = _nearest_category(
                                    q, ref_emb_cpu, ref_labels_cpu, device
                                )
                                if nn_sim >= sim_threshold:
                                    cat_id = nn_cat
                        predictions.append(
                            {
                                "image_id": image_id,
                                "category_id": cat_id,
                                "bbox": [
                                    round(x1, 1),
                                    round(y1, 1),
                                    round(x2 - x1, 1),
                                    round(y2 - y1, 1),
                                ],
                                "score": round(conf, 3),
                            }
                        )
            except Exception as e:
                print(f"Error on {img_path.name}: {e}")

            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(image_files)} done")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(predictions, f)

    print(f"Done — {len(predictions)} predictions → {output_path}")


if __name__ == "__main__":
    main()
