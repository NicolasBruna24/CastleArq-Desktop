from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ThinkSplit:
    raw_output: str
    final_text: str
    reasoning_text: str

    @property
    def has_reasoning(self) -> bool:
        return bool(self.reasoning_text.strip())


_THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def split_think_output(text: str) -> ThinkSplit:
    if not isinstance(text, str):
        text = "" if text is None else str(text)

    opens = text.count("<think>")
    closes = text.count("</think>")
    matches = list(_THINK_PATTERN.finditer(text))

    if opens == 0 and closes == 0:
        return ThinkSplit(raw_output=text, final_text=text, reasoning_text="")

    if opens != closes or len(matches) != opens:
        return ThinkSplit(raw_output=text, final_text=text, reasoning_text="")

    reasoning_parts = [match.group(1) for match in matches]
    final_text = _THINK_PATTERN.sub("", text)
    reasoning_text = "\n\n".join(reasoning_parts)
    return ThinkSplit(raw_output=text, final_text=final_text, reasoning_text=reasoning_text)
