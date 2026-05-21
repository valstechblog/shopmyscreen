"""
Clothing detection — three backends:

  ClothingDetector     Claude Vision API  (paid, most accurate)
  BLIPClothingDetector Local BLIP captioning  (free, good descriptions)
  FashionCLIPDetector  Local fashion-clip zero-shot  (free, category-focused)

All return a list of dicts with keys:
  item_type, color, style, search_query, character
"""

import base64
import io
import json
import logging
import os
import re

# Suppress the "unauthenticated requests" noise from huggingface_hub
logging.getLogger("huggingface_hub.utils._headers").setLevel(logging.ERROR)

# Disable tokenizers' background forking — silences the
# "leaked semaphore objects" warning on shutdown.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import anthropic


# ── Common clothing vocab ──────────────────────────────────────────────────

CLOTHING_KEYWORDS = [
    "jacket", "coat", "blazer", "cardigan", "vest", "windbreaker", "parka",
    "dress", "skirt", "gown", "mini dress", "midi dress", "maxi dress",
    "shirt", "blouse", "top", "sweater", "pullover", "hoodie", "t-shirt",
    "tshirt", "polo", "bodysuit", "crop top", "tank top",
    "pants", "jeans", "trousers", "shorts", "leggings", "sweatpants",
    "sneakers", "boots", "heels", "sandals", "loafers", "oxfords", "shoes",
    "handbag", "bag", "purse", "backpack", "clutch",
    "hat", "cap", "beanie", "beret",
    "scarf", "gloves", "belt", "sunglasses",
    "suit", "jumpsuit", "romper", "overalls", "tuxedo",
]

CLOTHING_CATEGORIES = [
    "jacket", "coat", "blazer", "cardigan", "vest",
    "dress", "skirt", "gown",
    "shirt", "blouse", "top", "sweater", "hoodie", "t-shirt", "crop top",
    "pants", "jeans", "trousers", "shorts", "leggings",
    "sneakers", "boots", "heels", "sandals", "shoes",
    "handbag", "backpack", "clutch",
    "hat", "cap", "scarf", "belt", "sunglasses",
    "suit", "jumpsuit",
]

COLORS = [
    "black", "white", "red", "blue", "green", "yellow", "orange",
    "purple", "pink", "brown", "grey", "gray", "navy", "beige", "cream",
    "gold", "silver", "burgundy", "maroon", "teal", "olive", "coral",
]

ITEM_EMOJIS = {
    "jacket": "🧥", "coat": "🧥", "blazer": "🧥", "cardigan": "🧥", "vest": "🧥",
    "windbreaker": "🧥", "parka": "🧥",
    "dress": "👗", "skirt": "👗", "gown": "👗",
    "shirt": "👕", "top": "👕", "blouse": "👕", "sweater": "👕",
    "t-shirt": "👕", "tshirt": "👕", "hoodie": "👕", "bodysuit": "👕",
    "pants": "👖", "jeans": "👖", "trousers": "👖", "shorts": "👖",
    "leggings": "👖", "sweatpants": "👖",
    "shoes": "👟", "sneakers": "👟", "boots": "👢", "heels": "👠",
    "sandals": "👡", "loafers": "👞", "oxfords": "👞",
    "bag": "👜", "handbag": "👜", "purse": "👛", "backpack": "🎒", "clutch": "👛",
    "hat": "🧢", "cap": "🧢", "beanie": "🧣", "beret": "🧢",
    "scarf": "🧣", "gloves": "🧤", "belt": "👔", "sunglasses": "🕶️",
    "suit": "🤵", "tuxedo": "🤵", "jumpsuit": "👗",
}

def item_emoji(item_type: str) -> str:
    lower = item_type.lower()
    for key, emoji in ITEM_EMOJIS.items():
        if key in lower:
            return emoji
    return "🛍️"


# ── Screen capture ─────────────────────────────────────────────────────────

def capture_screen_pil(max_w=1280, max_h=720):
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception:
        from PIL import ImageGrab
        img = ImageGrab.grab()
    img.thumbnail((max_w, max_h))
    return img


# ── 1. Claude Vision ───────────────────────────────────────────────────────

_CLAUDE_PROMPT = """\
Look at this screenshot from a TV show or film. Identify all distinct clothing and fashion items \
worn by visible characters.

For each item return a JSON object with these fields:
  item_type   – concise name, e.g. "leather biker jacket", "floral midi dress"
  color       – primary color or pattern, e.g. "black", "navy & white stripe"
  style       – 3-6 word style description, e.g. "vintage moto", "smart casual"
  search_query – a specific, shoppable search string to find this exact item
  character   – who is wearing it (character name or positional, e.g. "woman on left")

Return ONLY a JSON array. If no people or clothing are visible, return [].
Include at most 6 items. Focus on clearly visible pieces."""


