"""Render a code-only, synthetic support-workflow motion demo to MP4.

Uses installed Pillow and imageio-ffmpeg. No web assets, model calls, or ticket data.
"""

from __future__ import annotations

import math
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 1080, 1080
FPS = 60
DURATION = 14
ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "support-workflow-demo.mp4"
SILENT = ROOT / ".video-only.mp4"
AUDIO = ROOT / ".procedural-audio.wav"

BG = "#0A251D"
PANEL = "#12372B"
PANEL_LIGHT = "#194335"
CREAM = "#F7F4D5"
MUTED = "#A9BEA8"
MOSS = "#A6BE75"
ROSE = "#D3968C"
BLUE = "#76B9C6"
LINE = "#426751"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    primary = Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf")
    fallback = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    return ImageFont.truetype(str(primary if primary.exists() else fallback), size)


F12, F15, F18, F22 = (font(n) for n in (12, 15, 18, 22))
B15, B20, B26, B38, B56 = (font(n, True) for n in (15, 20, 26, 38, 56))


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def ease(value: float) -> float:
    p = clamp(value)
    return p * p * (3 - 2 * p)


def alpha_window(t: float, start: float, end: float) -> float:
    return min(ease((t - start) / 0.5), ease((end - t) / 0.5))


def rect(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float], fill: str, radius: int = 20, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(tuple(round(v) for v in box), radius=radius, fill=fill, outline=outline, width=width)


def text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], value: str, face: ImageFont.FreeTypeFont, color: str = CREAM, anchor: str | None = None) -> None:
    draw.text((round(xy[0]), round(xy[1])), value, font=face, fill=color, anchor=anchor)


def line(draw: ImageDraw.ImageDraw, start: tuple[float, float], end: tuple[float, float], color: str = LINE, width: int = 2) -> None:
    draw.line((round(start[0]), round(start[1]), round(end[0]), round(end[1])), fill=color, width=width)


def dot(draw: ImageDraw.ImageDraw, x: float, y: float, radius: float, color: str) -> None:
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def pill(draw: ImageDraw.ImageDraw, x: float, y: float, label: str, color: str, width: int | None = None) -> None:
    extent = width or int(draw.textbbox((0, 0), label, font=B15)[2] + 28)
    rect(draw, (x, y, x + extent, y + 32), PANEL_LIGHT, 16, color)
    text(draw, (x + extent / 2, y + 16), label, B15, color, "mm")


def card(draw: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float, title: str, lines: list[str], accent: str = MOSS, badge: str | None = None) -> None:
    rect(draw, (x + 4, y + 10, x + w + 4, y + h + 10), "#071B16", 22)
    rect(draw, (x, y, x + w, y + h), PANEL, 22, LINE)
    rect(draw, (x + 20, y + 22, x + 26, y + 52), accent, 3)
    text(draw, (x + 43, y + 23), title, B20)
    if badge:
        pill(draw, x + w - 106, y + 22, badge, accent, 84)
    for idx, value in enumerate(lines):
        text(draw, (x + 24, y + 74 + idx * 32), value, F18, CREAM if idx == 0 else MUTED)


def arrow(draw: ImageDraw.ImageDraw, x1: float, y1: float, x2: float, y2: float, t: float, color: str = MOSS) -> None:
    line(draw, (x1, y1), (x2, y2), LINE, 3)
    fraction = (t * 0.55) % 1
    dot(draw, x1 + (x2 - x1) * fraction, y1 + (y2 - y1) * fraction, 7, color)
    direction = 1 if x2 >= x1 else -1
    draw.polygon([(x2, y2), (x2 - direction * 11, y2 - 7), (x2 - direction * 11, y2 + 7)], fill=color)


