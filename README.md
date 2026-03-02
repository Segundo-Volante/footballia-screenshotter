# Footballia Screenshotter

Automated screenshot capture tool for football match broadcasts on [Footballia](https://footballia.eu). Captures frames at regular intervals, classifies each frame by camera angle using OpenAI's GPT-4o-mini vision API, and organizes them into labeled folders.

Built for football analysts, scouts, and data teams who need categorized broadcast stills sorted by camera type (wide, medium, closeup, aerial, behind goal, etc.).

## Features

- **Web UI** — Browser-based interface with match selector, target configuration, and live dashboard
- **Auto camera classification** — GPT-4o-mini vision classifies each frame into 8 camera angle categories
- **Real-time progress** — WebSocket-powered live dashboard with per-category progress bars, thumbnails, and activity log
- **Pause / Resume / Stop** — Full playback control with timestamp tracking for resuming later
- **Multi-part support** — Handles matches split into 2 video files (auto-detects part 2)
- **Persistent login** — Browser profile is saved between runs so you only log in once
- **Resume captures** — Existing screenshots are detected and targets adjusted automatically
- **Organized output** — Screenshots sorted into folders by camera type, with `metadata.csv` and `summary.json`

## Camera Angle Categories

| Category | Description |
|---|---|
| `WIDE_CENTER` | Main broadcast camera, full pitch width, 8+ players |
| `WIDE_LEFT` | Broadcast camera panned to follow play on the left |
| `WIDE_RIGHT` | Broadcast camera panned to follow play on the right |
| `MEDIUM` | Tighter zone shot, 3-7 players visible |
| `CLOSEUP` | Tight on 1-2 people, faces, celebrations, reactions |
| `BEHIND_GOAL` | Camera behind goal line looking down the pitch |
| `AERIAL` | Spider cam, top-down overhead view |
| `OTHER` | Crowd shots, scoreboards, graphics, replays, studio |

## Prerequisites

- **Python 3.11+**
- **A Footballia account** (free) — [Sign up here](https://footballia.eu/users/sign_up)
- **An OpenAI API key** with access to `gpt-4o-mini` — [Get one here](https://platform.openai.com/api-keys)

## Installation

1. **Clone the repo**

   ```bash
   git clone https://github.com/YOUR_USERNAME/footballia-screenshotter.git
   cd footballia-screenshotter
   ```

2. **Create a virtual environment** (recommended)

   ```bash
   python -m venv venv
   source venv/bin/activate    # macOS / Linux
   # venv\Scripts\activate     # Windows
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Install Playwright browsers**

   ```bash
   playwright install chromium
   ```

5. **Set up your API key**

   Copy the example env file and add your OpenAI key:

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and replace the placeholder:

   ```
   OPENAI_API_KEY=sk-proj-your-actual-key-here
   ```

## Usage

### 1. Prepare your Excel data

Place your match data Excel file in the `data/` folder. The file must have a sheet named **"Match Data"** with these columns:

| Column | Description |
|---|---|
| `MD` | Matchday number (1-38) |
| `Date` | Match date |
| `H/A` | Home or Away |
| `Opponent` | Opponent team name |
| `Score` | Final score |
| `Result` | W / D / L |
| `Footballia URL` | Full URL to the match on footballia.eu |

The `Footballia URL` column is created automatically if missing. Additional columns (Starting XI, Substitutes, Goal Scorers, etc.) are optional.

To find Footballia URLs: go to [footballia.eu](https://footballia.eu), search for your team, and copy the match page URLs.

### 2. Start the server

```bash
python main.py
```

Open **http://localhost:8000** in your browser.

### 3. Select a match

The match table shows all matches from your Excel file. Matches with a Footballia URL are clickable. Select one to proceed.

### 4. Configure targets

Set how many screenshots you want for each camera angle. The default targets are:

- Wide Center: 50
- Wide Left/Right: 20 each
- Medium: 15
- Closeup: 10
- Behind Goal: 5
- Aerial: 3

You can also set a custom start time to skip ahead in the video.

### 5. Start capture

Click **Start Capture**. A Chromium browser window will open.

**First time only:** Footballia requires a free account. When the browser opens:
1. The app will detect the login wall and show "Login required" in the dashboard
2. Log in manually in the Playwright browser window that opened
3. The app auto-detects when you've logged in and proceeds

Your login session is saved in `.browser_profile/` so you won't need to log in again on subsequent runs.

### 6. Monitor progress

The live dashboard shows:
- Video playback position and duration
- Per-category progress bars (captured vs target)
- Thumbnail of the latest captured frame
- Running API cost
- Activity log

Use **Pause** to save your position (the timestamp is shown so you can resume later) or **Stop** to end the capture.

## Output Structure

Each capture creates a folder under `recordings/`:

```
recordings/
  MD04_Athletic_Club_2024-08-31/
    WIDE_CENTER/
      frame_00045.30_wide_center_conf95.jpg
      frame_00047.30_wide_center_conf95.jpg
      ...
    WIDE_LEFT/
      ...
    MEDIUM/
      ...
    CLOSEUP/
      ...
    metadata.csv
    summary.json
```

- **`metadata.csv`** — Every saved frame with camera type, confidence, video time, player count, replay flag
- **`summary.json`** — Capture stats including total frames, per-category counts, duration, API cost

## Configuration

All settings are in `config.yaml`:

```yaml
defaults:
  targets:             # Default screenshot targets per camera type
    WIDE_CENTER: 50
    WIDE_LEFT: 20
    WIDE_RIGHT: 20
    MEDIUM: 15
    CLOSEUP: 10
    BEHIND_GOAL: 5
    AERIAL: 3
    OTHER: 0

sampling:
  interval_seconds: 2.0   # Seconds between screenshots

browser:
  headless: false          # Must be false (Footballia blocks headless)
  viewport_width: 1280
  viewport_height: 720
  timeout_ms: 30000

openai:
  model: gpt-4o-mini       # Vision model for classification
  detail: low              # Image detail level (low = cheaper)
  max_concurrent: 3        # Parallel API calls
  cost_per_frame: 0.00007  # Estimated cost per classification

output:
  base_dir: ./recordings
  thumbnail_width: 320
```

### Cost estimate

At `detail: low` with `gpt-4o-mini`, each frame classification costs ~$0.00007. A full match capture of 123 screenshots costs approximately **$0.01**.

## Project Structure

```
footballia-screenshotter/
  main.py                  # Entry point — starts the server
  config.yaml              # All configurable settings
  .env                     # OpenAI API key (not committed)
  .env.example             # Template for .env
  requirements.txt         # Python dependencies
  backend/
    server.py              # FastAPI app, REST + WebSocket endpoints
    pipeline.py            # Orchestrates capture/classify/broadcast loops
    browser_engine.py      # Playwright browser automation + JWPlayer handling
    camera_classifier.py   # OpenAI GPT-4o-mini vision classification
    excel_manager.py       # Excel file reader (openpyxl + pandas)
    output_manager.py      # File I/O, folder creation, CSV/JSON output
    utils.py               # Logging, config loading, constants
  frontend/
    index.html             # Single-page app with 3 views
    style.css              # Dark theme UI
    app.js                 # Frontend state, WebSocket, view switching
  data/
    *.xlsx                 # Your match data Excel file
  recordings/              # Output folder (auto-created)
  logs/                    # Application logs
```

## Troubleshooting

**"Login required" keeps showing even after logging in**
- Make sure you're logging in within the Playwright browser window (not your regular browser)
- The app checks for a video player element to confirm login — wait a few seconds after submitting credentials

**Video found but screenshots are empty**
- Footballia may have changed their player. Check `logs/footballia.log` for details
- Try closing the browser profile: `rm -rf .browser_profile/` and restart

**OpenAI API errors**
- Verify your key: `curl -s http://localhost:8000/api/health`
- Make sure your key has access to `gpt-4o-mini`
- Check your OpenAI account has billing enabled

**Port 8000 already in use**
- Kill the old process: `lsof -ti:8000 | xargs kill -9`
- Or change the port in `main.py`

## License

MIT
