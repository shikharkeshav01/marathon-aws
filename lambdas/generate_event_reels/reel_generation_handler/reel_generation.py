import os
# Set TMPDIR to /tmp to ensure ffmpeg uses writable directory in Lambda
os.environ['TMPDIR'] = '/tmp'
os.environ['TEMP'] = '/tmp'
os.environ['TMP'] = '/tmp'

from moviepy import VideoFileClip, ImageClip, CompositeVideoClip
import numpy as np
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw, ImageFont

def _parse_hex_color(color):
    if color is None:
        return None
    if isinstance(color, (tuple, list)) and len(color) in (3, 4):
        return tuple(color)
    s = str(color).strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) == 6:
        r = int(s[0:2], 16); g = int(s[2:4], 16); b = int(s[4:6], 16)
        return (r, g, b, 255)
    if len(s) == 8:
        r = int(s[0:2], 16); g = int(s[2:4], 16); b = int(s[4:6], 16); a = int(s[6:8], 16)
        return (r, g, b, a)
    raise ValueError(f"Unsupported color format: {color}")

def _load_font(font_name_or_path, font_size):
    # If a direct path is given and exists, use it
    if font_name_or_path and os.path.exists(font_name_or_path):
        return ImageFont.truetype(font_name_or_path, font_size)

    # Try common bundled/default fonts
    # DejaVuSans.ttf is commonly available in many environments
    candidates = [
        font_name_or_path,
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "arial.ttf",
    ]
    for c in candidates:
        if not c:
            continue
        try:
            return ImageFont.truetype(c, font_size)
        except Exception:
            pass

    # Fallback: PIL default bitmap font (limited sizing)
    return ImageFont.load_default()

def _wrap_text(draw, text, font, max_width_px):
    # Preserve manual newlines first
    lines = []
    for paragraph in str(text).split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue

        words = paragraph.split(" ")
        current = []
        for w in words:
            test = (" ".join(current + [w])).strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            w_px = bbox[2] - bbox[0]
            if max_width_px and w_px > max_width_px and current:
                lines.append(" ".join(current))
                current = [w]
            else:
                current.append(w)
        if current:
            lines.append(" ".join(current))
    return lines

def make_text_rgba_image(
    text: str,
    max_width_px: int | None,
    font="DejaVuSans.ttf",
    font_size=48,
    color="#FFFFFF",
    stroke_color="#000000",
    stroke_width=0,
    bg_color=None,
    padding=10,
    align="center",        # left|center|right
    line_spacing=6,
    opacity=1.0
) -> Image.Image:
    # Create a tiny canvas just to measure/wrap
    tmp = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tmp)
    font_obj = _load_font(font, int(font_size))

    # Wrap to max_width_px if provided
    lines = _wrap_text(draw, text, font_obj, max_width_px)

    # Measure each line
    line_boxes = [draw.textbbox((0, 0), ln, font=font_obj, stroke_width=int(stroke_width)) for ln in lines]
    line_widths = [(b[2] - b[0]) for b in line_boxes]
    line_heights = [(b[3] - b[1]) for b in line_boxes]

    text_w = max(line_widths) if line_widths else 0
    text_h = sum(line_heights) + max(0, (len(lines) - 1)) * int(line_spacing)

    pad = int(padding)
    img_w = max(1, text_w + 2 * pad)
    img_h = max(1, text_h + 2 * pad)

    bg_rgba = _parse_hex_color(bg_color) if bg_color else (0, 0, 0, 0)
    img = Image.new("RGBA", (img_w, img_h), bg_rgba)
    draw = ImageDraw.Draw(img)

    fill = _parse_hex_color(color) or (255, 255, 255, 255)
    sc = _parse_hex_color(stroke_color) or (0, 0, 0, 255)
    sw = int(stroke_width)

    # Apply global opacity by scaling alpha later (keeps strokes etc consistent)
    y = pad
    for i, ln in enumerate(lines):
        b = draw.textbbox((0, 0), ln, font=font_obj, stroke_width=sw)
        lw = b[2] - b[0]
        lh = b[3] - b[1]

        if align == "left":
            x = pad
        elif align == "right":
            x = img_w - pad - lw
        else:
            x = (img_w - lw) // 2

        draw.text(
            (x, y),
            ln,
            font=font_obj,
            fill=fill,
            stroke_width=sw,
            stroke_fill=sc
        )
        y += lh + int(line_spacing)

    # Apply opacity to alpha channel
    if opacity < 1.0:
        arr = np.array(img).astype(np.float32)
        arr[..., 3] = np.clip(arr[..., 3] * float(opacity), 0, 255)
        img = Image.fromarray(arr.astype(np.uint8), "RGBA")

    return img


