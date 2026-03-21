"""Inference entry (sandbox + local). Usage: python3 run.py --input ... --output ..."""
import argparse
import json
from pathlib import Path

import torch
from ultralytics.nn.tasks import DetectionModel

# PyTorch 2.6+: default torch.load(weights_only=True) breaks Ultralytics checkpoints.
torch.serialization.add_safe_globals([DetectionModel])
_tl = torch.load

def _torch_load_compat(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _tl(*args, **kwargs)

torch.load = _torch_load_compat

from ultralytics import YOLO


def get_image_id(img_path: Path) -> int:
    return int(img_path.stem.split("_")[-1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_path = Path(args.output)
    model_dir = Path(__file__).parent

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")

    weight_path = model_dir / "model.pt"
    if weight_path.exists():
        print(f"Loading fine-tuned weights: {weight_path}")
        model = YOLO(str(weight_path))
    else:
        print("No model.pt found — using yolov8n.pt pretrained baseline")
        model = YOLO("yolov8n.pt")

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
                results = model(
                    str(img_path), device=device, verbose=False, conf=0.25
                )
                for r in results:
                    if r.boxes is None:
                        continue
                    for j in range(len(r.boxes)):
                        x1, y1, x2, y2 = r.boxes.xyxy[j].tolist()
                        predictions.append(
                            {
                                "image_id": image_id,
                                "category_id": int(r.boxes.cls[j].item()),
                                "bbox": [
                                    round(x1, 1),
                                    round(y1, 1),
                                    round(x2 - x1, 1),
                                    round(y2 - y1, 1),
                                ],
                                "score": round(float(r.boxes.conf[j].item()), 3),
                            }
                        )
            except Exception as e:
                print(f"Error on {img_path.name}: {e}")

            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(image_files)} done")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(predictions, f)

    print(f"Done — {len(predictions)} predictions → {output_path}")


if __name__ == "__main__":
    main()
