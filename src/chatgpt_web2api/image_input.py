"""Bounded, local-only image inputs for chat completions.

Images are supplied as data URLs, never fetched from user-selected URLs.
Only the last user message may introduce images. Conversation IDs retain
earlier attachments on the website without uploading them again.
"""
from __future__ import annotations

import base64
import binascii
import io
import warnings
from dataclasses import dataclass, field

from PIL import Image, UnidentifiedImageError

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 6 * 1024 * 1024
MAX_PIXELS = 20_000_000
FORMATS = {"image/png": ("PNG", ".png"), "image/jpeg": ("JPEG", ".jpg"), "image/webp": ("WEBP", ".webp")}


class ImageInputError(ValueError):
    """Invalid client input; no browser mutation has occurred."""


class ImageUploadError(RuntimeError):
    """Upload could not be verified; the prompt must not be sent."""


class ImageUploadTimeout(ImageUploadError):
    """The upload deadline expired before any prompt was sent."""


@dataclass(frozen=True)
class ImageInput:
    mime: str
    data: bytes = field(repr=False)

    @property
    def suffix(self) -> str:
        return FORMATS[self.mime][1]


def decode_image(part: dict) -> ImageInput:
    spec = part.get("image_url")
    if not isinstance(spec, dict) or not isinstance(spec.get("url"), str):
        raise ImageInputError("image_url must contain a string url")
    if spec.get("detail", "auto") != "auto":
        raise ImageInputError("Only image_url.detail=auto is supported; the website controls image processing")
    url = spec["url"]
    header, sep, encoded = url.partition(",")
    valid = {f"data:{mime};base64": mime for mime in FORMATS}
    if not sep or header not in valid:
        raise ImageInputError("Use a Base64 data URL for PNG, JPEG or WebP; remote URLs and file paths are not supported")
    if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ImageInputError("Each image must be at most 4 MiB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ImageInputError("Invalid image Base64") from None
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageInputError("Each image must be nonempty and at most 4 MiB")
    mime = valid[header]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                if img.format != FORMATS[mime][0]:
                    raise ImageInputError("Image bytes do not match the declared MIME type")
                if img.width * img.height > MAX_PIXELS:
                    raise ImageInputError("Each image must be at most 20 megapixels")
                if getattr(img, "n_frames", 1) != 1:
                    raise ImageInputError("Animated images are not supported")
                img.verify()
            # verify() alone does not decode all JPEG pixel data.
            with Image.open(io.BytesIO(data)) as img:
                img.load()
    except ImageInputError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageInputError("Invalid or oversized image") from None
    return ImageInput(mime, data)


def normalize_messages(messages) -> tuple[list[dict], list[ImageInput]]:
    if not isinstance(messages, list) or not messages or any(not isinstance(m, dict) for m in messages):
        raise ImageInputError("messages must be a nonempty array of objects")
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    normalized, images = [], []
    total = 0
    for index, msg in enumerate(messages):
        content = msg.get("content", "")
        if isinstance(content, list):
            texts = []
            for part in content:
                if not isinstance(part, dict):
                    raise ImageInputError("Content parts must be objects")
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
                elif part.get("type") == "image_url":
                    if msg.get("role") != "user" or index != last_user:
                        raise ImageInputError("Images are supported only in the last user message; use conversation_id for follow-up questions")
                    if len(images) >= MAX_IMAGES:
                        raise ImageInputError("At most 4 images per request")
                    img = decode_image(part)
                    total += len(img.data)
                    if total > MAX_TOTAL_BYTES:
                        raise ImageInputError("Combined image size must be at most 6 MiB")
                    images.append(img)
                else:
                    raise ImageInputError("Supported content parts are text and image_url")
            content = "\n".join(texts)
            if index == last_user and images and not content.strip():
                content = "Describe the attached image(s)."
        elif not isinstance(content, str):
            raise ImageInputError("Message content must be text or an array of content parts")
        normalized.append({**msg, "content": content})
    return normalized, images
