# NorgesGruppen — Object Detection

Data ligger her som **`train/annotations.json`** + **`train/images/`** (samme layout som i COCO-zip etter utpakking).

## Rask start (dette repoet)

### 1. Avhengigheter (kun lokal trening)

```bash
cd nmai2026/norgesgruppen
pip install -r requirements-train.txt
```

**PyTorch-versjon:** Lokalt kan du ha f.eks. **2.8.x** (`python3 -c "import torch; print(torch.__version__)"`). Konkurransesandbox har **2.6.0+cu124**. Fra **2.6** endret `torch.load` standard **`weights_only`**, som kan knekke **Ultralytics**-vekter; **`run.py`** og **`train.py`** bruker **`add_safe_globals([DetectionModel])`** og en liten **`torch.load`-wrapper** (setter **`weights_only=False`** når det ikke er oppgitt) før **`YOLO()`**. Trener du med annen torch og vil minimere risiko på submit: egen venv med **2.6.0** / **0.21.0** (se `requirements-train.txt`).

### 2. Røyktest inferens (uten `model.pt`)

Bruker nedlastet **YOLOv8n** første gang (krever nett lokalt). Sandbox bruker din **`model.pt`**.

```bash
cd nmai2026/norgesgruppen
python3 run.py --input train/images --output /tmp/predictions.json
```

### 3. Tren modell

Fra **`norgesgruppen/`** pek **`--data`** på **`train`** (eller full sti til mappen som har `annotations.json` + `images/`):

```bash
cd nmai2026/norgesgruppen
python3 train.py --data train --model n --epochs 100
```

Offisiell zip-struktur med ekstra **`train/`**-nivå støttes også:

```bash
python3 train.py --data /path/to/NM_NGD_coco_dataset --model m --epochs 50
```

Det genereres **`yolo_dataset/`** ved siden av **`train/`** (konvertert COCO → YOLO). Kjør `train.py` fra samme katalog hvert gang, så havner **`runs/detect/...`** forutsigbart.

### 4. Legg vekter klare for innsending

```bash
cp runs/detect/ng_model/weights/best.pt model.pt
```

### 5. ZIP til konkurransen

Kun filer som trengs i sandbox (spar plass — dropp gjerne `train.py` hvis du vil):

```bash
cd nmai2026/norgesgruppen
zip -r ../submission.zip run.py model.pt -x ".*" "__MACOSX/*"
# valgfritt: legg til train.py om du vil -- tar én av max 10 .py-filer
```

Verifiser:

```bash
unzip -l ../submission.zip | head -10
# Skal liste run.py på toppnivå — ikke norgesgruppen/run.py
```

## Kartlegging COCO → YOLO

| Kilde (repo) | `--data` |
| --- | --- |
| `norgesgruppen/train/{annotations.json,images/}` | `train` |

## ZIP til organisatorer (krav)

```
submission.zip
├── run.py       # påkrevd
├── model.pt     # anbefalt (≤ grenser i knowledge/norgesgruppen.md)
└── …            # valgfritt
```

## Sandbox (kort)

- Python **3.11**, **ultralytics 8.1.0**, GPU **L4**, **ingen nett**, **300 s**.
- I **`run.py`**: ikke bruk `os`, `subprocess`, `yaml`, … — bruk **`pathlib`** og **`json`** (som nå).

## Scoring (kort)

- **70%** detection mAP @ IoU≥0.5 (klasse ignoreres)
- **30%** classification @ IoU≥0.5 og riktig **`category_id`** (**0–355**)
- Kun deteksjon: sett **`category_id": 0`** overalt → opptil **0.70** totalt

Mer detaljer: **`../knowledge/norgesgruppen.md`**.

## `run.py` i konkurransen

```bash
python3 run.py --input /data/images --output /output/predictions.json
```

Utdata: JSON-liste med **`image_id`**, **`category_id`**, **`bbox`** `[x,y,w,h]`, **`score`**.
