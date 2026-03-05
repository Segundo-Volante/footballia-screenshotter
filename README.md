<h1 align="center">Footballia Screenshotter</h1>

<p align="center">
  <strong>Automated football broadcast frame capture &amp; AI classification</strong><br>
  Turn any match video into a labeled image dataset — in minutes, not hours.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#how-it-works">How It Works</a> &bull;
  <a href="#features">Features</a> &bull;
  <a href="#api-key-setup">API Keys</a> &bull;
  <a href="#export-formats">Export</a> &bull;
  <a href="#cost-estimate">Cost</a> &bull;
  <a href="#pre-alpha-changelog">Changelog</a>
</p>

---

## What is this?

**Footballia Screenshotter** captures frames from football match videos, classifies each frame by camera angle (or other criteria) using AI, and produces organized, labeled datasets ready for annotation and ML training.

Whether you're building a computer vision model, studying tactical formations, or just need organized match screenshots — this tool does the heavy lifting.

**Three ways to feed it video:**

| Source | Description |
|--------|-------------|
| **Footballia** | Free football archive — paste a URL and go |
| **Local Files** | Your own `.mp4`, `.mkv`, `.avi` files |
| **Streaming Sites** | ESPN+, Paramount+, YouTube *(under construction)* |

---

## Features

### Core
- **3 Video Sources** — Footballia, local video files, streaming sites
- **3 AI Providers** — OpenAI GPT-4o-mini, Google Gemini Flash, Manual classification (free)
- **4 Built-in Tasks** — Camera angle, tactical formation, match events, scene type
- **Custom Tasks** — Define your own categories and descriptions

### Smart Capture
- **Pre-filter** — Skips black frames, duplicates, and scene transitions locally (saves 30-40% on API costs)
- **Adaptive Sampling** — Dynamically adjusts capture intervals based on scene changes
- **Goals Only Mode** — Captures only around goal timestamps
- **Custom Time Ranges** — Specify exact time windows to capture
- **Capture Presets** — Pre-configured target sets for common use cases

### Organization & Review
- **Match Library** — Browse, search, and manage all your matches
- **Gallery View** — Keyboard-driven manual classification and AI review with undo
- **Batch Capture** — Queue multiple matches for automated sequential processing
- **Browse by Coach/Player/Team** — Discover matches from Footballia person pages
- **Project Management** — Create and delete projects with full data cleanup

### Export & Integration
- **4 Export Formats** — COCO JSON, ImageNet, CSV, HuggingFace Datasets
- **Annotation Bridge** — Generates `annotation_ready/` packages with metadata + roster CSVs
- **Season Statistics** — Track progress, accuracy, and costs across your whole season

---

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/Segundo-Volante/footballia-screenshotter.git
cd footballia-screenshotter

# Install Python dependencies
pip install -r requirements.txt

# Install browser automation
playwright install chromium
```

### 2. Set Up API Keys *(optional)*

```bash
cp .env.example .env
```

Open `.env` in your editor and add your key(s):

```env
# Pick one or both:
OPENAI_API_KEY=sk-your-key-here        # ~$0.07 per 1000 frames
GEMINI_API_KEY=your-gemini-key-here     # Free tier: 1,500 req/day
```

> **Don't have an API key?** No problem — **Manual Classification** mode works without any keys. Frames are saved for you to classify by hand in the Gallery view.

### 3. Launch

```bash
python main.py
```

Open **http://localhost:8000** in your browser. That's it!

---

## How It Works

Here's the typical workflow from start to finish:

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│  1. Choose   │────▶│  2. Configure │────▶│  3. Capture    │────▶│  4. Export    │
│  a match     │     │  your task    │     │  & classify    │     │  your dataset │
└─────────────┘     └──────────────┘     └───────────────┘     └──────────────┘
```

| Step | What You Do | What Happens |
|------|-------------|--------------|
| **1. Add a match** | Paste a Footballia URL, select from your library, or load a local video | Match metadata (lineups, goals, date) is auto-scraped if available |
| **2. Configure** | Pick a classification task, AI provider, capture mode, and frame targets | Smart defaults are pre-filled — customize as needed |
| **3. Capture** | Hit "Start Capture" and watch the dashboard | Frames are captured, pre-filtered, and classified by AI in real-time |
| **4. Review** *(optional)* | Open the Gallery to verify or correct AI labels | Keyboard shortcuts make reviewing fast (1-8 keys to reclassify) |
| **5. Export** | Choose your format from the Statistics page | Dataset is packaged and ready for your ML pipeline |

---

## API Key Setup

The app supports two AI providers for automatic classification. You only need **one** (or neither, if using Manual mode).

| Provider | Cost | Speed | How to Get a Key |
|----------|------|-------|------------------|
| **OpenAI GPT-4o-mini** | ~$0.07 / 1K frames | Fast | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| **Google Gemini Flash** | Free tier (1,500 req/day) | Fast | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **Manual** | Free | You classify | No key needed |

Add your keys to `.env` in the project root:

```env
OPENAI_API_KEY=sk-your-key-here
GEMINI_API_KEY=your-gemini-key-here
```

> **Important:** Do NOT put API keys in your shell profile (`~/.zshrc`, `~/.bashrc`, etc.). The `.env` file is gitignored and keeps your keys local to this project.

---

## Export Formats

| Format | Best For | Output |
|--------|----------|--------|
| **COCO JSON** | Object detection pipelines | `annotations.json` + image folder |
| **ImageNet** | Image classification models | Category-based folder structure |
| **CSV** | Spreadsheets, pandas, custom scripts | `frames.csv` with all metadata |
| **HuggingFace** | Sharing on HuggingFace Hub | Ready-to-upload dataset format |

