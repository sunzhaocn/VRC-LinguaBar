import unittest

from linguabar.text import clip_text, text_units


class TextTests(unittest.TestCase):
    def test_multilingual_text_retains_content_and_uses_utf16_budget(self):
        text = "中文 日本語 café 😀"
        self.assertEqual(clip_text(text), text)
        self.assertEqual(text_units("中😀a"), 4)

    def test_emoji_at_limit_is_kept_whole_or_omitted(self):
        self.assertEqual(clip_text("a" * 142 + "😀尾"), "a" * 142 + "😀")
        self.assertEqual(clip_text("a" * 143 + "😀尾"), "a" * 143)
        self.assertEqual(clip_text("😀" * 73), "😀" * 72)
        self.assertEqual(clip_text("😀", limit=1), "")

    def test_pasted_lines_and_nuls_become_safe_single_line_text(self):
        self.assertEqual(clip_text("中\x00文\r\n日本語\nEnglish"),
                         "中文  日本語 English")

    def test_incomplete_qt_surrogates_are_removed_without_losing_neighbors(self):
        self.assertEqual(clip_text("前\ud83d後\ude00尾"), "前後尾")
        self.assertEqual(clip_text("\ud83d\ude00"), "😀")
        self.assertEqual(clip_text("文字", limit=0), "")


if __name__ == "__main__":
    unittest.main()
