"""Tests for Hindi number-words (used to voice prices/quantities in TTS)."""

from dukaan import numwords


def test_to_hindi_words():
    assert numwords.to_hindi_words(0) == "शून्य"
    assert numwords.to_hindi_words(5) == "पाँच"
    assert numwords.to_hindi_words(28) == "अट्ठाईस"
    assert numwords.to_hindi_words(100) == "एक सौ"
    assert numwords.to_hindi_words(528) == "पाँच सौ अट्ठाईस"
    assert numwords.to_hindi_words(1250) == "एक हज़ार दो सौ पचास"
    assert numwords.to_hindi_words(14263) == "चौदह हज़ार दो सौ तिरसठ"
    assert numwords.to_hindi_words(100000) == "एक लाख"


def test_digits_to_words():
    assert numwords.digits_to_words("₹528") == "पाँच सौ अट्ठाईस रुपये"
    assert "रुपये" in numwords.digits_to_words("₹14,263")
    assert "इकतीस" in numwords.digits_to_words("31 units bike")
    # text with no digits is untouched
    assert numwords.digits_to_words("नमस्ते जी") == "नमस्ते जी"