def background(t: float) -> Image.Image:
    im = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(im)
    for x in range(0, WIDTH, 48):
        line(draw, (x, 0), (x, HEIGHT), "#103328", 1)
    for y in range(0, HEIGHT, 48):
        line(draw, (0, y), (WIDTH, y), "#103328", 1)
    for radius in (180, 290, 400):
        phase = t * 0.5
        cx = 1125 + math.sin(phase) * 20
        cy = 115 + math.cos(phase) * 20
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline="#214937", width=2)
    rect(draw, (42, 26, 322, 62), PANEL, 18, LINE)
    dot(draw, 65, 44, 7, MOSS)
    text(draw, (85, 35), "SUPPORT WORKFLOW", B15)
    pill(draw, 1080, 28, "SYNTHETIC DEMO", BLUE, 154)
    line(draw, (54, 664), (1226, 664), LINE, 2)
    line(draw, (54, 664), (54 + 1172 * clamp(t / DURATION), 664), MOSS, 4)
    labels = ["TICKET", "EVIDENCE", "PLAN", "REVIEW", "HUMAN", "INTEGRATE"]
    for i, label in enumerate(labels):
        text(draw, (54 + i * 230, 676), label, F12, MOSS if i <= int(t / 5) else MUTED)
    return im


def intro(draw: ImageDraw.ImageDraw, t: float) -> None:
    k = ease((t - 0.2) / 0.9)
    text(draw, (88, 166 + (1 - k) * 50), "From ticket to trusted reply.", B56)
    text(draw, (90, 251), "One traceable workflow. Every decision visible.", F22, MUTED)
    for i, (label, color) in enumerate([("RECEIVE", BLUE), ("REASON", MOSS), ("VERIFY", ROSE), ("APPROVE", CREAM)]):
        x = 100 + i * 294
        y = 420 + math.sin(t * 2 + i) * 5
        rect(draw, (x, y, x + 236, y + 104), PANEL, 20, color)
        dot(draw, x + 28, y + 30, 10, color)
        text(draw, (x + 22, y + 65), label, B20)
        if i < 3:
            arrow(draw, x + 245, y + 52, x + 283, y + 52, t, color)


def intake(draw: ImageDraw.ImageDraw, t: float) -> None:
    text(draw, (80, 114), "01 / A request arrives", B38)
    text(draw, (82, 167), "Portal, email, or a service desk queue — one normalized intake.", F22, MUTED)
    offset = (1 - ease((t - 4) / 0.8)) * 200
    card(draw, 84 - offset, 258, 492, 285, "Incoming ticket", [
        "VPN disconnects during calls",
        "Reported: three drops today",
        "Impact: one user, call interrupted",
        "Cause and service status: unknown",
    ], BLUE, "NEW")
    arrow(draw, 610, 398, 710, 398, t, BLUE)
    card(draw, 746 + offset, 280, 410, 242, "Triage", [
        "Classify: connectivity",
        "Route: N2 investigation",
        "Urgency: review impact",
    ], MOSS)
    pill(draw, 782, 480, "NO ACTION TAKEN", ROSE, 196)


def evidence(draw: ImageDraw.ImageDraw, t: float) -> None:
    text(draw, (80, 111), "02 / Ground in approved guidance", B38)
    text(draw, (82, 163), "Retrieval returns sources, never an instruction to act.", F22, MUTED)
    card(draw, 80, 250, 380, 260, "Ticket facts", ["Gateway timeout reported", "Timestamp: ~09:20", "Web access still works"], BLUE)
    arrow(draw, 482, 380, 580, 380, t)
    card(draw, 615, 234, 550, 298, "Knowledge match  ·  KB-104", [
        "VPN connection checks",
        "Capture exact error and time",
        "Check approved service status",
        "Never request passwords or keys",
    ], MOSS, "SOURCE")
    scan = 252 + (t * 110) % 245
    line(draw, (635, scan), (1143, scan), BLUE, 2)


