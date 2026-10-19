"""비밀값 마스킹 테스트 (T01)."""
import pytest

import config


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("50000000-01", "*******0-01"),
        ("12345678", "****5678"),
        ("abcd", "****"),
        ("abc", "***"),
        ("", ""),
        (None, ""),
    ],
)
def test_mask_keeps_only_tail(raw, expected):
    assert config.mask(raw) == expected


def test_mask_hides_prefix():
    """원문 앞부분이 결과에 남지 않아야 한다."""
    secret = "SECRETKEY1234abcd"
    masked = config.mask(secret)
    assert "SECRETKEY" not in masked
    assert masked.endswith("abcd")


def test_mask_accepts_non_string():
    assert config.mask(123456789) == "*****6789"
