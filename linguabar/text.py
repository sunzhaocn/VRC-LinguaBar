"""Single-line text and UTF-16 chatbox limits, independent of Windows and Qt."""
from .config import MAX_CHARS


def clip_text(text: str, limit: int = MAX_CHARS) -> str:
    """Conservative UTF-16 budget, matching Qt 5; never split a surrogate pair.

    VRChat documents 144 characters but not its counting algorithm. UTF-16
    budgeting can accept fewer emoji than a Unicode-code-point limit.
    """
    text = text.replace("\x00", "").replace("\r", " ").replace("\n", " ")
    # Strip isolated surrogates that can arise from a Qt maxLength boundary.
    clean = text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "ignore")
    return clean.encode("utf-16-le")[:limit * 2].decode("utf-16-le", "ignore")


def text_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2
