import csv
import json
from datetime import datetime
from pathlib import Path

from backend.utils import logger


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

        self.results.append({
            "filename": filename,
            "classified_as": classified_as,
            "confidence": classification.get("confidence", 0),
            "video_time": video_time,
            "video_part": video_part,
            "reasoning": classification.get("reasoning", ""),
            "timestamp": datetime.now().isoformat(),
        })

        return filepath

    def save_frame_to_pending(self, jpeg_bytes: bytes, video_time: float) -> Path:
        """Save a frame to PENDING/ for manual classification later."""
        pending_dir = self.base_path / "PENDING"
        pending_dir.mkdir(parents=True, exist_ok=True)
        filename = f"frame_{video_time:08.2f}.jpg"
        filepath = pending_dir / filename
        filepath.write_bytes(jpeg_bytes)
        return filepath

    def generate_metadata_csv(self):
        csv_path = self.base_path / "metadata.csv"
        if not self.results:
            return

        fieldnames = [
            "filename", "classified_as", "confidence", "video_time",
            "video_part", "reasoning", "timestamp",
        ]

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in self.results:
                writer.writerow({k: r.get(k, "") for k in fieldnames})

        logger.info(f"Wrote metadata.csv with {len(self.results)} entries")

    def generate_summary_json(self, classifier, duration_seconds: float):
        counts = {cat: 0 for cat in self.categories}
        for r in self.results:
            cam = r.get("classified_as") or r.get("camera_type", "OTHER")
            if cam in counts:
                counts[cam] += 1

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
                "api_cost": round(classifier.get_cost(), 6),
                "api_calls": classifier.get_call_count(),
                "provider": classifier.get_provider_name(),
            },
            "output_dir": str(self.base_path),
            "generated_at": datetime.now().isoformat(),
        }

        json_path = self.base_path / "summary.json"
        with open(json_path, "w") as f:
            json.dump(summary, f, indent=2)

        logger.info(f"Wrote summary.json")

    def get_output_dir(self) -> str:
        return str(self.base_path)

    @property
    def output_dir(self) -> str:
        return str(self.base_path)
