"""
Защита от промпт-инъекций.
Эвристики — вспомогательный сигнал. Основная защита архитектурная:
neutralize_data_block + read-after-write block.
"""

from __future__ import annotations

import re
import unicodedata


_INVISIBLE = re.compile(
    "["
    "\u00ad"
    "\u180e"
    "\u200b-\u200f"
    "\u202a-\u202e"
    "\u2060-\u206f"
    "\ufe00-\ufe0f"
    "\ufeff"
    "\ufff9-\ufffb"
    "\U000E0100-\U000E01EF"
    "]"
)

_HOMOGLYPHS = str.maketrans({
    "а":"a","в":"b","е":"e","к":"k","м":"m","н":"h","о":"o","р":"p",
    "с":"c","т":"t","у":"y","х":"x","і":"i","ј":"j","ѕ":"s","һ":"h",
    "А":"A","В":"B","Е":"E","К":"K","М":"M","Н":"H","О":"O","Р":"P",
    "С":"C","Т":"T","У":"Y","Х":"X","І":"I","Ј":"J","Ѕ":"S","Һ":"H",
    "α":"a","ο":"o","ρ":"p","ν":"v","τ":"t",
    "Α":"A","Ο":"O","Ρ":"P","Ν":"N","Τ":"T",
    "ａ":"a","ｅ":"e",
    "ı":"i","ⅰ":"i","ⅱ":"ii","ⅲ":"iii",
})

_TAG_RE = re.compile(
    r"</?(?:"
    r"tool_responses|tool_response|"
    r"tool_results|tool_result|"
    r"tool_outputs|tool_output|"
    r"tool_calls|tool_call|"
    r"tool_uses|tool_use|"
    r"tool|"
    r"function_calls|function_call|"
    r"function_results|function_result|"
    r"results|result|"
    r"system|assistant|user|instruction|"
    r"response|output|prompt|context|thought"
    r")\b[^>]*>",
    re.IGNORECASE,
)

_FENCE_RE = re.compile(r"`{3,}")

_SUSPICIOUS = [
    re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior|above)", re.I),
    re.compile(r"forget\s+(?:everything|all|previous)", re.I),
    re.compile(r"new\s+(?:system\s+)?(?:instructions?|prompt|rules)", re.I),
    re.compile(r"reveal\s+(?:your\s+)?(?:system\s+)?prompt", re.I),
    re.compile(r"show\s+(?:me\s+)?(?:your\s+)?(?:system\s+)?prompt", re.I),
    re.compile(r"\byou\s+are\s+now\s+(?:a|an)\b", re.I),
    re.compile(r"act\s+as\s+(?:a|an)\s+(?:dan|jailbreak|developer\s+mode)", re.I),
]

_DANGEROUS_PAYLOAD = [
    re.compile(r"\bcurl\s+[^\n]{0,200}\|\s*(?:ba|z|da)?sh\b", re.I),
    re.compile(r"\bwget\s+[^\n]{0,200}\|\s*(?:ba|z|da)?sh\b", re.I),
    re.compile(r"\bchmod\s+\+x\b", re.I),
    re.compile(r"\brm\s+-rf\s+/(?!\w)", re.I),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;:"),
    re.compile(r"\beval\s*\(", re.I),
    re.compile(r"\bos\.system\s*\(", re.I),
    re.compile(r"\bsubprocess\.(?:run|Popen|call|check_output)\s*\(", re.I),
    re.compile(r"\bbase64\s+-d\b[^\n]{0,100}\|\s*(?:ba|z)?sh\b", re.I),
]


class InjectionGuard:

    def normalize(self, text: str) -> str:
        if not text:
            return ""
        text = unicodedata.normalize("NFKC", text)
        text = _INVISIBLE.sub("", text)
        text = text.translate(_HOMOGLYPHS)
        return text

    def is_suspicious(self, text: str) -> bool:
        if not text:
            return False
        if _INVISIBLE.search(text):
            return True
        n = self.normalize(text)
        return any(p.search(n) for p in _SUSPICIOUS)

    def neutralize_data_block(self, text: str) -> str:
        if not text:
            return ""
        text = _TAG_RE.sub(
            lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"),
            text,
        )
        text = _FENCE_RE.sub("'''", text)
        return text

    def scan_payload(self, content: str) -> str | None:
        if not content:
            return None
        n = self.normalize(content)
        for p in _DANGEROUS_PAYLOAD:
            if p.search(n):
                return p.pattern
        return None
