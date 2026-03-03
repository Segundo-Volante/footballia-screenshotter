# Tutorial: Capturing Your First Match

This tutorial walks through capturing and classifying frames from a single football match.

## Step 1: Setup

After running `setup.sh` (or `setup.bat`), start the server:

```bash
source venv/bin/activate
python main.py
```

Open http://localhost:8000. The setup wizard will ask for your team name, season, and competitions.

## Step 2: Add a Match

Click "📋 Match Library" in the Home view. Click "+ Add Match" and enter the opponent name and Footballia URL. Or paste a Footballia URL directly in the Quick Capture bar.

## Step 3: Configure Capture

Click the match row to open the Configuration view. Choose:

* **Task**: Camera Angle Classification (default)
* **Provider**: OpenAI (or Gemini, or Manual for no AI)
* **Preset**: Training Data (balanced targets)
* **Mode**: Full Match

## Step 4: Capture

Click "Start Capture". The Dashboard shows real-time progress: frames captured, categories filled, API costs, filter statistics.

## Step 5: Review (Optional)

When capture completes, click "Review Classifications" to check AI accuracy. Use number keys 1-8 to reclassify any mistakes. The Gallery auto-advances to the next frame.

## Step 6: Use the Data

Your frames are in `recordings/{match_folder}/`:

* `WIDE_CENTER/`, `MEDIUM/`, etc. — sorted by camera angle
* `annotation_ready/` — ready for the Football Annotation Tool

Open the `annotation_ready/images/` folder in the Annotation Tool. It will auto-detect screenshotter metadata and pre-fill session info, rosters, and shot type labels.