---

## Platform Compatibility

| Video Source | Windows | macOS | Linux | Status |
|---|---|---|---|---|
| Footballia | ✅ | ✅ | ✅ | **Fully supported** |
| Local video files | ✅ | ✅ | ✅ | **Fully supported** |
| YouTube (free) | ✅ | ✅ | ✅ | Under construction |
| ESPN+ (DRM) | ✅ | ⚠️* | ✅ | Under construction |
| Paramount+ (DRM) | ✅ | ⚠️* | ✅ | Under construction |

> \* **macOS + DRM**: Some streaming platforms may produce black screenshots due to DRM restrictions. Use local video files as a workaround.

---

## Cost Estimate

With **GPT-4o-mini** and pre-filter enabled:

| Scope | Estimated Cost |
|-------|---------------|
| 1 match (~120 API calls) | **~$0.008** |
| Full 34-match season | **~$0.26** |
| Manual mode | **$0.00** |

The pre-filter catches black frames, duplicates, and non-game content *before* sending anything to the API, saving 30-40% in costs.

---

## Project Structure

```
footballia-screenshotter/
├── main.py                    # Entry point — starts the server
├── config.yaml                # App settings (sampling, browser, AI models)
├── requirements.txt           # Python dependencies
├── .env.example               # Template for API keys
│
├── backend/
│   ├── server.py              # FastAPI routes + WebSocket
│   ├── pipeline.py            # Main capture orchestration
│   ├── sources/               # Video source implementations
│   │   ├── footballia.py      #   Footballia player integration
│   │   ├── local_file.py      #   Local .mp4/.mkv/.avi support
│   │   └── generic_web.py     #   Generic streaming site capture
│   ├── classifiers/           # AI classification providers
│   │   ├── openai_classifier.py
│   │   ├── gemini_classifier.py
│   │   └── manual_classifier.py
│   ├── pre_filter.py          # Local frame analysis (zero API cost)
│   ├── adaptive_sampler.py    # Dynamic capture intervals
│   ├── batch_manager.py       # Multi-match queue processing
│   ├── footballia_navigator.py # Browse by coach/player/team
│   ├── footballia_scraper.py  # Match page data extraction
│   ├── annotation_bridge.py   # annotation_ready/ package generator
│   ├── stats_aggregator.py    # Season statistics
│   ├── exporter.py            # Dataset export (COCO, ImageNet, CSV, HF)
│   ├── project_config.py      # Project creation and deletion
│   └── match_db.py            # SQLite database for matches + frames
│
├── frontend/
│   ├── index.html             # Single-page web UI
│   ├── app.js                 # Frontend application logic
│   └── style.css              # Styles
│
├── config/
│   ├── project.json           # Team/season config (auto-generated)
│   └── tasks/                 # Classification task templates (JSON)
│
├── screenshots/               # README screenshots
└── tests/                     # Test suite
```

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Docker

```bash
# Best for local video file processing:
docker-compose up

# Pass API keys via environment:
OPENAI_API_KEY=sk-... docker-compose up
```

> **Note:** Footballia mode requires a visible browser (non-headless). Docker works best for local video file processing.

---

## Disclaimer

This project is intended for **educational and research purposes only**. It is not designed or licensed for commercial use.

**Regarding Footballia:** This tool captures screenshots at a throttled, responsible rate — it does **not** send rapid or concurrent requests that could overload Footballia's servers. The capture pipeline includes built-in delays and adaptive sampling to minimize server impact.

**Streaming sites:** Only **Footballia** and **local video file** modes are fully supported and tested. Other streaming site integrations are under construction and may not work reliably.

**Legal:** If you have any concerns about the use of this tool or its interaction with any third-party service, please [open an issue](https://github.com/Segundo-Volante/footballia-screenshotter/issues) and we will address it promptly.

---

## Pre-Alpha Changelog

### Screenshotter Pre Alpha Bug Fixes & Feature Updates

#### Bug Fixes

- **macOS file picker could not select `.json` files** — The native AppleScript file dialog used plain extension strings (e.g., `"json"`) which newer macOS versions no longer match. Fixed by adding UTI (Uniform Type Identifier) mappings (e.g., `public.json`, `public.mpeg-4`) so the dialog accepts both formats.
- **"No targets found" when importing `resample_request.json`** — The import endpoint expected flat JSON keys (`match_url`, `targets`) but the annotation tool exports a nested structure (`match_info.match_url`, `resample_targets[].target_player + sequences[]`). Fixed by supporting both formats with automatic flattening of the nested structure.
- **`annotation_exporter.py` failed on missing fields** — Fixed field access errors and added fallback defaults so exports no longer crash on incomplete annotation data.

#### New Features

- **Resample target delete buttons** — Added ✕ buttons to each target row in the resample plan view, allowing users to remove individual targets before starting capture. Backend: `DELETE /api/resample/tasks/{task_id}/targets/{target_index}`.
- **Resample task delete buttons** — Added ✕ buttons to each task card in the resample task list, allowing users to remove entire resample tasks with confirmation. Backend: `DELETE /api/resample/tasks/{task_id}`.
- **Resample pipeline** — Full backend for resample-based capture: `resample_runner.py` for executing resample tasks, `sequence_manager.py` for managing capture sequences, and new REST endpoints for creating, listing, and managing resample tasks.
- **Output manager improvements** — Enhanced output organization with better file naming conventions and folder structure for captured frames.

---

## License

MIT
