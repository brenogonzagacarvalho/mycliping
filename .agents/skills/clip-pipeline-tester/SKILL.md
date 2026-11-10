---
name: clip-pipeline-tester
description: Use this skill when testing or verifying the MyCliping end-to-end pipeline, checking FFmpeg and Faster-Whisper availability, or validating rendering without downloading long videos.
---

# Clip Pipeline Tester & Validation Runbook

This skill allows the agent and developer to quickly verify that all core dependencies (FFmpeg, OpenCV, Faster-Whisper, and MediaPipe) are functioning properly in MyCliping.

---

## 1. Quick Dependency & Smoke Test

Before debugging complex bugs, run the diagnostic script located at:
[`quick_pipeline_check.py`](./scripts/quick_pipeline_check.py)

```powershell
python .agents/skills/clip-pipeline-tester/scripts/quick_pipeline_check.py
```

### What it checks:
- [x] FFmpeg executable binary presence and version.
- [x] PyTorch / CTranslate2 device availability (CPU / CUDA).
- [x] MediaPipe face detector initialization.
- [x] OpenCV video stream reading.

---

## 2. Testing End-to-End Clip Generation Locally

To test video clipping without waiting for 1-hour YouTube/Twitch downloads:
1. Place a short 10-second test video at `data/cache/test_sample.mp4`.
2. Execute a local job via `app.core.clip_pipeline.ClipPipeline` with `source_type="upload"`.
3. Verify that:
   * Transcription produces word-level timestamps.
   * ASS subtitle file is created under `data/cache/*.ass`.
   * Final rendered video exists in `data/clips/*.mp4` and has 1080x1920 resolution.

