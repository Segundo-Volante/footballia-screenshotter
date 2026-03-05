import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from backend.utils import logger

# Sequence metadata fields added to every frame record
SEQUENCE_FIELDS = [
    "camera_angle_source",
    "sequence_id",
    "sequence_type",
    "sequence_purpose",
    "sequence_position",
    "sequence_total_frames",
    "sequence_video_time_start",
    "sequence_video_time_end",
    "sequence_truncated",
    "sequence_preempted_by",
    "is_resample",
    "resample_of",
    "resample_original_interval",
]

# CSV columns: original + sequence subset (mirrors JSON for CSV analysis)
CSV_FIELDNAMES = [
    "filename", "classified_as", "confidence", "video_time",
    "video_part", "reasoning", "timestamp",
    "sequence_id", "sequence_type", "sequence_purpose",
    "sequence_position", "is_resample", "resample_of",
]


class OutputManager:
    def __init__(self, match: dict, base_dir: str, categories: list[str] | None = None):
        self.match = match
        self.categories = categories or []
        md = int(match.get("md", 0))
        opponent = match.get("opponent", "Unknown").replace(" ", "_")
        date = match.get("date", "unknown")
        self.folder_name = f"MD{md:02d}_{opponent}_{date}"
        self.base_path = Path(base_dir) / self.folder_name
        self._ensure_dirs()
        self.results: list[dict] = []
        self._load_existing_metadata()

    def _ensure_dirs(self):
        self.base_path.mkdir(parents=True, exist_ok=True)
        for cat in self.categories:
            (self.base_path / cat).mkdir(exist_ok=True)
        # Always create PENDING for manual mode
        (self.base_path / "PENDING").mkdir(exist_ok=True)
        logger.info(f"Output directory: {self.base_path}")

    def _load_existing_metadata(self):
        csv_path = self.base_path / "metadata.csv"
        if csv_path.exists():
            with open(csv_path, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self.results.append(row)
            logger.info(f"Loaded {len(self.results)} existing frames from metadata.csv")

    def get_existing_counts(self) -> dict:
        counts = {cat: 0 for cat in self.categories}
        for r in self.results:
            cam = r.get("classified_as") or r.get("camera_type", "OTHER")
            if cam in counts:
                counts[cam] += 1
        return counts

    async def save_frame(
        self,
        jpeg_bytes: bytes,
        video_time: float,
        classification: dict,
        video_part: int,
        sequence_meta: dict | None = None,
    ) -> Path:
        classified_as = classification.get("classified_as") or classification.get("camera_type", "OTHER")
        confidence = int(classification.get("confidence", 0) * 100)
        time_str = f"{video_time:08.2f}"

        filename = f"frame_{time_str}_{classified_as.lower()}_conf{confidence}.jpg"

        # Ensure category dir exists
        cat_dir = self.base_path / classified_as
        cat_dir.mkdir(parents=True, exist_ok=True)
        filepath = cat_dir / filename

        filepath.write_bytes(jpeg_bytes)

        # Build frame record with sequence metadata
        record = {
            "filename": filename,
            "classified_as": classified_as,
            "confidence": classification.get("confidence", 0),
            "video_time": video_time,
            "video_part": video_part,
            "reasoning": classification.get("reasoning", ""),
            "timestamp": datetime.now().isoformat(),
        }

        # Add sequence fields — null/false defaults for non-sequence frames
        if sequence_meta:
            record["camera_angle_source"] = sequence_meta.get("camera_angle_source", "trigger")
            record["sequence_id"] = sequence_meta.get("sequence_id")
            record["sequence_type"] = sequence_meta.get("sequence_type")
            record["sequence_purpose"] = sequence_meta.get("sequence_purpose")
            record["sequence_position"] = sequence_meta.get("sequence_position")
            # These are backfilled after sequence ends
            record["sequence_total_frames"] = sequence_meta.get("sequence_total_frames")
            record["sequence_video_time_start"] = sequence_meta.get("sequence_video_time_start")
            record["sequence_video_time_end"] = sequence_meta.get("sequence_video_time_end")
            record["sequence_truncated"] = sequence_meta.get("sequence_truncated", False)
            record["sequence_preempted_by"] = sequence_meta.get("sequence_preempted_by")
        else:
            record["camera_angle_source"] = "classifier"
            record["sequence_id"] = None
            record["sequence_type"] = None
            record["sequence_purpose"] = None
            record["sequence_position"] = None
            record["sequence_total_frames"] = None
            record["sequence_video_time_start"] = None
            record["sequence_video_time_end"] = None
            record["sequence_truncated"] = False
            record["sequence_preempted_by"] = None

        record["is_resample"] = sequence_meta.get("is_resample", False) if sequence_meta else False
        record["resample_of"] = sequence_meta.get("resample_of") if sequence_meta else None
        record["resample_original_interval"] = (
            sequence_meta.get("resample_original_interval") if sequence_meta else None
        )

        self.results.append(record)

        return filepath

    def save_frame_to_pending(self, jpeg_bytes: bytes, video_time: float) -> Path:
        """Save a frame to PENDING/ for manual classification later."""
        pending_dir = self.base_path / "PENDING"
        pending_dir.mkdir(parents=True, exist_ok=True)
        filename = f"frame_{video_time:08.2f}.jpg"
        filepath = pending_dir / filename
        filepath.write_bytes(jpeg_bytes)
        return filepath

    # ── Backfill ──

    def backfill_sequence(
        self,
        sequence_id: str,
        total_frames: int,
        video_time_end: float,
        truncated: bool = False,
        preempted_by: str | None = None,
    ):
        """
        After a sequence ends, update all frames in that sequence with
        final values for total_frames, video_time_end, truncated, preempted_by.
        """
        for record in self.results:
            if record.get("sequence_id") == sequence_id:
                record["sequence_total_frames"] = total_frames
                record["sequence_video_time_end"] = video_time_end
                record["sequence_truncated"] = truncated
                record["sequence_preempted_by"] = preempted_by

    # ── CSV ──

    def generate_metadata_csv(self):
        csv_path = self.base_path / "metadata.csv"
        if not self.results:
            return

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            for r in self.results:
                writer.writerow({k: r.get(k, "") for k in CSV_FIELDNAMES})

        logger.info(f"Wrote metadata.csv with {len(self.results)} entries")

    # ── summary.json ──

    def generate_summary_json(
        self,
        classifier=None,
        duration_seconds: float = 0.0,
        sequence_records: list[dict] | None = None,
    ):
        counts = {cat: 0 for cat in self.categories}
        for r in self.results:
            cam = r.get("classified_as") or r.get("camera_type", "OTHER")
            if cam in counts:
                counts[cam] += 1

        # Classifier is optional (ResampleRunner has no classifier)
        if classifier is not None:
            api_cost = round(classifier.get_cost(), 6)
            api_calls = classifier.get_call_count()
            provider = classifier.get_provider_name()
        else:
            api_cost = 0
            api_calls = 0
            provider = "none"

        summary = {
            "match": {
                "md": self.match.get("md"),
                "opponent": self.match.get("opponent"),
                "date": self.match.get("date"),
                "score": self.match.get("score"),
            },
            "capture": {
                "total_frames": len(self.results),
                "counts": counts,
                "duration_seconds": round(duration_seconds, 1),
                "api_cost": api_cost,
                "api_calls": api_calls,
                "provider": provider,
            },
            "output_dir": str(self.base_path),
            "generated_at": datetime.now().isoformat(),
        }

        # Add sequence_stats section
        if sequence_records:
            summary["sequence_stats"] = self._build_sequence_stats(sequence_records)

        json_path = self.base_path / "summary.json"
        with open(json_path, "w") as f:
            json.dump(summary, f, indent=2)

        logger.info(f"Wrote summary.json")

    def _build_sequence_stats(self, sequence_records: list[dict]) -> dict:
        """Build the sequence_stats section for summary.json."""
        by_type: dict[str, dict] = {}
        preempted_count = 0
        truncated_count = 0

        for rec in sequence_records:
            stype = rec.get("profile_name", "unknown")
            if stype not in by_type:
                by_type[stype] = {"count": 0, "total_frames": 0, "frame_counts": []}
            by_type[stype]["count"] += 1
            fc = rec.get("frame_count", 0)
            by_type[stype]["total_frames"] += fc
            by_type[stype]["frame_counts"].append(fc)

            if rec.get("preempted"):
                preempted_count += 1
            if rec.get("truncated"):
                truncated_count += 1

        # Compute averages and clean up
        by_type_clean = {}
        for stype, data in by_type.items():
            avg = data["total_frames"] / data["count"] if data["count"] > 0 else 0.0
            by_type_clean[stype] = {
                "count": data["count"],
                "total_frames": data["total_frames"],
                "avg_frames_per_seq": round(avg, 1),
            }

        return {
            "total_sequences": len(sequence_records),
            "by_type": by_type_clean,
            "preempted_count": preempted_count,
            "truncated_count": truncated_count,
        }

    # ── frame_metadata.json ──

    def generate_frame_metadata_json(
        self,
        match_url: str | None = None,
        sequence_profiles_used: dict | None = None,
        sequence_records: list[dict] | None = None,
        is_resample: bool = False,
        resample_source_match: str | None = None,
        resample_request_file: str | None = None,
    ):
        """
        Generate frame_metadata.json — the PRIMARY data exchange file
        between Screenshotter and Annotation Tool.
        """
        match_id = self.folder_name

        session_info = {
            "match_id": match_id,
            "match_url": match_url or self.match.get("footballia_url", ""),
            "capture_date": datetime.now(timezone.utc).isoformat(),
            "screenshotter_version": "1.2.0",
            "is_resample": is_resample,
            "resample_source_match": resample_source_match,
            "resample_request_file": resample_request_file,
            "sequence_profiles_used": sequence_profiles_used or {},
        }

        # Build sequence_summary from completed records
        sequence_summary = []
        if sequence_records:
            for rec in sequence_records:
                sequence_summary.append({
                    "sequence_id": rec.get("sequence_id", ""),
                    "sequence_type": rec.get("profile_name", ""),
                    "video_time_start": rec.get("start_video_time"),
                    "video_time_end": rec.get("end_video_time"),
                    "frame_count": rec.get("frame_count", 0),
                    "truncated": rec.get("truncated", False),
                    "preempted_by": rec.get("preempted_by"),
                })

        # Build frames array from results
        frames = []
        for r in self.results:
            frame = {
                "file_name": r.get("filename", ""),
                "video_time": r.get("video_time"),
                "camera_angle": r.get("classified_as", ""),
                "camera_angle_confidence": (
                    int(float(r["confidence"]) * 100)
                    if r.get("confidence") and r["confidence"] not in (None, "")
                    else 0
                ),
                "camera_angle_source": r.get("camera_angle_source", "classifier"),
                "sequence_id": r.get("sequence_id"),
                "sequence_type": r.get("sequence_type"),
                "sequence_purpose": r.get("sequence_purpose"),
                "sequence_position": r.get("sequence_position"),
                "sequence_total_frames": r.get("sequence_total_frames"),
                "sequence_video_time_start": r.get("sequence_video_time_start"),
                "sequence_video_time_end": r.get("sequence_video_time_end"),
                "sequence_truncated": r.get("sequence_truncated", False),
                "sequence_preempted_by": r.get("sequence_preempted_by"),
                "is_resample": r.get("is_resample", False),
                "resample_of": r.get("resample_of"),
                "resample_original_interval": r.get("resample_original_interval"),
            }
            # Ensure numeric types for video_time
            if frame["video_time"] is not None:
                try:
                    frame["video_time"] = float(frame["video_time"])
                except (ValueError, TypeError):
                    pass
            frames.append(frame)

        metadata = {
            "schema_version": "1.0.0",
            "session_info": session_info,
            "sequence_summary": sequence_summary,
            "frames": frames,
        }

        json_path = self.base_path / "frame_metadata.json"
        with open(json_path, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Wrote frame_metadata.json with {len(frames)} frames, {len(sequence_summary)} sequences")
        return str(json_path)

    # ── Accessors ──

    def get_output_dir(self) -> str:
        return str(self.base_path)

    @property
    def output_dir(self) -> str:
        return str(self.base_path)

    def move_frame(self, current_path: str, new_category: str) -> str:
        """
        Move a frame from its current category folder to a new one.
        Used when user reclassifies during review.

        Returns:
            New file path as string.
        """
        src = Path(current_path)
        if not src.exists():
            logger.warning(f"Cannot move frame — file not found: {src}")
            return current_path

        dest_dir = self.base_path / new_category
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name

        # Handle name collision
        if dest.exists():
            stem = src.stem
            suffix = src.suffix
            counter = 1
            while dest.exists():
                dest = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        src.rename(dest)
        logger.info(f"Moved frame: {src.name} from {src.parent.name}/ to {new_category}/")
        return str(dest)

    @staticmethod
    def static_move_frame(current_path: str, new_category: str, base_dir: str) -> str:
        """Static version of move_frame for use outside of an active capture session."""
        src = Path(current_path)
        if not src.exists():
            return current_path
        dest_dir = Path(base_dir) / new_category
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            stem, suffix = src.stem, src.suffix
            counter = 1
            while dest.exists():
                dest = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        src.rename(dest)
        return str(dest)
