"""Interactive style and palette prompts, driven through injected read and write."""

import io
import unittest


def asker(*answers):
    """A ``read`` that returns the answers in order and raises EOFError when they run out."""
    queue = list(answers)
    prompts = []

    def read(prompt=""):
        prompts.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)

    return read, prompts


class PromptTests(unittest.TestCase):
    def ask(self, function, answers, **kwargs):
        read, _ = asker(*answers)
        out = []
        return function(read, out.append, **kwargs), "\n".join(out)

    def test_style_supply_my_own(self):
        from mascot_wardrobe.interfaces.prompts import ask_style

        value, text = self.ask(ask_style, ["1", "streetwear"], allow_keep=False)
        self.assertEqual(value, "streetwear")
        self.assertIn("supply my own", text)
        self.assertIn("generator choose", text)
        self.assertNotIn("keep current", text)

    def test_style_generator_choice_is_auto(self):
        from mascot_wardrobe.interfaces.prompts import ask_style

        value, _ = self.ask(ask_style, ["2"], allow_keep=False)
        self.assertEqual(value, "auto")

    def test_palette_supply_accepts_one_to_six_colours(self):
        from mascot_wardrobe.interfaces.prompts import ask_palette

        value, _ = self.ask(ask_palette, ["1", "#101014 #4920A8 #9B4DFF"], allow_keep=False)
        self.assertEqual(value, ["#101014", "#4920A8", "#9B4DFF"])
        value, _ = self.ask(ask_palette, ["2"], allow_keep=False)
        self.assertEqual(value, "auto")

    def test_invalid_answers_reask_then_fail_after_three(self):
        from mascot_wardrobe.interfaces.prompts import ask_palette, ask_style

        value, text = self.ask(ask_style, ["1", "grunge", "", "classic"], allow_keep=False)
        self.assertEqual(value, "classic")
        self.assertIn("Unknown style", text)
        with self.assertRaises(ValueError):
            self.ask(ask_style, ["1", "a", "b", "c", "classic"], allow_keep=False)
        with self.assertRaises(ValueError):
            self.ask(ask_palette, ["1", "#12", "red", "#fff", "#101014"], allow_keep=False)
        with self.assertRaises(ValueError):
            self.ask(ask_style, ["7", "x", "9", "classic"], allow_keep=False)

    def test_edit_offers_keep_current(self):
        from mascot_wardrobe.interfaces.prompts import KEEP, ask_palette, ask_style

        value, text = self.ask(ask_style, ["3"], allow_keep=True)
        self.assertIs(value, KEEP)
        self.assertIn("keep current", text)
        value, _ = self.ask(ask_palette, ["3"], allow_keep=True)
        self.assertIs(value, KEEP)
        with self.assertRaises(ValueError):
            self.ask(ask_style, ["3", "3", "3", "3"], allow_keep=False)

    def test_end_of_input_is_an_error_never_a_default(self):
        from mascot_wardrobe.interfaces.prompts import ask_palette, ask_style

        with self.assertRaisesRegex(ValueError, "end of input"):
            self.ask(ask_style, [], allow_keep=False)
        with self.assertRaisesRegex(ValueError, "end of input"):
            self.ask(ask_palette, ["1"], allow_keep=False)

    def test_prompts_use_the_injected_streams_only(self):
        from mascot_wardrobe.interfaces.prompts import ask_style

        read, prompts = asker("2")
        out = []
        ask_style(read, out.append, allow_keep=False)
        self.assertTrue(out)
        self.assertTrue(prompts)

    def test_interactive_needs_both_ttys_and_no_flag(self):
        from mascot_wardrobe.interfaces.prompts import is_interactive

        class Tty(io.StringIO):
            def isatty(self):
                return True

        tty, plain = Tty(), io.StringIO()
        self.assertTrue(is_interactive(False, tty, tty))
        self.assertFalse(is_interactive(True, tty, tty))
        self.assertFalse(is_interactive(False, plain, tty))
        self.assertFalse(is_interactive(False, tty, plain))
        self.assertFalse(is_interactive(False, None, tty))


class TemperaturePromptTests(unittest.TestCase):
    def ask(self, answers, **kwargs):
        from mascot_wardrobe.interfaces.prompts import ask_temperature

        read, _ = asker(*answers)
        out = []
        return ask_temperature(read, out.append, **kwargs), "\n".join(out)

    def test_accepts_celsius_including_decimals_and_negatives(self):
        self.assertEqual(self.ask(["12.5"])[0], 12.5)
        self.assertEqual(self.ask([" -7 "])[0], -7.0)

    def test_invalid_answers_reask_with_the_reason_then_fail_after_three(self):
        value, text = self.ask(["warm", "99", "21"])
        self.assertEqual(value, 21.0)
        self.assertIn("number", text)
        self.assertIn("between -90 and 65", text)
        with self.assertRaisesRegex(ValueError, "No valid temperature after 3 attempts"):
            self.ask(["x", "nan", "inf"])

    def test_blank_keeps_only_when_allowed(self):
        from mascot_wardrobe.interfaces.prompts import KEEP

        self.assertIs(self.ask([""], allow_keep=True)[0], KEEP)
        with self.assertRaises(ValueError):
            self.ask(["", "", ""])

    def test_end_of_input_is_not_a_default(self):
        from mascot_wardrobe.interfaces.prompts import EndOfInput

        with self.assertRaises(EndOfInput):
            self.ask([])


class YesNoPromptTests(unittest.TestCase):
    def ask(self, answers, default=False):
        from mascot_wardrobe.interfaces.prompts import ask_yes_no

        read, _ = asker(*answers)
        return ask_yes_no(read, lambda text: None, "Another piece?", default=default)

    def test_answers_and_defaults(self):
        self.assertTrue(self.ask(["y"]))
        self.assertTrue(self.ask(["YES"]))
        self.assertFalse(self.ask(["n"]))
        self.assertFalse(self.ask([""]))
        self.assertTrue(self.ask([""], default=True))

    def test_unclear_answers_reask_then_fail(self):
        self.assertTrue(self.ask(["maybe", "y"]))
        with self.assertRaisesRegex(ValueError, "yes or no"):
            self.ask(["a", "b", "c"])
