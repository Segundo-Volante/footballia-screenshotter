# Footballia Screenshotter

Automated football broadcast frame capture and classification tool. Captures screenshots from match videos, classifies each frame by camera angle using AI, and produces organized datasets ready for annotation and ML training.

## Features

- **3 Video Sources**: Footballia (free archive), local video files (.mp4/.mkv), any streaming site
- **3 Classification Modes**: OpenAI GPT-4o-mini, Google Gemini Flash, Manual
- **4 Analysis Tasks**: Camera angle, tactical formation, match events, scene type — or create custom tasks
- **Smart Capture**: Pre-filter saves 30-40% API costs, adaptive sampling, Goals Only mode, custom time ranges
- **Gallery/Review UI**: Keyboard-driven manual classification and AI review with undo
- **Footballia Integration**: Auto-scrapes lineups, goals, match data. Browse by coach/player/team.
- **Annotation Tool Bridge**: Generates annotation_ready/ packages with metadata + roster CSVs
- **Batch Capture**: Queue multiple matches for automated sequential processing
- **Export**: COCO JSON, ImageNet, CSV, HuggingFace Datasets
- **Project Management**: Create/delete projects with full data cleanup
- **Progress Tracking**: Real-time WebSocket updates with progress bars during capture and navigation

## Quick Start

```bash
git clone https://github.com/Segundo-Volante/footballia-screenshotter.git
cd footballia-screenshotter

# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Set up API keys (optional — Manual mode works without keys)
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY and/or GEMINI_API_KEY

# Start
python main.py
# Open http://localhost:8000
```

## Platform Compatibility

| Video Source        | Windows | macOS  | Linux | Status |
|---------------------|---------|--------|-------|--------|
| Footballia          | ✅      | ✅     | ✅    | **Fully supported** |
| Local video files   | ✅      | ✅     | ✅    | **Fully supported** |
| YouTube (free)      | ✅      | ✅     | ✅    | 🚧 Under construction |
| ESPN+ (DRM)         | ✅      | ⚠️*    | ✅    | 🚧 Under construction |
| Paramount+ (DRM)    | ✅      | ⚠️*    | ✅    | 🚧 Under construction |

> **Note**: Only **Footballia** and **local video files** are fully tested and guaranteed to work. Other streaming site integrations are under active development and may not function reliably yet.
>
> \* macOS: DRM platforms may produce black screenshots. Use local video files as an alternative.

## Usage

1. **First run**: Setup wizard asks for team name, season, competitions
2. **Add matches**: Paste Footballia URLs via Quick Capture, browse the Match Library, or discover matches by coach/player/team
3. **Configure**: Choose analysis task, AI provider, target counts per category, and capture mode (full match, goals only, or custom time ranges)
4. **Capture**: Watch the dashboard as frames are captured, classified, and saved in real-time
5. **Review**: Optionally review AI classifications in the Gallery view with keyboard shortcuts
6. **Export**: Use the Statistics view to export datasets in standard ML formats (COCO, ImageNet, CSV, HuggingFace)
7. **Delete project**: Use the "Delete Project" button on the home screen to wipe all data and start fresh

## API Key Setup

The app supports OpenAI and Google Gemini for AI-powered classification. Add your keys to a `.env` file in the project root:

```
OPENAI_API_KEY=sk-your-key-here
GEMINI_API_KEY=your-gemini-key-here
```

If no API key is configured, the UI will show instructions on where to add it. Manual classification mode works without any API keys.

## Project Structure

```
backend/
  server.py              - FastAPI routes + WebSocket
  pipeline.py            - Main capture orchestration
  sources/               - Video source implementations (Footballia, local file, generic web)
  classifiers/           - AI classification providers (OpenAI, Gemini, Manual)
  pre_filter.py          - Local frame analysis (zero cost)
  adaptive_sampler.py    - Dynamic capture intervals
  annotation_bridge.py   - annotation_ready/ package generator
  batch_manager.py       - Multi-match queue
  footballia_navigator.py - Browse by coach/player/team
  footballia_scraper.py  - Match page data extraction
  stats_aggregator.py    - Season statistics
  exporter.py            - Dataset export (COCO, ImageNet, CSV, HuggingFace)
  project_config.py      - Project creation and deletion
  match_db.py            - SQLite database for matches, captures, frames
frontend/
  index.html             - Single-page web UI
  app.js                 - Frontend application logic
  style.css              - Styles
config/
  project.json           - Team/season configuration (auto-generated)
  tasks/                 - Analysis task templates (JSON)
  config.yaml            - App settings (sampling, browser, AI models)
```

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

## Docker

```bash
# Local file mode (works headless):
docker-compose up

# Set API keys in .env or pass via environment:
OPENAI_API_KEY=sk-... docker-compose up
```

> **Note**: Footballia mode requires a visible browser (non-headless). Docker works best for local video file processing.

## Cost Estimate

With GPT-4o-mini and pre-filter enabled:

* ~$0.008 per match (~120 API calls after filtering)
* ~$0.26 for an entire 34-match season
* Manual mode: $0.00 (no API calls)

## Disclaimer

This project is intended for **educational and research purposes only**. It is not designed or licensed for commercial use.

**Regarding Footballia**: This tool captures screenshots at a throttled, responsible rate — it does **not** send rapid or concurrent requests that could overload Footballia's servers. The capture pipeline includes built-in delays and adaptive sampling to minimize server impact.

**Streaming site support**: Only **Footballia** and **local video file** modes are fully supported and tested. Other streaming site integrations (YouTube, ESPN+, Paramount+, etc.) are currently under construction and may not work reliably.

**Legal**: If you have any concerns about the use of this tool or its interaction with any third-party service, please open an issue on this repository and we will address it promptly.

## License

MIT
