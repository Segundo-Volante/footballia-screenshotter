"""
Dataset Exporter — exports captured and classified frames to standard ML formats.

Supported formats:
1. COCO JSON — classification annotations compatible with COCO API
2. ImageNet-style — one folder per category (already the default output, but this creates a clean copy)
3. CSV — flat metadata file for data analysis (Pandas/R friendly)
4. HuggingFace Datasets — generates dataset_info.json + data/ for load_dataset()
"""
import csv
import json
import logging
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

from backend.match_db import MatchDB

logger = logging.getLogger(__name__)


class DatasetExporter:

    def __init__(self, db: Optional[MatchDB] = None):
        self._db = db or MatchDB()

    def export_coco(self, output_path: str, capture_ids: list[int] = None,
                    match_ids: list[int] = None) -> str:
        """
        Export as COCO-format JSON with classification annotations.

        Output:
        {
            "images": [{id, file_name, width, height, ...}],
            "annotations": [{id, image_id, category_id, ...}],
            "categories": [{id, name, supercategory}]
        }
        """
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        images_dir = out / "images"
        images_dir.mkdir(exist_ok=True)

        frames = self._get_frames(capture_ids, match_ids)
        categories = self._get_category_map(frames)

        coco = {
            "info": {
                "description": "Footballia Screenshotter Export",
                "version": "1.0",
                "date_created": datetime.now().isoformat(),
            },
            "images": [],
            "annotations": [],
            "categories": [
                {"id": cid, "name": name, "supercategory": "broadcast"}
                for name, cid in categories.items()
            ],
        }

        for i, frame in enumerate(frames):
            src = Path(frame["filepath"])
            if not src.exists():
                continue

            # Copy image
            dest = images_dir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)

            image_entry = {
                "id": i + 1,
                "file_name": src.name,
                "width": 1280,  # Default; could read actual dimensions
                "height": 720,
            }
            coco["images"].append(image_entry)

            ann_entry = {
                "id": i + 1,
                "image_id": i + 1,
                "category_id": categories.get(frame["camera_type"], 0),
                "attributes": {
                    "confidence": frame.get("confidence", 0),
                    "video_time": frame.get("video_time", 0),
                    "is_reviewed": bool(frame.get("is_reviewed", False)),
                },
            }
            coco["annotations"].append(ann_entry)

        ann_path = out / "annotations.json"
        ann_path.write_text(json.dumps(coco, indent=2), encoding="utf-8")
        logger.info(f"COCO export: {len(coco['images'])} images → {out}")
        return str(out)

    def export_imagenet(self, output_path: str, capture_ids: list[int] = None,
                        match_ids: list[int] = None) -> str:
        """
        Export as ImageNet-style directory: one folder per category.
        This is essentially a clean copy of the recordings output.
        """
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)

        frames = self._get_frames(capture_ids, match_ids)
        count = 0
        for frame in frames:
            src = Path(frame["filepath"])
            if not src.exists():
                continue
            cat = frame.get("camera_type", "OTHER")
            cat_dir = out / cat
            cat_dir.mkdir(exist_ok=True)
            dest = cat_dir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)
                count += 1

        logger.info(f"ImageNet export: {count} images → {out}")
        return str(out)

    def export_csv(self, output_path: str, capture_ids: list[int] = None,
                   match_ids: list[int] = None) -> str:
        """
        Export as flat CSV file — one row per frame.
        Columns: filename, filepath, match_id, video_time, video_part,
                 camera_type, confidence, is_reviewed, was_corrected
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        frames = self._get_frames(capture_ids, match_ids)

        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "filename", "filepath", "capture_id", "match_id",
                "video_time", "video_part", "camera_type", "confidence",
                "is_reviewed", "was_corrected", "anomaly",
            ])
            for frame in frames:
                was_corrected = (
                    frame.get("reviewed_type", "") != ""
                    and frame.get("reviewed_type") != frame.get("camera_type")
                )
                # Get match_id from capture
                match_id = ""
                if frame.get("capture_id"):
                    cap = self._db.conn.execute(
                        "SELECT match_id FROM captures WHERE id = ?",
                        (frame["capture_id"],),
                    ).fetchone()
                    if cap:
                        match_id = cap[0] or ""

                writer.writerow([
                    frame.get("filename", ""),
                    frame.get("filepath", ""),
                    frame.get("capture_id", ""),
                    match_id,
                    round(frame.get("video_time", 0), 2),
                    frame.get("video_part", 1),
                    frame.get("camera_type", ""),
                    round(frame.get("confidence", 0), 4),
                    int(bool(frame.get("is_reviewed", False))),
                    int(was_corrected),
                    int(bool(frame.get("anomaly", False))),
                ])

        logger.info(f"CSV export: {len(frames)} rows → {out}")
        return str(out)

    def export_huggingface(self, output_path: str, capture_ids: list[int] = None,
                           match_ids: list[int] = None) -> str:
        """
        Export as HuggingFace Datasets format.
        Creates dataset_info.json + data/ with images and metadata.jsonl.
        Can be loaded with: datasets.load_dataset("imagefolder", data_dir="path")
        """
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        data_dir = out / "data"
        data_dir.mkdir(exist_ok=True)

        frames = self._get_frames(capture_ids, match_ids)
        categories = self._get_category_map(frames)

        metadata_lines = []
        count = 0
        for frame in frames:
            src = Path(frame["filepath"])
            if not src.exists():
                continue
            dest = data_dir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)
                count += 1

            metadata_lines.append(json.dumps({
                "file_name": src.name,
                "label": frame.get("camera_type", "OTHER"),
                "confidence": round(frame.get("confidence", 0), 4),
                "video_time": round(frame.get("video_time", 0), 2),
            }))

        # Write metadata.jsonl
        (data_dir / "metadata.jsonl").write_text(
            "\n".join(metadata_lines) + "\n", encoding="utf-8"
        )

        # Write dataset_info.json
        info = {
            "description": "Football broadcast frame classification dataset",
            "features": {
                "image": {"_type": "Image"},
                "label": {
                    "_type": "ClassLabel",
                    "names": list(categories.keys()),
                },
                "confidence": {"_type": "Value", "dtype": "float32"},
                "video_time": {"_type": "Value", "dtype": "float32"},
            },
            "splits": {
                "train": {"num_examples": count},
            },
        }
        (out / "dataset_info.json").write_text(
            json.dumps(info, indent=2), encoding="utf-8"
        )

        logger.info(f"HuggingFace export: {count} images → {out}")
        return str(out)

    def _get_frames(self, capture_ids: list[int] = None,
                    match_ids: list[int] = None) -> list[dict]:
        """Fetch frames from the database with optional filters."""
        if capture_ids:
            placeholders = ",".join("?" * len(capture_ids))
            rows = self._db.conn.execute(
                f"SELECT * FROM frames WHERE capture_id IN ({placeholders}) ORDER BY video_time",
                capture_ids,
            ).fetchall()
        elif match_ids:
            placeholders = ",".join("?" * len(match_ids))
            rows = self._db.conn.execute(
                f"""SELECT f.* FROM frames f
                    JOIN captures c ON c.id = f.capture_id
                    WHERE c.match_id IN ({placeholders})
                    ORDER BY f.video_time""",
                match_ids,
            ).fetchall()
        else:
            rows = self._db.conn.execute("SELECT * FROM frames ORDER BY video_time").fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _get_category_map(frames: list[dict]) -> dict[str, int]:
        cats = sorted(set(f.get("camera_type", "OTHER") for f in frames))
        return {name: i + 1 for i, name in enumerate(cats)}

    def close(self):
        self._db.close()
