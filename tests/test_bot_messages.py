from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from botwanfa.apps.bot import (
    admin_menu_markup,
    betting_unavailable_reason,
    failure_message,
    looks_like_bet,
)
from botwanfa.domain.state_machine import RoundStatus


def test_failure_message_escapes_name_and_mentions_by_id() -> None:
    user = SimpleNamespace(id=123, full_name="<Admin & Player>")
    text = failure_message(
        user, item="\u548c\u503c19 20", reason="\u548c\u503c\u8303\u56f4\u4e3a3\u81f318"
    )
    assert "tg://user?id=123" in text
    assert "&lt;Admin &amp; Player&gt;" in text
    assert "ID:" not in text
    assert "\u672c\u6761\u6d88\u606f\u4e2d\u7684\u6295\u6ce8\u5747\u672a\u6263\u5206" in text


def test_betting_window_reports_closed_and_expired_rounds() -> None:
    now = datetime.now(UTC)
    open_round = SimpleNamespace(
        status=RoundStatus.BETTING.value,
        betting_closes_at=now + timedelta(seconds=10),
    )
    closed_round = SimpleNamespace(
        status=RoundStatus.CLOSED.value,
        betting_closes_at=now - timedelta(seconds=1),
    )
    expired_round = SimpleNamespace(
        status=RoundStatus.BETTING.value,
        betting_closes_at=now - timedelta(seconds=1),
    )
    assert betting_unavailable_reason(open_round, now) is None
    assert "停止下注" in betting_unavailable_reason(closed_round, now)
    assert "停止下注" in betting_unavailable_reason(expired_round, now)
    assert "尚未开盘" in betting_unavailable_reason(None, now)


def test_bet_intent_detection_does_not_interrupt_normal_group_chat() -> None:
    assert looks_like_bet("大100")
    assert looks_like_bet("和值19 20")
    assert looks_like_bet("聊天内容，dd100")
    assert not looks_like_bet("大家晚上好")
    assert not looks_like_bet("今天讨论大小单双")


def test_admin_menu_has_expected_buttons() -> None:
    markup = admin_menu_markup()
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert "📊 运行总览" in labels
    assert "🎮 群管理" in labels
    assert "🔎 查询玩家" in labels
    assert "💳 玩家上下分" in labels
    assert "🏆 群排行榜" in labels
    assert "📈 数据报表" in labels
    assert "🧾 操作日志" in labels
    assert "💾 备份恢复" in labels
    assert "🧪 测试模式" in labels
    assert "📖 玩法说明" in labels


def test_admin_callback_data_fits_telegram_limit() -> None:
    markup = admin_menu_markup()
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert all(value is not None and len(value.encode()) <= 64 for value in callbacks)
