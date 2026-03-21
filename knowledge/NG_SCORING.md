# NorgesGruppen Data — Scoring

**URI:** `challenge://norgesgruppen-data/scoring`  
**Source:** NM i AI docs via **nmiai** MCP — tool `search_docs` (full `read_doc` not exposed). Below: merged excerpts whose text includes this URI.

---

## challenge://norgesgruppen-data/scoring


### Classification mAP (30% of score)

Measures whether you identified the correct product:

---

## challenge://norgesgruppen-data/scoring


Your final score combines detection and classification:

```

---

```
Score = 0.7 × detection_mAP + 0.3 × classification_mAP
```


---


### Classification mAP (30% of score)

Measures whether you identified the correct product:

========

---

## challenge://norgesgruppen-data/scoring


The public leaderboard shows scores from the public test set. The final ranking uses the private test set which is never revealed to participants.

## Select for Final Evaluation

---

## challenge://norgesgruppen-data/scoring


Both components use mAP@0.5 (Mean Average Precision at IoU threshold 0.5).

### Detection mAP (70% of score)

---

## challenge://norgesgruppen-data/scoring


Both components use mAP@0.5 (Mean Average Precision at IoU threshold 0.5).

### Detection mAP (70% of score)

---

- Each prediction is matched to the closest ground truth box
- A prediction is a true positive if IoU ≥ 0.5 (category is ignored)
- This rewards accurate bounding box localization


---


- A prediction is a true positive if IoU ≥ 0.5 AND the `category_id` matches the ground truth
- 356 product categories (IDs 0-355) from the training data `annotations.json`


========

---

## challenge://norgesgruppen-data/scoring

```
Score = 0.7 × detection_mAP + 0.3 × classification_mAP
```