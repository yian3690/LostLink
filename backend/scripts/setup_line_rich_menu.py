"""Create and publish the LostLink AI banner and three-button LINE rich menu."""

import asyncio
import os
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
ASSET_PATH = PROJECT_ROOT / "admin-web" / "public" / "lostlink-rich-menu.png"
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.core.config import get_settings  # noqa: E402


MENU_NAME = "LostLink AI 主選單"
WIDTH = 2500
BANNER_HEIGHT = 843
BUTTON_HEIGHT = 843
HEIGHT = BANNER_HEIGHT + BUTTON_HEIGHT


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _centered(draw: ImageDraw.ImageDraw, bounds: tuple[int, int, int, int], text: str, font, fill) -> None:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    width, height = right - left, bottom - top
    x0, y0, x1, y1 = bounds
    draw.text(
        (x0 + (x1 - x0 - width) / 2, y0 + (y1 - y0 - height) / 2 - top),
        text,
        font=font,
        fill=fill,
    )


def render_menu() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#0d8f50")
    draw = ImageDraw.Draw(image)

    start = (8, 74, 53)
    end = (13, 181, 100)
    for y in range(BANNER_HEIGHT):
        ratio = y / max(BANNER_HEIGHT - 1, 1)
        color = tuple(
            round(start[index] + (end[index] - start[index]) * ratio)
            for index in range(3)
        )
        draw.line((0, y, WIDTH, y), fill=color)

    draw.rounded_rectangle((120, 80, 600, 245), radius=70, fill="#caff77")
    _centered(draw, (120, 80, 600, 245), "LostLink AI", _font(70, bold=True), "#073f2d")
    draw.text((120, 295), "遺失的物品，", font=_font(142, bold=True), fill="white")
    draw.text((120, 460), "我們一起找回來。", font=_font(142, bold=True), fill="white")
    draw.rounded_rectangle((1550, 455, 2410, 685), radius=42, fill="white")
    _centered(draw, (1550, 455, 2410, 685), "開啟 LostLink AI  →", _font(62, bold=True), "#087a46")

    columns = ((0, 833), (833, 1666), (1666, WIDTH))
    colors = ("#0d4936", "#0bb564", "#087f49")
    for (left, right), color in zip(columns, colors, strict=True):
        draw.rectangle((left, BANNER_HEIGHT, right, HEIGHT), fill=color)
    draw.line((0, BANNER_HEIGHT, WIDTH, BANNER_HEIGHT), width=8, fill="#77dfa8")
    draw.line((833, BANNER_HEIGHT + 70, 833, HEIGHT - 70), width=5, fill="#35c87d")
    draw.line((1666, BANNER_HEIGHT + 70, 1666, HEIGHT - 70), width=5, fill="#35c87d")

    title_font = _font(94, bold=True)
    _centered(draw, (0, BANNER_HEIGHT + 445, 833, BANNER_HEIGHT + 625), "我遺失物品", title_font, "white")
    _centered(draw, (833, BANNER_HEIGHT + 445, 1666, BANNER_HEIGHT + 625), "我撿到物品", title_font, "white")
    _centered(draw, (1666, BANNER_HEIGHT + 445, WIDTH, BANNER_HEIGHT + 625), "一般聊天", title_font, "white")

    # Search icon.
    cx, cy, radius = (416, BANNER_HEIGHT + 265, 92)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), width=30, outline="#caff77")
    draw.line((cx + 65, cy + 65, cx + 155, cy + 155), width=30, fill="#caff77")
    # Camera icon.
    cx = 1249
    draw.rounded_rectangle((cx - 140, BANNER_HEIGHT + 175, cx + 140, BANNER_HEIGHT + 355), radius=30, width=26, outline="white")
    draw.ellipse((cx - 62, BANNER_HEIGHT + 205, cx + 62, BANNER_HEIGHT + 329), width=23, outline="white")
    draw.rounded_rectangle((cx - 72, BANNER_HEIGHT + 140, cx + 42, BANNER_HEIGHT + 195), radius=14, fill="white")
    # Chat icon.
    cx = 2083
    draw.rounded_rectangle((cx - 145, BANNER_HEIGHT + 160, cx + 145, BANNER_HEIGHT + 350), radius=48, width=27, outline="white")
    draw.polygon(
        ((cx - 70, BANNER_HEIGHT + 345), (cx - 125, BANNER_HEIGHT + 425), (cx - 8, BANNER_HEIGHT + 350)),
        fill="white",
    )
    for offset in (-72, 0, 72):
        draw.ellipse((cx + offset - 14, BANNER_HEIGHT + 242, cx + offset + 14, BANNER_HEIGHT + 270), fill="white")

    ASSET_PATH.parent.mkdir(parents=True, exist_ok=True)
    image.save(ASSET_PATH, "PNG", optimize=True)


async def main() -> None:
    settings = get_settings()
    token = settings.line_channel_access_token
    if not token:
        raise SystemExit("LINE_CHANNEL_ACCESS_TOKEN is not configured")
    if not settings.line_liff_id:
        raise SystemExit("LINE_LIFF_ID is not configured")
    liff_url = f"https://liff.line.me/{settings.line_liff_id}"
    render_menu()
    headers = {"Authorization": f"Bearer {token}"}
    menu = {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,
        "name": MENU_NAME,
        "chatBarText": "LostLink AI 選單",
        "areas": [
            {
                "bounds": {"x": 0, "y": 0, "width": WIDTH, "height": BANNER_HEIGHT},
                "action": {"type": "uri", "uri": liff_url},
            },
            {
                "bounds": {"x": 0, "y": BANNER_HEIGHT, "width": 833, "height": BUTTON_HEIGHT},
                "action": {
                    "type": "postback",
                    "data": "action=start_lost",
                    "displayText": "我有東西不見了",
                },
            },
            {
                "bounds": {"x": 833, "y": BANNER_HEIGHT, "width": 833, "height": BUTTON_HEIGHT},
                "action": {
                    "type": "postback",
                    "data": "action=start_found",
                    "displayText": "我撿到東西了",
                },
            },
            {
                "bounds": {"x": 1666, "y": BANNER_HEIGHT, "width": 834, "height": BUTTON_HEIGHT},
                "action": {
                    "type": "postback",
                    "data": "action=continue_chat",
                    "displayText": "繼續聊天",
                    "inputOption": "openKeyboard",
                },
            },
        ],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        listed = await client.get("https://api.line.me/v2/bot/richmenu/list", headers=headers)
        listed.raise_for_status()
        existing = [
            item
            for item in listed.json().get("richmenus", [])
            if item.get("name") == MENU_NAME
        ]
        response = await client.post(
            "https://api.line.me/v2/bot/richmenu",
            headers={**headers, "Content-Type": "application/json"},
            json=menu,
        )
        response.raise_for_status()
        rich_menu_id = response.json()["richMenuId"]

        upload = await client.post(
            f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
            headers={**headers, "Content-Type": "image/png"},
            content=ASSET_PATH.read_bytes(),
        )
        upload.raise_for_status()

        default = await client.post(
            f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}",
            headers=headers,
        )
        default.raise_for_status()
        for old_menu in existing:
            old_id = old_menu.get("richMenuId")
            if old_id and old_id != rich_menu_id:
                removed = await client.delete(
                    f"https://api.line.me/v2/bot/richmenu/{old_id}",
                    headers=headers,
                )
                removed.raise_for_status()

    print(f"rich_menu_ready={rich_menu_id} image={ASSET_PATH.name}")


if __name__ == "__main__":
    asyncio.run(main())
