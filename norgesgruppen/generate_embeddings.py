#!/usr/bin/env python3
"""Pre-compute per-product ResNet18 embeddings for NM_NGD_product_images (JSON bundle for sandbox — no pickle)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torchvision import models, transforms

RESNET18_LOCAL_WEIGHTS = "resnet18-f37072fd.pth"
IMAGE_PRIORITY = ("main.jpg", "front.jpg", "back.jpg", "left.jpg", "right.jpg")
OUTPUT_NAME = "product_embeddings.json"


def _make_resnet18_embedder(device: torch.device, model_dir: Path) -> tuple[nn.Module, transforms.Compose]:
    local = model_dir / RESNET18_LOCAL_WEIGHTS
    if not local.is_file():
        raise FileNotFoundError(f"Missing {local} — place resnet18-f37072fd.pth next to this script.")
    print(f"Loading ResNet18 from local: {local}")
    net = models.resnet18(weights=None)
    net.load_state_dict(torch.load(str(local), map_location="cpu", weights_only=True))
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
def _embed_image(net: nn.Module, tfm: transforms.Compose, image_path: Path, device: torch.device) -> np.ndarray:
    pil = Image.open(image_path).convert("RGB")
    t = tfm(pil).unsqueeze(0).to(device)
    e = net(t).float().squeeze(0)
    e = e / (e.norm(p=2) + 1e-8)
    return e.cpu().numpy().astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build product_embeddings.json from NM_NGD_product_images/")
    parser.add_argument(
        "--images-root",
        type=Path,
        default=None,
        help="Folder containing product subfolders (default: <script_dir>/NM_NGD_product_images)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Output JSON path (default: <script_dir>/{OUTPUT_NAME})",
    )
    args = parser.parse_args()

    model_dir = Path(__file__).resolve().parent
    ref_root = args.images_root or (model_dir / "NM_NGD_product_images")
    out_path = args.output or (model_dir / OUTPUT_NAME)

    if not ref_root.is_dir():
        raise SystemExit(f"Images root not found: {ref_root}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    net, tfm = _make_resnet18_embedder(device, model_dir)

    embeddings_dict: dict[str, np.ndarray] = {}

    for folder in sorted(ref_root.iterdir()):
        if not folder.is_dir():
            continue
        code = folder.name
        chosen: Path | None = None
        for fname in IMAGE_PRIORITY:
            p = folder / fname
            if p.is_file():
                chosen = p
                break
        if chosen is None:
            continue
        try:
            emb = _embed_image(net, tfm, chosen, device)
            embeddings_dict[code] = emb
        except Exception as e:
            print(f"  skip {code}: {e}")

    embeddings_json = {k: v.tolist() for k, v in embeddings_dict.items()}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(embeddings_json, f)
    print(f"Saved {len(embeddings_json)} embeddings to {out_path}")


if __name__ == "__main__":
    main()
