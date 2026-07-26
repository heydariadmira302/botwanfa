from types import SimpleNamespace

from botwanfa.apps.bot import failure_message


def test_failure_message_escapes_name_and_mentions_by_id() -> None:
    user = SimpleNamespace(id=123, full_name="<Admin & Player>")
    text = failure_message(
        user, item="\u548c\u503c19 20", reason="\u548c\u503c\u8303\u56f4\u4e3a3\u81f318"
    )
    assert "tg://user?id=123" in text
    assert "&lt;Admin &amp; Player&gt;" in text
    assert "\u672c\u6761\u6d88\u606f\u4e2d\u7684\u6295\u6ce8\u5747\u672a\u6263\u5206" in text
