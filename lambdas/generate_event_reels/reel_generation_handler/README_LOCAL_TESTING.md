# Local Testing for Reel Generation

This directory contains the reel generation Lambda handler, refactored to support local testing without AWS dependencies.

## Core Function: `generate_reel_local()`

The `generate_reel_local()` function in `handler.py` is a pure Python function that:
- Takes local file paths (video, images)
- Accepts a reel configuration JSON
- Supports template variable substitution
- Generates a video reel locally

This function can be used for local development and testing without needing AWS S3, DynamoDB, or Lambda.

## Prerequisites

Install required Python packages:

```bash
pip install moviepy pillow numpy
```

Note: MoviePy requires ffmpeg to be installed on your system:
- macOS: `brew install ffmpeg`
- Ubuntu: `sudo apt-get install ffmpeg`
- Windows: Download from https://ffmpeg.org/download.html

## Quick Start

1. **Prepare your files:**
   - A background video file (e.g., `background.mp4`)
   - One or more image files (e.g., `photo1.jpg`, `photo2.jpg`)

2. **Edit `test_local.py`:**
   - Update `video_path` to point to your background video
   - Update `image_paths` to point to your images
   - Adjust the `reel_config` if needed
   - Set your `template_vars` (participantsName, completionTime, etc.)

3. **Run the test:**
   ```bash
   python test_local.py
   ```

4. **Check output:**
   - The generated reel will be saved as `output_reel.mp4`

## Example Usage

```python
from handler import generate_reel_local
import json

# Define your configuration
config = {
    "overlays": [
        {
            "type": "image",
            "start_time": 2.0,
            "duration": 5.0,
            "position": "top-right",
            "scale": 0.5
        },
        {
            "type": "text",
            "text": "Name: ${participantsName}",
            "start_time": 2.0,
            "duration": 5.0,
            "position": "bottom",
            "text_style": {
                "font_size": 48,
                "color": "#FFFFFF"
            }
        }
    ]
}

# Generate the reel
generate_reel_local(
    video_path="background.mp4",
    image_paths=["photo1.jpg", "photo2.jpg"],
    reel_config_json=json.dumps(config),
    output_path="output.mp4",
    template_vars={
        "participantsName": "John Doe",
        "completionTime": "02:30:15"
    }
)
```

## Reel Configuration Format

The reel configuration is a JSON object with an `overlays` array. Each overlay can be either an image or text.

### Image Overlay

```json
{
  "type": "image",
  "start_time": 2.0,
  "duration": 5.0,
  "position": "top-right",
  "scale": 0.6,
  "rotation": 0,
  "opacity": 1.0,
  "width": null,
  "height": null,
  "bg_color": null
}
```

**Position options:**
- String keywords: `"center"`, `"top"`, `"bottom"`, `"left"`, `"right"`, `"top-left"`, `"top-right"`, `"bottom-left"`, `"bottom-right"`
- Dict with x/y: `{"x": 100, "y": 200}` (pixels)
- Dict with ratios: `{"x": 0.5, "y": 0.5}` (0.0-1.0 ratio of video size)

**Background Color (Full-Screen Background):**

Add a `bg_color` to create a **full-screen background** with your image centered on it. This works with both `scale` and `width/height` parameters:

```json
{
  "type": "image",
  "start_time": 2.0,
  "duration": 5.0,
  "scale": 0.6,
  "bg_color": "#FFFFFF"
}
```

**How it works:**
- Creates a background that fills the **entire video screen**
- Centers the scaled/sized image on top of the background
- Perfect for ensuring images always appear on a consistent background color
- The `position` parameter is ignored when `bg_color` is used (image is always centered)

**Color format:**
- Hex with optional alpha: `"#FFFFFF"` (white), `"#000000"` (black), `"#FFFFFFCC"` (semi-transparent white)

**Examples:**

White background with scaled image:
```json
{
  "type": "image",
  "start_time": 2.0,
  "duration": 3.0,
  "scale": 0.5,
  "bg_color": "#FFFFFF"
}
```

Black background with specific dimensions:
```json
{
  "type": "image",
  "start_time": 5.0,
  "duration": 3.0,
  "width": 800,
  "height": 600,
  "bg_color": "#000000"
}
```

### Text Overlay

```json
{
  "type": "text",
  "text": "Hello ${participantsName}!",
  "start_time": 0.0,
  "duration": 10.0,
  "position": "center",
  "text_style": {
    "font": "DejaVuSans.ttf",
    "font_size": 72,
    "color": "#FFFFFF",
    "stroke_color": "#000000",
    "stroke_width": 3,
    "bg_color": null,
    "bg_gradient": null,
    "padding": 20,
    "align": "center",
    "max_width": 0.85,
    "line_spacing": 10
  }
}
```

