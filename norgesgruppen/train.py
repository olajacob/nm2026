"""
NorgesGruppen — YOLOv8 training (local only, not in sandbox).

Dataset layouts supported (--data):
  1) Folder that contains annotations.json + images/  (this repo: .../norgesgruppen/train)
  2) Parent folder that contains train/annotations.json + train/images/  (unzipped NM_NGD_coco_dataset)

Requirements:
  pip install ultralytics==8.1.0

Usage (from norgesgruppen/):
  python3 train.py --data train --model n --epochs 100

Output:
  ./runs/detect/ng_model/weights/best.pt  → copy to ./model.pt for submission
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from pathlib import Path
import shutil

import torch
from ultralytics.nn.tasks import DetectionModel

torch.serialization.add_safe_globals([DetectionModel])
_tl = torch.load


def _torch_load_compat(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _tl(*args, **kwargs)


torch.load = _torch_load_compat

from ultralytics import YOLO


@contextmanager
def _ultralytics_skip_val_on_mps_cpu(device: str, force_val: bool):
    """Ultralytics calls validate() every final epoch and in final_eval(); DFL Half overflow on MPS/CPU."""
    if device == "cuda" or force_val:
        yield
        return

    import ultralytics.engine.trainer as tr

    _orig_validate = tr.BaseTrainer.validate
    _orig_final = tr.BaseTrainer.final_eval

    def _validate_stub(self):
        if self.tloss is None:
            fit = 0.0
        elif isinstance(self.tloss, torch.Tensor):
            fit = float(self.tloss.mean().item()) if self.tloss.numel() else 0.0
        else:
            fit = float(torch.as_tensor(self.tloss).float().mean().item())
        if not self.best_fitness or self.best_fitness < fit:
            self.best_fitness = fit
        return {}, fit

    def _final_stub(self) -> None:
        return None

    tr.BaseTrainer.validate = _validate_stub
    tr.BaseTrainer.final_eval = _final_stub
    try:
        yield
    finally:
        tr.BaseTrainer.validate = _orig_validate
        tr.BaseTrainer.final_eval = _orig_final


def resolve_coco_train_root(data: Path) -> Path:
    """Return directory containing annotations.json and images/."""
    data = data.expanduser().resolve()
    if (data / "annotations.json").is_file() and (data / "images").is_dir():
        return data
    nested = data / "train"
    if (nested / "annotations.json").is_file() and (nested / "images").is_dir():
        return nested
    raise FileNotFoundError(
        f"No COCO train folder found under: {data}\n"
        "Expected either:\n"
        "  • <path>/annotations.json and <path>/images/\n"
        "  • <path>/train/annotations.json and <path>/train/images/"
    )


def convert_coco_to_yolo(coco_train_root: Path, output_dir: Path) -> Path:
    """Convert COCO annotations under coco_train_root to YOLO layout in output_dir."""
    ann_path = coco_train_root / "annotations.json"
    images_src = coco_train_root / "images"

    output_dir.mkdir(parents=True, exist_ok=True)
    img_out = output_dir / "images" / "train"
    lbl_out = output_dir / "labels" / "train"
    img_out.mkdir(parents=True, exist_ok=True)
    lbl_out.mkdir(parents=True, exist_ok=True)

    with open(ann_path) as f:
        coco = json.load(f)

    images = {img["id"]: img for img in coco["images"]}
    ann_by_image: dict[int, list] = defaultdict(list)
    for ann in coco["annotations"]:
        ann_by_image[ann["image_id"]].append(ann)

    n_categories = len(coco["categories"])
    print(f"Categories: {n_categories}")
    print(f"Images in JSON: {len(images)}")
    print(f"Annotations: {len(coco['annotations'])}")

    missing = 0
    copied = 0
    for img_id, img_info in images.items():
        fname = img_info["file_name"]
        W = img_info["width"]
        H = img_info["height"]
        src = images_src / fname
        dst = img_out / fname
        if not src.is_file():
            missing += 1
            continue
        shutil.copy2(src, dst)
        copied += 1

        label_path = lbl_out / (Path(fname).stem + ".txt")
        with open(label_path, "w") as lf:
            for ann in ann_by_image[img_id]:
                x, y, w, h = ann["bbox"]
                cx = (x + w / 2) / W
                cy = (y + h / 2) / H
                nw = w / W
                nh = h / H
                cat_id = ann["category_id"]
                lf.write(f"{cat_id} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}\n")

    if missing:
        print(f"Warning: {missing} image(s) listed in JSON but missing on disk under {images_src}")

    print(f"Copied {copied} images → {img_out}")

    yaml_path = output_dir / "dataset.yaml"
    with open(yaml_path, "w") as f:
        f.write(f"path: {output_dir.as_posix()}\n")
        f.write("train: images/train\n")
        f.write("val: images/train\n")  # Ultralytics expects train + val; no held-out split yet
        f.write(f"nc: {n_categories}\n")
        f.write("names:\n")
        for cat in sorted(coco["categories"], key=lambda x: x["id"]):
            f.write(f"  - {json.dumps(cat['name'])}\n")

    dataset_json = output_dir / "dataset.json"
    with open(dataset_json, "w") as f:
        json.dump(
            {
                "path": str(output_dir.resolve()),
                "train": "images/train",
                "val": "images/train",
                "nc": n_categories,
                "names": [c["name"] for c in sorted(coco["categories"], key=lambda x: x["id"])],
            },
            f,
            indent=2,
        )

    print(f"YOLO dataset: {output_dir}")
    print(f"Ultralytics YAML: {yaml_path}")
    return yaml_path


def _training_device_and_batch(batch_override: int | None) -> tuple[str, int]:
    """Pick NVIDIA CUDA, Apple MPS, or CPU; set a sensible default batch when not overridden."""
    if torch.cuda.is_available():
        device = "cuda"
        default_batch = 16
    elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        device = "mps"
        default_batch = 8
    else:
        device = "cpu"
        default_batch = 4

    print(f"Device: {device}")
    b = batch_override if batch_override is not None else default_batch
    print(f"Batch: {b}")
    return device, b


def train(
    data_path: Path,
    model_size: str = "m",
    epochs: int = 100,
    imgsz: int = 640,
    batch: int | None = None,
    force_val: bool = False,
) -> Path:
    train_root = resolve_coco_train_root(data_path)
    yolo_dir = train_root.parent / "yolo_dataset"
    yaml_path = convert_coco_to_yolo(train_root, yolo_dir)

    device, batch = _training_device_and_batch(batch)

    model = YOLO(f"yolov8{model_size}.pt")
    # Ultralytics ties AMP to CUDA GradScaler — disable on MPS/CPU.
    amp = device == "cuda"
    workers = 0 if device in ("mps", "cpu") else 8
    use_yaml_val = (device == "cuda") or force_val
    skip_real_val = device != "cuda" and not force_val
    if skip_real_val:
        print(
            "Non-CUDA: Ultralytics validate/final_eval patched (DFL Half issue); fitness from train loss. "
            "Use --force-val for real mAP, or train on NVIDIA CUDA."
        )

    patch_ctx = (
        _ultralytics_skip_val_on_mps_cpu(device, force_val) if skip_real_val else nullcontext()
    )

    with patch_ctx:
        model.train(
            data=str(yaml_path),
            epochs=epochs,
            imgsz=imgsz,
            batch=batch,
            device=device,
            amp=amp,
            half=False,
            workers=workers,
            val=use_yaml_val,
            project="runs/detect",
            name="ng_model",
            patience=10,
            save=True,
            plots=True,
            mosaic=1.0,
            mixup=0.1,
            flipud=0.0,
            fliplr=0.5,
            degrees=5.0,
            translate=0.1,
            scale=0.5,
        )

    best = Path.cwd() / "runs/detect/ng_model/weights/best.pt"
    best = best.resolve()
    print("\nTraining complete.")
    print(f"Best weights: {best}")
    sub_dir = Path(__file__).resolve().parent
    print(f"Copy for zip: cp {best} {sub_dir / 'model.pt'}")
    return best


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare COCO → YOLO and train YOLOv8 (local).")
    parser.add_argument(
        "--data",
        required=True,
        help="Path to train/ (annotations.json + images/) or NM_NGD root containing train/",
    )
    parser.add_argument("--model", default="m", choices=["n", "s", "m", "l", "x"])
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument(
        "--batch",
        type=int,
        default=None,
        help="Override auto batch (16 CUDA / 8 MPS / 4 CPU)",
    )
    parser.add_argument(
        "--force-val",
        action="store_true",
        help="Run per-epoch validation on MPS/CPU anyway (may crash with many classes; default: val only on CUDA)",
    )
    args = parser.parse_args()

    train(
        Path(args.data),
        args.model,
        args.epochs,
        args.imgsz,
        args.batch,
        force_val=args.force_val,
    )