class ClothingDetector:
    def __init__(self):
        self.client = anthropic.Anthropic()

    def detect(self, img=None):
        if img is None:
            img = capture_screen_pil()
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        image_b64 = base64.b64encode(buf.getvalue()).decode()

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                    {"type": "text", "text": _CLAUDE_PROMPT},
                ],
            }],
        )
        raw = response.content[0].text.strip()
        if "```" in raw:
            for part in raw.split("```"):
                s = part.strip().lstrip("json").strip()
                if s.startswith("["):
                    raw = s
                    break
        try:
            items = json.loads(raw)
            return items if isinstance(items, list) else []
        except json.JSONDecodeError:
            return []


# ── 2. BLIP Local ──────────────────────────────────────────────────────────

def _parse_blip_caption(caption: str) -> list[dict]:
    """Extract clothing items from a BLIP caption string."""
    text = caption.lower()

    # Strip common prompt prefixes
    for prefix in [
        "a person wearing ", "a woman wearing ", "a man wearing ",
        "person wearing ", "someone wearing ", "wearing ",
    ]:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break

    items = []
    seen: set[str] = set()
    segments = re.split(r"\band\b|\bwith\b|,|;", text)

    for seg in segments:
        seg = seg.strip()
        for clothing in sorted(CLOTHING_KEYWORDS, key=len, reverse=True):
            if re.search(r"\b" + re.escape(clothing) + r"\b", seg) and clothing not in seen:
                seen.add(clothing)
                color = ""
                for c in sorted(COLORS, key=len, reverse=True):
                    if re.search(r"\b" + c + r"\b", seg):
                        color = c
                        break
                search_q = f"{color} {clothing}".strip()
                items.append({
                    "item_type": clothing,
                    "color": color,
                    "style": "",
                    "search_query": search_q,
                    "character": "person on screen",
                })
                break

    return items


def _is_hf_cached(model_id: str) -> bool:
    """Return True if the model's config.json is already in the HF cache on disk."""
    try:
        from huggingface_hub import try_to_load_from_cache
        result = try_to_load_from_cache(model_id, "config.json")
        return isinstance(result, str)
    except Exception:
        return False


class BLIPClothingDetector:
    """Free, local — uses Salesforce/blip-image-captioning-large."""

    MODEL_ID = "Salesforce/blip-image-captioning-large"

    def __init__(self):
        self._model = None
        self._processor = None

    def is_ready(self) -> bool:
        """True if model is already loaded in memory."""
        return self._model is not None

    def is_cached(self) -> bool:
        """True if model files are already on disk (no download needed)."""
        return self.is_ready() or _is_hf_cached(self.MODEL_ID)

    def _load(self):
        if self._model is None:
            from transformers import BlipForConditionalGeneration, BlipProcessor
            self._processor = BlipProcessor.from_pretrained(self.MODEL_ID)
            self._model = BlipForConditionalGeneration.from_pretrained(self.MODEL_ID)

    def _caption(self, img, prompt: str) -> str:
        import torch
        inputs = self._processor(img, text=prompt, return_tensors="pt")
        with torch.no_grad():
            out = self._model.generate(**inputs, max_new_tokens=120)
        return self._processor.decode(out[0], skip_special_tokens=True)

    def detect(self, img=None):
        if img is None:
            img = capture_screen_pil()
        self._load()
        caption = self._caption(img, "a person wearing")
        return _parse_blip_caption(caption)


# ── 3. FashionCLIP Local ───────────────────────────────────────────────────

class FashionCLIPDetector:
    """Free, local — uses patrickjohncyh/fashion-clip zero-shot classification."""

    MODEL_ID = "patrickjohncyh/fashion-clip"
    TOP_K = 5

    def __init__(self):
        self._model = None
        self._processor = None

    def is_ready(self) -> bool:
        return self._model is not None

    def is_cached(self) -> bool:
        return self.is_ready() or _is_hf_cached(self.MODEL_ID)

    def _load(self):
        if self._model is None:
            from transformers import CLIPModel, CLIPProcessor
            self._processor = CLIPProcessor.from_pretrained(self.MODEL_ID)
            self._model = CLIPModel.from_pretrained(self.MODEL_ID)

    def _classify(self, img, texts: list[str]) -> list[float]:
        import torch
        inputs = self._processor(text=texts, images=img,
                                 return_tensors="pt", padding=True)
        with torch.no_grad():
            outputs = self._model(**inputs)
        probs = outputs.logits_per_image.softmax(dim=1)[0]
        return probs.tolist()

    def detect(self, img=None):
        if img is None:
            img = capture_screen_pil()
        self._load()

        # Step 1: detect clothing categories
        cat_prompts = [f"a photo of a {c}" for c in CLOTHING_CATEGORIES]
        cat_probs = self._classify(img, cat_prompts)
        ranked = sorted(zip(CLOTHING_CATEGORIES, cat_probs), key=lambda x: -x[1])
        top_categories = [cat for cat, _ in ranked[:self.TOP_K]]

        # Step 2: for each category, identify best-matching color
        items = []
        for category in top_categories:
            color_prompts = [f"a {c} {category}" for c in COLORS]
            color_probs = self._classify(img, color_prompts)
            best_color = COLORS[color_probs.index(max(color_probs))]
            search_q = f"{best_color} {category}"
            items.append({
                "item_type": category,
                "color": best_color,
                "style": "fashion-clip",
                "search_query": search_q,
                "character": "person on screen",
            })

        return items
