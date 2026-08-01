import base64
import io
import pickle
from typing import Callable, Mapping, Optional

import aiohttp
from fastapi import HTTPException
from PIL.Image import Image

from manga_translator import Config

NotifyType = Optional[Callable[[int, Optional[bytes]], None]]


def _encode_image(image: Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _encode_config(config: Config) -> dict:
    return config.model_dump(mode="json")


def _encode_attributes(image, config: Config | None) -> dict:
    if isinstance(image, dict) and "images" in image:
        return {
            "images": [_encode_image(item) for item in image["images"]],
            "config": _encode_config(image["config"]),
            "batch_size": image.get("batch_size"),
        }
    return {"image": _encode_image(image), "config": _encode_config(config)}


async def fetch_data_stream(url, image: Image, config: Config, sender: NotifyType, headers: Optional[Mapping[str, str]] = None):
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=_encode_attributes(image, config), headers=headers) as response:
            if response.status == 200:
                await process_stream(response, sender)
            else:
                raise HTTPException(response.status, detail=await response.text())

async def fetch_data(url, image: Image, config: Config | None = None, headers: Optional[Mapping[str, str]] = None):
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=_encode_attributes(image, config), headers=headers) as response:
            if response.status == 200:
                return pickle.loads(await response.read())
            else:
                raise HTTPException(response.status, detail=await response.text())

async def process_stream(response, sender: NotifyType):
    buffer = b''

    async for chunk in response.content.iter_any():
        if chunk:
            buffer += chunk
            buffer = handle_buffer(buffer, sender)



def handle_buffer(buffer, sender: NotifyType):
    while len(buffer) >= 5:
        status, expected_size = extract_header(buffer)

        if len(buffer) >= 5 + expected_size:
            data = buffer[5:5 + expected_size]
            sender(status, data)
            buffer = buffer[5 + expected_size:]
        else:
            break
    return buffer


def extract_header(buffer):
    """Extract the status and expected size from the buffer."""
    status = int.from_bytes(buffer[0:1], byteorder='big')
    expected_size = int.from_bytes(buffer[1:5], byteorder='big')
    return status, expected_size