def test():
    try:
        out = subprocess.check_output(
            ["ffmpeg", "-version"],
            stderr=subprocess.STDOUT
        ).decode()
        print("FFMPEG OK:", out.splitlines()[0])
    except Exception as e:
        print("FFMPEG FAILED:", e)


def transform_image(image_path, scale=1.0, rotation=0, opacity=1.0):
    """
    Load and transform an image with scaling, rotation, and opacity.
    
    Args:
        image_path: Path to the image file
        scale: Scaling factor (1.0 = original size)
        rotation: Rotation angle in degrees
        opacity: Opacity from 0.0 to 1.0
    
    Returns:
        PIL Image object with transformations applied
    """
    # Load image
    img = Image.open(image_path).convert("RGBA")
    
    # Apply scaling
    if scale != 1.0:
        new_size = (int(img.width * scale), int(img.height * scale))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
    
    # Apply rotation
    if rotation != 0:
        img = img.rotate(-rotation, expand=True, fillcolor=(0, 0, 0, 0))
    
    # Apply opacity
    if opacity < 1.0:
        alpha = img.split()[3]
        alpha = alpha.point(lambda p: int(p * opacity))
        img.putalpha(alpha)
    
    return img


def get_position(position, video_size, overlay_size):
    vw, vh = video_size
    ow, oh = overlay_size

    # --------------------------------
    # Numeric tuple or list [x, y]
    # --------------------------------
    if isinstance(position, (list, tuple)) and len(position) == 2:
        x, y = position
        return _resolve_xy(x, y, vw, vh, ow, oh)

    # --------------------------------
    # Dict {x, y}
    # --------------------------------
    if isinstance(position, dict):
        x = position.get("x", 0)
        y = position.get("y", 0)
        return _resolve_xy(x, y, vw, vh, ow, oh)

    # --------------------------------
    # String keyword positions
    # --------------------------------
    if isinstance(position, str):
        p = position.lower()

        # Compound positions
        mapping = {
            "center":      ("center", "center"),
            "top":         ("center", "top"),
            "bottom":      ("center", "bottom"),
            "left":        ("left", "center"),
            "right":       ("right", "center"),
            "top-left":    ("left", "top"),
            "top-right":   ("right", "top"),
            "bottom-left": ("left", "bottom"),
            "bottom-right":("right", "bottom"),
        }

        if p not in mapping:
            raise ValueError(
                f"Invalid position '{position}'. "
                f"Use center/top-right or numeric {{x,y}}."
            )

        hx, vy = mapping[p]

        # Horizontal
        if hx == "left":
            x = 0
        elif hx == "right":
            x = vw - ow
        else:
            x = (vw - ow) // 2

        # Vertical
        if vy == "top":
            y = 0
        elif vy == "bottom":
            y = vh - oh
        else:
            y = (vh - oh) // 2

        return (int(x), int(y))

    raise TypeError(f"Unsupported position format: {position}")


def _resolve_xy(x, y, vw, vh, ow, oh):
    """
    x/y can be:
    - <=1.0 : treated as ratio of video
    - >1.0  : treated as absolute pixels
    """

    # Horizontal
    if abs(x) <= 1.0:
        px = int(vw * x)
    else:
        px = int(x)

    # Vertical
    if abs(y) <= 1.0:
        py = int(vh * y)
    else:
        py = int(y)

    return (px, py)

def save_output():
    pass


"""{
  "overlays": [
    {
      "type": "image",
      "start_time": 2.0,
      "duration": 5.0,
      "position": "top-right",
      "scale": 0.6,
      "rotation": 0,
      "opacity": 1.0
    },
    {
      "type": "text",
      "text": "SALE 50% OFF",
      "start_time": 2.0,
      "duration": 5.0,
      "position": "center",
      "rotation": 0,
      "opacity": 1.0,
      "style": {
        "font": "DejaVuSans.ttf",
        "font_size": 72,
        "color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 6,
        "bg_color": null,
        "padding": 20,
        "align": "center",
        "max_width": 0.85,
        "line_spacing": 10
      }
    },
    {
      "type": "image",
      "start_time": 21.0,
      "duration": 1.0,
      "width": 1.0,
      "height": 1.0,
      "opacity": 0.9
    }
  ]
}
"""

