from moviepy import VideoFileClip, ImageClip, CompositeVideoClip
from moviepy.video.VideoClip import VideoClip
import numpy as np
import os
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

def _create_gradient_image(width, height, start_color, end_color, direction="vertical"):
    """
    Create a gradient image.

    Args:
        width: Image width in pixels
        height: Image height in pixels
        start_color: Starting color (RGBA tuple)
        end_color: Ending color (RGBA tuple)
        direction: "vertical" (top to bottom) or "horizontal" (left to right)

    Returns:
        PIL Image with gradient
    """
    img = Image.new("RGBA", (width, height))
    pixels = img.load()

    if direction == "vertical":
        for y in range(height):
            ratio = y / max(height - 1, 1)
            r = int(start_color[0] * (1 - ratio) + end_color[0] * ratio)
            g = int(start_color[1] * (1 - ratio) + end_color[1] * ratio)
            b = int(start_color[2] * (1 - ratio) + end_color[2] * ratio)
            a = int(start_color[3] * (1 - ratio) + end_color[3] * ratio)
            for x in range(width):
                pixels[x, y] = (r, g, b, a)
    elif direction == "horizontal":
        for x in range(width):
            ratio = x / max(width - 1, 1)
            r = int(start_color[0] * (1 - ratio) + end_color[0] * ratio)
            g = int(start_color[1] * (1 - ratio) + end_color[1] * ratio)
            b = int(start_color[2] * (1 - ratio) + end_color[2] * ratio)
            a = int(start_color[3] * (1 - ratio) + end_color[3] * ratio)
            for y in range(height):
                pixels[x, y] = (r, g, b, a)
    else:
        raise ValueError(f"Invalid gradient direction: {direction}. Use 'vertical' or 'horizontal'.")

    return img

def make_text_rgba_image(
    text: str,
    max_width_px: int | None,
    font="DejaVuSans.ttf",
    font_size=48,
    color="#FFFFFF",
    stroke_color="#000000",
    stroke_width=0,
    bg_color=None,
    bg_gradient=None,
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

    # Create background - gradient takes priority over solid color
    if bg_gradient:
        # bg_gradient should be a dict like:
        # {"start": "#000000FF", "end": "#00000000", "direction": "vertical"}
        start_color = _parse_hex_color(bg_gradient.get("start", "#000000FF"))
        end_color = _parse_hex_color(bg_gradient.get("end", "#00000000"))
        direction = bg_gradient.get("direction", "vertical")
        img = _create_gradient_image(img_w, img_h, start_color, end_color, direction)
    else:
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

def _get_character_positions(text, font_obj, line_spacing=6):
    """
    Calculate the position of each character in the rendered text.

    Returns:
        List of tuples: [(char, x, y, width, height), ...]
    """
    tmp = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tmp)

    lines = text.split('\n')
    char_positions = []

    y_offset = 0
    for line in lines:
        x_offset = 0
        for char in line:
            bbox = draw.textbbox((0, 0), char, font=font_obj)
            char_width = bbox[2] - bbox[0]
            char_height = bbox[3] - bbox[1]

            char_positions.append((char, x_offset, y_offset, char_width, char_height))
            x_offset += char_width

        # Move to next line
        line_bbox = draw.textbbox((0, 0), line if line else " ", font=font_obj)
        line_height = line_bbox[3] - line_bbox[1]
        y_offset += line_height + line_spacing

    return char_positions


