import re


class SpeechTextError(ValueError):
    pass


class SpeechTextNormalizer:
    _code_fence = re.compile(r"```(?:[^\n]*)\n?(.*?)```", re.DOTALL)
    _inline_code = re.compile(r"`([^`]*)`")
    _url = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
    _metadata = re.compile(r"\[(?:provider|model|request_id|event_id|scene_id)=[^\]]*\]", re.IGNORECASE)
    _spaces = re.compile(r"\s+")

    def __init__(self, max_chars: int = 240) -> None:
        self.max_chars = max_chars

    def normalize(self, text: str) -> str:
        working = self._code_fence.sub(lambda match: match.group(1), text)
        working = self._inline_code.sub(lambda match: match.group(1), working)
        working = self._url.sub("", working)
        working = self._metadata.sub("", working)
        # Remove markup characters only. The words inside *emphasis* and
        # **strong emphasis** are deliberately retained verbatim.
        working = re.sub(r"(?<!\\)[*_~#>]", "", working)
        working = working.replace("\\*", "*").replace("\\_", "_")
        working = self._spaces.sub(" ", working).strip()
        if len(working) > self.max_chars:
            cut = working[: self.max_chars + 1]
            if len(cut) > self.max_chars and not cut[self.max_chars].isspace():
                cut = cut[: self.max_chars].rsplit(" ", 1)[0]
            else:
                cut = cut[: self.max_chars]
            working = cut.rstrip(" ,;:-")
        if not working:
            raise SpeechTextError("speech text is empty after normalization")
        return working
