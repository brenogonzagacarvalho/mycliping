---
name: ffmpeg-clip-engine
description: Use this skill when implementing, debugging, or optimizing FFmpeg rendering, 9:16 layout filters (smart face framing, split screen, blurred background), ASS subtitle burning, or Windows path escaping in MyCliping.
---

# FFmpeg Clip Engine Guidelines for MyCliping

This skill governs video rendering procedures, filter graph configurations, and Windows compatibility rules when working with [`video_renderer.py`](file:///c:/Users/Usuario/Documents/Dev/mycliping/app/core/video_renderer.py) and [`face_tracker.py`](file:///c:/Users/Usuario/Documents/Dev/mycliping/app/core/face_tracker.py).

---

## 1. Windows FFmpeg Path Escaping (Critical)

In Windows environments, FFmpeg filter arguments (particularly `subtitles` and `ass` filters) fail when Windows backslashes `\` or drive colons `:` are passed without escaping.

### Rules:
1. Always normalize paths to forward slashes:
   ```python
   clean_ass_path = str(Path(ass_path).resolve()).replace("\\", "/")
   ```
2. For filter strings inside `-vf` or `-filter_complex`, escape colons and quotes:
   ```python
   # Ex: C:/path/sub.ass -> C\\:/path/sub.ass or escape colon
   escaped_path = clean_ass_path.replace(":", "\\:")
   filter_str = f"subtitles='{escaped_path}'"
   ```

---

## 2. Layout Modes (9:16 Vertical Video)

### A. Smart Face (`smart_face`)
* Detected crop window from `face_tracker.py` centered around the speaker.
* Target: 1080x1920.
* Filter pattern:
  `crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=1080:1920:flags=lanczos`

### B. Split Screen (`split_screen`)
* Top: Facecam cropped to 1080x960.
* Bottom: Center/gameplay content cropped or scaled to 1080x960.
* Filter pattern:
  `[0:v]crop=...[top]; [0:v]crop=...[bottom]; [top][bottom]vstack=inputs=2[v]`

### C. Blurred Background (`blur_bg`)
* Background: Video scaled up to cover 1080x1920, heavily blurred (`boxblur=20:5` or `gblur=sigma=20`).
* Foreground: Original 16:9 scaled to fit width (1080x608), overlaid in the vertical center.
* Filter pattern:
  `[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg]; [0:v]scale=1080:-1[fg]; [bg][fg]overlay=(W-w)/2:(H-h)/2[v]`

---

## 3. Performance & Codec Optimization

* **Audio**: Always copy or encode with AAC: `-c:a aac -b:a 192k -ar 44100`.
* **Video**:
  * Default software: `-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p`.
  * If NVIDIA NVENC is available: `-c:v h264_nvenc -preset p4 -cq 22`.
* **Seeking**: Place `-ss` before `-i` for fast keyframe seeking, or after `-i` for exact frame-accurate cuts when burning subtitles.