def make_animated_text_clip(
    text: str,
    duration: float,
    max_width_px: int | None,
    char_fade_duration: float = 0.15,
    char_delay: float = 0.05,
    font="DejaVuSans.ttf",
    font_size=48,
    color="#FFFFFF",
    stroke_color="#000000",
    stroke_width=0,
    bg_color=None,
    bg_gradient=None,
    padding=10,
    align="center",
    line_spacing=6,
    opacity=1.0
):
    """
    Create an animated text clip where each character fades in sequentially.

    Args:
        text: Text to display
        duration: Total duration of the clip
        max_width_px: Maximum width for text wrapping
        char_fade_duration: How long each character takes to fade in (seconds)
        char_delay: Delay between each character starting to fade (seconds)
        ... (other text styling parameters)

    Returns:
        VideoClip with character-by-character fade-in animation
    """
    # Create the base text image (fully rendered)
    base_img = make_text_rgba_image(
        text=text,
        max_width_px=max_width_px,
        font=font,
        font_size=font_size,
        color=color,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        bg_color=bg_color,
        bg_gradient=bg_gradient,
        padding=padding,
        align=align,
        line_spacing=line_spacing,
        opacity=opacity
    )

    base_array = np.array(base_img).astype(np.float32)
    img_height, img_width = base_array.shape[:2]

    # Get font object for character measurements
    font_obj = _load_font(font, int(font_size))

    # Split text into lines for proper wrapping
    tmp = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tmp)
    wrapped_lines = _wrap_text(draw, text, font_obj, max_width_px)
    wrapped_text = '\n'.join(wrapped_lines)

    # Get character positions
    char_positions = _get_character_positions(wrapped_text, font_obj, line_spacing)
    total_chars = len([c for c in wrapped_text if c != '\n'])

    def make_frame(t):
        """Generate frame at time t with progressive character fade-in."""
        # Start with base image
        frame = base_array.copy()

        # Calculate which characters should be visible and their opacity
        char_index = 0
        for char, x, y, w, h in char_positions:
            if char == '\n':
                continue

            # Calculate when this character should start and finish fading in
            char_start_time = char_index * char_delay
            char_end_time = char_start_time + char_fade_duration

            # Calculate opacity for this character at time t
            if t < char_start_time:
                # Not started yet - fully transparent
                char_opacity = 0.0
            elif t >= char_end_time:
                # Fully faded in
                char_opacity = 1.0
            else:
                # Currently fading in
                progress = (t - char_start_time) / char_fade_duration
                char_opacity = progress

            # Apply opacity to this character's region
            # We need to find the character's bounding box in the rendered image
            # This is approximate - we'll apply opacity to a region
            x_start = int(padding + x)
            y_start = int(padding + y)
            x_end = min(x_start + int(w) + 2, img_width)
            y_end = min(y_start + int(h) + 2, img_height)

            if x_start < img_width and y_start < img_height:
                # Modify alpha channel for this character
                if char_opacity < 1.0:
                    frame[y_start:y_end, x_start:x_end, 3] *= char_opacity

            char_index += 1

        return frame.astype(np.uint8)

    # Create the animated clip
    animated_clip = VideoClip(make_frame, duration=duration)
    animated_clip = animated_clip.with_fps(30)  # 30 fps for smooth animation

    return animated_clip, (img_width, img_height)


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
        overlay_type = overlay_config.get("type", "image")
        start_time = float(overlay_config.get("start_time", 0))
        duration = float(overlay_config.get("duration", 0))
        end_time = start_time + duration

        if duration <= 0:
            print(f"Warning: Overlay {i+1} has non-positive duration, skipping...")
            continue

        if start_time >= video_duration:
            print(f"Warning: Overlay {i+1} starts after video ends, skipping...")
            continue

        # Handle image_stack type - creates multiple overlays from a single config
        if overlay_type == "image_stack":
            # Get all image_paths from this overlay (should be a list)
            image_paths = overlay_config.get("image_paths", [])
            if not image_paths:
                print(f"Warning: image_stack overlay {i+1} has no image_paths, skipping...")
                continue

            num_images = len(image_paths)
            time_interval = duration / num_images  # Time between each image appearing
            rotation_range = float(overlay_config.get("rotation_range", 10))
            position = overlay_config.get("position", "center")
            width = overlay_config.get("width", None)
            height = overlay_config.get("height", None)
            bg_color = overlay_config.get("bg_color", None)
            opacity = float(overlay_config.get("opacity", 1.0))
            scale = overlay_config.get("scale", 1.0)
            fit_mode = overlay_config.get("fit_mode", "contain")  # "stretch", "contain", "cover"

            # Create an overlay for each image
            for img_idx, image_path in enumerate(image_paths):
                if not os.path.exists(image_path):
                    print(f"Warning: Image not found at {image_path}, skipping...")
                    continue

                # Calculate timing for this image - each image appears at intervals but stays visible until the end
                img_start_time = start_time + (img_idx * time_interval)
                # Duration extends from when this image appears until the end of the total duration
                img_duration = duration - (img_idx * time_interval)

                # Generate random rotation within range
                import random
                random_rotation = random.uniform(-rotation_range, rotation_range)

                # Transform and load image (but don't apply rotation yet)
                transformed_img = transform_image(image_path, scale=scale, rotation=0, opacity=opacity)

                # Apply width/height override with fit_mode
                if width is not None or height is not None:
                    current_w, current_h = transformed_img.size

                    # Calculate target dimensions
                    if width is not None:
                        target_width = int(video_size[0] * width) if width <= 1.0 else int(width)
                    else:
                        target_width = None

                    if height is not None:
                        target_height = int(video_size[1] * height) if height <= 1.0 else int(height)
                    else:
                        target_height = None

                    # Apply fit_mode logic
                    if fit_mode == "stretch":
                        # Stretch to exact dimensions (may distort aspect ratio)
                        new_width = target_width if target_width is not None else current_w
                        new_height = target_height if target_height is not None else current_h
                        transformed_img = transformed_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                    elif fit_mode == "contain":
                        # Fit within bounds while maintaining aspect ratio
                        if target_width is not None and target_height is not None:
                            # Calculate scaling factors
                            width_scale = target_width / current_w
                            height_scale = target_height / current_h
                            # Use the smaller scale to ensure it fits within both constraints
                            scale_factor = min(width_scale, height_scale)
                            new_width = int(current_w * scale_factor)
                            new_height = int(current_h * scale_factor)
                        elif target_width is not None:
                            # Only width specified, scale proportionally
                            scale_factor = target_width / current_w
                            new_width = target_width
                            new_height = int(current_h * scale_factor)
                        else:  # target_height is not None
                            # Only height specified, scale proportionally
                            scale_factor = target_height / current_h
                            new_width = int(current_w * scale_factor)
                            new_height = target_height
                        transformed_img = transformed_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                    elif fit_mode == "cover":
                        # Cover bounds while maintaining aspect ratio (may crop)
                        if target_width is not None and target_height is not None:
                            # Calculate scaling factors
                            width_scale = target_width / current_w
                            height_scale = target_height / current_h
                            # Use the larger scale to ensure it covers both dimensions
                            scale_factor = max(width_scale, height_scale)
                            new_width = int(current_w * scale_factor)
                            new_height = int(current_h * scale_factor)
                            transformed_img = transformed_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                            # Crop to exact target dimensions from center
                            left = (new_width - target_width) // 2
                            top = (new_height - target_height) // 2
                            right = left + target_width
                            bottom = top + target_height
                            transformed_img = transformed_img.crop((left, top, right, bottom))
                        elif target_width is not None:
                            # Only width specified, scale proportionally (same as contain)
                            scale_factor = target_width / current_w
                            new_width = target_width
                            new_height = int(current_h * scale_factor)
                            transformed_img = transformed_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                        else:  # target_height is not None
                            # Only height specified, scale proportionally (same as contain)
                            scale_factor = target_height / current_h
                            new_width = int(current_w * scale_factor)
                            new_height = target_height
                            transformed_img = transformed_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    else:
                        print(f"Warning: Unknown fit_mode '{fit_mode}', defaulting to 'contain'")
                        # Default to contain
                        if target_width is not None and target_height is not None:
                            width_scale = target_width / current_w
                            height_scale = target_height / current_h
                            scale_factor = min(width_scale, height_scale)
                            new_width = int(current_w * scale_factor)
                            new_height = int(current_h * scale_factor)
                        elif target_width is not None:
                            scale_factor = target_width / current_w
                            new_width = target_width
                            new_height = int(current_h * scale_factor)
                        else:
                            scale_factor = target_height / current_h
                            new_width = int(current_w * scale_factor)
                            new_height = target_height
                        transformed_img = transformed_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                # If bg_color is specified, add a border/mat around the image (not full-screen)
                if bg_color is not None:
                    bg_rgba = _parse_hex_color(bg_color)
                    border_size = 20  # Pixels of border/mat around the image
                    img_w, img_h = transformed_img.size
                    matted_img = Image.new("RGBA", (img_w + border_size * 2, img_h + border_size * 2), bg_rgba)
                    matted_img.paste(transformed_img, (border_size, border_size), transformed_img)
                    transformed_img = matted_img

                # Apply rotation after adding border (so border rotates with image)
                if random_rotation != 0:
                    transformed_img = transformed_img.rotate(-random_rotation, expand=True, fillcolor=(0, 0, 0, 0))

                img_array = np.array(transformed_img)
                img_clip = ImageClip(img_array, duration=img_duration).with_start(img_start_time)
                pos = get_position(position, video_size, (img_array.shape[1], img_array.shape[0]))
                img_clip = img_clip.with_position(pos)
                overlay_clips.append(img_clip)

            continue  # Skip the rest of the loop, we've handled this overlay

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
            bg_color = overlay_config.get("bg_color", None)  # Background color for full-screen background



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

                # If bg_color is specified, create a full-screen background and center the image
                if bg_color is not None:
                    bg_rgba = _parse_hex_color(bg_color)
                    # Create full-screen background
                    background = Image.new("RGBA", video_size, bg_rgba)

                    # Center the image on the background
                    img_w, img_h = transformed_img.size
                    x_offset = (video_size[0] - img_w) // 2
                    y_offset = (video_size[1] - img_h) // 2
                    background.paste(transformed_img, (x_offset, y_offset), transformed_img)

                    transformed_img = background
                    # Position must be (0, 0) since background is full-screen
                    position = (0, 0)

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
            bg_gradient = text_style.get("bg_gradient", None)
            padding = int(text_style.get("padding", 10))
            align = text_style.get("align", "center")
            line_spacing = int(text_style.get("line_spacing", 6))

            # Animation parameters
            char_animation = text_style.get("char_animation", False)
            char_fade_duration = float(text_style.get("char_fade_duration", 0.15))
            char_delay = float(text_style.get("char_delay", 0.05))

            # max_width can be px or ratio of video width
            max_width = text_style.get("max_width", None)
            if max_width is None:
                max_width_px = None
            else:
                max_width_px = int(video_size[0] * max_width) if float(max_width) <= 1.0 else int(max_width)

            # Check if character-by-character animation is enabled
            if char_animation:
                # Use animated text clip
                text_clip, (text_width, text_height) = make_animated_text_clip(
                    text=text,
                    duration=duration,
                    max_width_px=max_width_px,
                    char_fade_duration=char_fade_duration,
                    char_delay=char_delay,
                    font=font,
                    font_size=font_size,
                    color=color,
                    stroke_color=stroke_color,
                    stroke_width=stroke_width,
                    bg_color=bg_color,
                    bg_gradient=bg_gradient,
                    padding=padding,
                    align=align,
                    line_spacing=line_spacing,
                    opacity=opacity
                )
                text_clip = text_clip.with_start(start_time)

                # Apply rotation if needed
                if rotation != 0:
                    text_clip = text_clip.rotate(-rotation)

                tpos = get_position(text_position, video_size, (text_width, text_height))
                text_clip = text_clip.with_position(tpos)
                overlay_clips.append(text_clip)
            else:
                # Use static text image (original behavior)
                text_img = make_text_rgba_image(
                    text=text,
                    max_width_px=max_width_px,
                    font=font,
                    font_size=font_size,
                    color=color,
                    stroke_color=stroke_color,
                    stroke_width=stroke_width,
                    bg_color=bg_color,
                    bg_gradient=bg_gradient,
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



    