**Background Options:**
- Use `bg_color` for a solid background color (e.g., `"#000000AA"`)
- Use `bg_gradient` for a gradient background (takes priority over `bg_color`)

**Gradient Background Example:**
```json
{
  "type": "text",
  "text": "Runner: ${participantsName}",
  "start_time": 0.0,
  "duration": 5.0,
  "position": "bottom",
  "text_style": {
    "font_size": 48,
    "color": "#FFFFFF",
    "bg_gradient": {
      "start": "#000000DD",
      "end": "#00000000",
      "direction": "vertical"
    },
    "padding": 20
  }
}
```

**Gradient Configuration:**
- `start`: Starting color in hex format with alpha channel (RRGGBBAA)
  - Example: `"#000000DD"` = black with 87% opacity
  - Example: `"#000000FF"` = fully opaque black
- `end`: Ending color in hex format with alpha channel
  - Example: `"#00000000"` = fully transparent black
  - Example: `"#00000088"` = black with 53% opacity
- `direction`: Either `"vertical"` (top to bottom) or `"horizontal"` (left to right)

**Common Gradient Patterns:**
- **Black fade (top to bottom)**: `{"start": "#000000DD", "end": "#00000000", "direction": "vertical"}`
- **Black fade (left to right)**: `{"start": "#000000CC", "end": "#00000000", "direction": "horizontal"}`
- **Dark solid to semi-transparent**: `{"start": "#000000FF", "end": "#00000088", "direction": "vertical"}`
- **Colored gradient**: `{"start": "#1a1a1aFF", "end": "#00000000", "direction": "vertical"}`

### Text Animations

**Character-by-Character Fade In:**

You can animate text so that each character fades in one at a time (typewriter/reveal effect):

```json
{
  "type": "text",
  "text": "CONGRATULATIONS!",
  "start_time": 2.0,
  "duration": 4.0,
  "position": "top",
  "text_style": {
    "font_size": 80,
    "color": "#FFD700",
    "stroke_color": "#000000",
    "stroke_width": 4,
    "padding": 30,
    "align": "center",
    "char_animation": true,
    "char_fade_duration": 0.2,
    "char_delay": 0.08
  }
}
```

**Animation Parameters:**
- `char_animation`: Set to `true` to enable character-by-character fade-in (default: `false`)
- `char_fade_duration`: How long each character takes to fade in, in seconds (default: `0.15`)
  - Smaller values = faster fade (e.g., `0.1` for quick fade)
  - Larger values = slower fade (e.g., `0.3` for gradual fade)
- `char_delay`: Delay between each character starting to fade, in seconds (default: `0.05`)
  - Smaller values = characters appear closer together (e.g., `0.03` for rapid-fire)
  - Larger values = more noticeable sequential effect (e.g., `0.1` for dramatic reveal)

**Examples:**
- **Fast typewriter effect**: `"char_fade_duration": 0.1, "char_delay": 0.03`
- **Dramatic reveal**: `"char_fade_duration": 0.3, "char_delay": 0.1`
- **Smooth appearance**: `"char_fade_duration": 0.15, "char_delay": 0.05` (default)

## Template Variables

The configuration supports template variable substitution using `${variableName}` syntax:

```python
template_vars = {
    "participantsName": "John Doe",
    "completionTime": "02:45:30",
    "eventName": "Boston Marathon 2024"
}
```

These will be replaced in any text fields in your configuration.

## Common Issues

1. **ModuleNotFoundError: No module named 'moviepy'**
   - Solution: `pip install moviepy`

2. **MoviePy error: FFMPEG not found**
   - Solution: Install ffmpeg on your system

3. **Warning: Skipping image overlay (no more images available)**
   - This is just a warning - the reel will still be generated
   - Extra image overlays (beyond the number of images provided) will be automatically skipped
   - Text overlays are always included regardless of image count

4. **Font not found errors**
   - Solution: Use absolute path to font file, or use default fonts like "DejaVuSans.ttf" (available on most systems)

## Architecture

The refactored code separates concerns:

- **`generate_reel_local()`**: Pure function, no AWS dependencies
- **`handler()`**: AWS Lambda handler that:
  1. Fetches data from DynamoDB
  2. Downloads files from S3
  3. Calls `generate_reel_local()`
  4. Uploads result to S3
  5. Updates DynamoDB

This makes the core video generation logic testable and reusable outside of AWS Lambda.
