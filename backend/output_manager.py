import csv
import json
from datetime import datetime
from pathlib import Path

from backend.utils import logger, CAMERA_TYPES


class OutputManager:
    def __init__(self, match: dict, base_dir: str):
        self.match = match
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
        for cam_type in CAMERA_TYPES:
            (self.base_path / cam_type).mkdir(exist_ok=True)
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
        counts = {cam: 0 for cam in CAMERA_TYPES}
        for r in self.results:
            cam = r.get("camera_type", "OTHER")
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
        cam_type = classification["camera_type"]
        confidence = int(classification["confidence"] * 100)
        time_str = f"{video_time:08.2f}"

        filename = f"frame_{time_str}_{cam_type.lower()}_conf{confidence}.jpg"
        filepath = self.base_path / cam_type / filename

        filepath.write_bytes(jpeg_bytes)

        self.results.append({
            "filename": filename,
            "camera_type": cam_type,
            "confidence": classification["confidence"],
            "video_time": video_time,
            "video_part": video_part,
            "players_visible": classification.get("players_visible", 0),
            "pitch_visible_pct": classification.get("pitch_visible_pct", 0),
            "is_replay": classification.get("is_replay", False),
            "timestamp": datetime.now().isoformat(),
        })

        return filepath

    def generate_metadata_csv(self):
        csv_path = self.base_path / "metadata.csv"
        if not self.results:
            return

        fieldnames = [
            "filename", "camera_type", "confidence", "video_time",
            "video_part", "players_visible", "pitch_visible_pct",
            "is_replay", "timestamp",
        ]

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in self.results:
                writer.writerow({k: r.get(k, "") for k in fieldnames})

        logger.info(f"Wrote metadata.csv with {len(self.results)} entries")

    def generate_summary_json(self, classifier, duration_seconds: float):
        counts = {cam: 0 for cam in CAMERA_TYPES}
        for r in self.results:
            cam = r.get("camera_type", "OTHER")
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
                "total_tokens": classifier.total_tokens,
            },
            "output_dir": str(self.base_path),
            "generated_at": datetime.now().isoformat(),
        }

        json_path = self.base_path / "summary.json"
        with open(json_path, "w") as f:
            json.dump(summary, f, indent=2)

        logger.info(f"Wrote summary.json")

    def get_output_dir(self) -> str:
        """Return the output directory path as string."""
        return str(self.base_path)

    @property
    def output_dir(self) -> str:
        return str(self.base_path)