def planning(draw: ImageDraw.ImageDraw, t: float) -> None:
    text(draw, (80, 111), "03 / Plan, critique, revise", B38)
    text(draw, (82, 162), "An independent critic checks evidence, scope, uncertainty, and next step.", F22, MUTED)
    card(draw, 78, 242, 480, 303, "Architect proposes", [
        "Confirm the reported failure",
        "Ask for exact error and time",
        "Check approved status guidance",
        "Escalate repeated failures",
    ], BLUE)
    arrow(draw, 582, 394, 672, 394, t, ROSE)
    score = 7.8 if t < 13.5 else 9.2
    verdict = "REVISION" if t < 13.5 else "GATE PASSED"
    card(draw, 708, 242, 465, 303, "Independent critic", [
        f"Score: {score:.1f} / 10",
        "Citations and safe scope checked",
        "Unknowns remain explicit",
        "Only validated plan moves on",
    ], ROSE if t < 13.5 else MOSS)
    pill(draw, 908, 481, verdict, ROSE if t < 13.5 else MOSS, 201)
    if t >= 13.5:
        text(draw, (1038, 215), "+1.4", B26, MOSS)


def reviews(draw: ImageDraw.ImageDraw, t: float) -> None:
    text(draw, (80, 108), "04 / Separate builder and verifier", B38)
    text(draw, (82, 160), "A draft is checked in fresh context, then reviewed in independent lanes.", F22, MUTED)
    card(draw, 80, 270, 283, 237, "Reply builder", ["Draft a concise reply", "Cite available guidance", "Preserve unknowns"], BLUE)
    arrow(draw, 382, 386, 460, 386, t, BLUE)
    card(draw, 482, 270, 282, 237, "Verifier", ["Read-only check", "Compare with request", "Flag unsupported claims"], MOSS)
    arrow(draw, 785, 386, 855, 386, t, MOSS)
    rect(draw, (885, 241, 1192, 532), PANEL, 22, LINE)
    text(draw, (910, 265), "Specialist lanes", B20)
    for i, (label, color) in enumerate([("Technical", BLUE), ("Security", ROSE), ("Clarity", MOSS)]):
        y = 315 + i * 65
        rect(draw, (909, y, 1168, y + 48), PANEL_LIGHT, 12, color)
        dot(draw, 931, y + 24, 6, color)
        text(draw, (950, y + 12), label, F18)


def human(draw: ImageDraw.ImageDraw, t: float) -> None:
    text(draw, (80, 108), "05 / A human owns the decision", B38)
    text(draw, (82, 160), "Quality gate first. Reviewer approval second. Publishing is a separate action.", F22, MUTED)
    card(draw, 102, 247, 645, 338, "Response ready for review", [
        "Evidence: KB-104  •  Gate: 9.2 / 10",
        "Suggested next step: capture error + time",
        "Unverified: service status and root cause",
        "Draft stays private until approved",
    ], MOSS, "DRAFT")
    step = 0 if t < 22.3 else 1 if t < 23.5 else 2
    statuses = [("REVIEW", BLUE), ("APPROVED", MOSS), ("PUBLISH", ROSE)]
    for i, (label, color) in enumerate(statuses):
        y = 270 + i * 105
        rect(draw, (828, y, 1154, y + 70), PANEL_LIGHT if i <= step else PANEL, 16, color if i <= step else LINE)
        dot(draw, 859, y + 35, 11, color if i <= step else LINE)
        text(draw, (887, y + 20), label, B20, CREAM if i <= step else MUTED)
        if i < 2:
            arrow(draw, 991, y + 75, 991, y + 98, t, color)


