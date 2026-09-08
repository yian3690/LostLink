import asyncio
import io

from PIL import Image

from app.core.config import Settings
from app.services.ai import EmbeddingService


async def main() -> None:
    service = EmbeddingService(Settings(demo_mode=False))
    print(f"device={service._get_device()}", flush=True)
    e5 = await service.encode_e5("黑色 AirPods 在圖書館不見了")
    print(f"e5_dim={len(e5)}", flush=True)
    siglip_text = await service.encode_siglip_text("a photo of black wireless earphones")
    print(f"siglip_text_dim={len(siglip_text)}", flush=True)

    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), color="black").save(buffer, "JPEG")
    siglip_image = await service.encode_siglip_image(buffer.getvalue())
    print(f"siglip_image_dim={len(siglip_image)}", flush=True)
    print("models_ready=true", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