def overlay_images_on_video(video_path, overlays, output_path):
    print(f"Loading video: {video_path}")
    video = VideoFileClip(video_path)
    video_duration = video.duration
    video_size = (video.w, video.h)

    overlay_clips = []

    for i, overlay_config in enumerate(overlays):
        start_time = float(overlay_config.get("start_time", 0))
        duration = float(overlay_config.get("duration", 0))
        end_time = start_time + duration

        if duration <= 0:
            print(f"Warning: Overlay {i+1} has non-positive duration, skipping...")
            continue

        if start_time >= video_duration:
            print(f"Warning: Overlay {i+1} starts after video ends, skipping...")
            continue

        rotation = float(overlay_config.get("rotation", 0))
        opacity = float(overlay_config.get("opacity", 1.0))

        # -----------------------------------------
        # A) IMAGE overlay (existing behavior)
        # -----------------------------------------
        if "image_path" in overlay_config and overlay_config["image_path"] is not None:
            image_path = overlay_config["image_path"]
            scale = overlay_config.get("scale", 1.0)
            position = overlay_config.get("position", "center")
            width = overlay_config.get("width", None)
            height = overlay_config.get("height", None)



            if not os.path.exists(image_path):
                print(f"Warning: Image {i+1} not found at {image_path}, skipping image...")
            else:
                transformed_img = transform_image(image_path, scale=scale, rotation=rotation, opacity=opacity)

                # width/height override
                if width is not None or height is not None:
                    current_w, current_h = transformed_img.size

                    if width is not None:
                        new_width = int(video_size[0] * width) if width <= 1.0 else int(width)
                    else:
                        new_width = current_w

                    if height is not None:
                        new_height = int(video_size[1] * height) if height <= 1.0 else int(height)
                    else:
                        new_height = current_h

                    transformed_img = transformed_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                img_array = np.array(transformed_img)

                img_clip = ImageClip(img_array, duration=duration).with_start(start_time)
                pos = get_position(position, video_size, (img_array.shape[1], img_array.shape[0]))
                img_clip = img_clip.with_position(pos)
                overlay_clips.append(img_clip)

        # -----------------------------------------
        # B) TEXT overlay (NEW)
        # -----------------------------------------
        if "text" in overlay_config and overlay_config["text"] is not None:
            text = str(overlay_config["text"])
            text_position = overlay_config.get("text_position", overlay_config.get("position", "center"))

            text_style = overlay_config.get("text_style", {}) or {}
            font = text_style.get("font", "DejaVuSans.ttf")
            font_size = int(text_style.get("font_size", 48))
            color = text_style.get("color", "#FFFFFF")
            stroke_color = text_style.get("stroke_color", "#000000")
            stroke_width = int(text_style.get("stroke_width", 0))
            bg_color = text_style.get("bg_color", None)
            padding = int(text_style.get("padding", 10))
            align = text_style.get("align", "center")
            line_spacing = int(text_style.get("line_spacing", 6))

            # max_width can be px or ratio of video width
            max_width = text_style.get("max_width", None)
            if max_width is None:
                max_width_px = None
            else:
                max_width_px = int(video_size[0] * max_width) if float(max_width) <= 1.0 else int(max_width)

            text_img = make_text_rgba_image(
                text=text,
                max_width_px=max_width_px,
                font=font,
                font_size=font_size,
                color=color,
                stroke_color=stroke_color,
                stroke_width=stroke_width,
                bg_color=bg_color,
                padding=padding,
                align=align,
                line_spacing=line_spacing,
                opacity=opacity
            )

            # rotate text if needed
            if rotation != 0:
                text_img = text_img.rotate(-rotation, expand=True, fillcolor=(0, 0, 0, 0))

            text_array = np.array(text_img)

            text_clip = ImageClip(text_array, duration=duration).with_start(start_time)
            tpos = get_position(text_position, video_size, (text_array.shape[1], text_array.shape[0]))
            text_clip = text_clip.with_position(tpos)
            overlay_clips.append(text_clip)

    print(f"\nCompositing {len(overlay_clips)} overlays on video...")
    final_video = CompositeVideoClip([video] + overlay_clips)

    print(f"\nWriting output video to: {output_path}")
    final_video.write_videofile(
        output_path,
        codec="libx264",
        audio_codec="aac",
        fps=video.fps,
        preset="medium",
        threads=4,
        temp_audiofile=os.path.join('/tmp', 'temp_audio.m4a')
    )

    video.close()
    final_video.close()
    print(f"\n✓ Done: {output_path}")



    