def integration(draw: ImageDraw.ImageDraw, t: float) -> None:
    text(draw, (80, 108), "06 / How the pieces connect", B38)
    text(draw, (82, 160), "The prototype keeps read, reasoning, approval, and write boundaries explicit.", F22, MUTED)
    nodes = [
        (82, 249, 277, 119, "TICKET SOURCE", "Portal / Jira queue", BLUE),
        (403, 249, 277, 119, "KNOWLEDGE", "Approved local files", MOSS),
        (724, 249, 277, 119, "MODEL", "Optional live inference", ROSE),
        (1005, 249, 190, 119, "HUMAN", "Final decision", CREAM),
        (403, 439, 277, 119, "WORKFLOW", "Plan • verify • score", BLUE),
        (724, 439, 277, 119, "OUTPUT", "Approved reply", MOSS),
    ]
    for x, y, w, h, label, sub, color in nodes:
        rect(draw, (x, y, x + w, y + h), PANEL, 19, color)
        dot(draw, x + 23, y + 29, 7, color)
        text(draw, (x + 41, y + 17), label, B15, color)
        text(draw, (x + 22, y + 65), sub, F18)
    arrow(draw, 360, 307, 397, 307, t, BLUE)
    arrow(draw, 681, 307, 718, 307, t, MOSS)
    arrow(draw, 1002, 307, 1000 + 1, 307, t, ROSE)
    arrow(draw, 542, 371, 542, 432, t, MOSS)
    arrow(draw, 680, 498, 718, 498, t, BLUE)
    line(draw, (861, 432), (861, 376), LINE, 3)
    dot(draw, 861, 399 + math.sin(t * 5) * 20, 7, ROSE)
    pill(draw, 492, 590, "NO AUTOMATIC CUSTOMER SEND", ROSE, 291)


def finale(draw: ImageDraw.ImageDraw, t: float) -> None:
    text(draw, (640, 203), "Support automation", B56, CREAM, "mm")
    text(draw, (640, 277), "with evidence and a human decision.", B38, CREAM, "mm")
    text(draw, (640, 373), "Ticket  →  Guidance  →  Plan  →  Verify  →  Approve", F22, MUTED, "mm")
    pill(draw, 480, 450, "EXPLORE THE WORKFLOW", MOSS, 320)


SCENES = [
    (0, 4, intro),
    (4, 8, intake),
    (8, 12, evidence),
    (12, 16, planning),
    (16, 20, reviews),
    (20, 24, human),
    (24, 28, integration),
    (28, 30.01, finale),
]


def frame(t: float) -> Image.Image:
    canvas = background(t)
    for start, end, scene in SCENES:
        amount = alpha_window(t, start, end)
        if amount <= 0:
            continue
        layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        scene(ImageDraw.Draw(layer), t)
        layer.putalpha(layer.getchannel("A").point(lambda x: int(x * amount)))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB")
    return canvas


def render_audio() -> None:
    sample_rate = 22050
    with wave.open(str(AUDIO), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        block = bytearray()
        for i in range(DURATION * sample_rate):
            t = i / sample_rate
            fade = min(1.0, t / 1.2, (DURATION - t) / 1.2)
            pad = (math.sin(2 * math.pi * 110 * t) + 0.35 * math.sin(2 * math.pi * 164.81 * t) + 0.25 * math.sin(2 * math.pi * 220 * t)) * 0.055
            pulses = 0.0
            for beat in (4, 8, 12, 13.5, 16, 20, 22.3, 23.5, 24, 28):
                age = t - beat
                if 0 <= age < 0.28:
                    pulses += math.sin(2 * math.pi * (650 - age * 620) * age) * math.exp(-18 * age) * 0.12
            value = int(max(-1.0, min(1.0, (pad + pulses) * max(0, fade))) * 32767)
            block += value.to_bytes(2, "little", signed=True)
            if len(block) >= 32768:
                wav.writeframes(block)
                block.clear()
        if block:
            wav.writeframes(block)


def main() -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    encoder = subprocess.Popen(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(SILENT)],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert encoder.stdin is not None
    for n in range(DURATION * FPS):
        encoder.stdin.write(frame(n / FPS).tobytes())
    encoder.stdin.close()
    error = encoder.stderr.read().decode("utf-8", "replace") if encoder.stderr else ""
    if encoder.wait() != 0:
        raise RuntimeError(f"Video encoding failed: {error}")
    render_audio()
    result = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(SILENT), "-i", str(AUDIO), "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(OUTPUT)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Audio mux failed: {result.stderr}")
    SILENT.unlink(missing_ok=True)
    AUDIO.unlink(missing_ok=True)
    print(f"Rendered {OUTPUT} ({DURATION}s, {WIDTH}x{HEIGHT}, {FPS} fps)")


if __name__ == "__main__":
    main()
