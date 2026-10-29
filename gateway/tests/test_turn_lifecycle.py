"""工单 05:待定轮 → 确定轮 / 已取代;只有确定轮写入事实库;写入可重试、可补写。"""

from typing import Any

from conftest import Harness


def _u(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _a(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def test_turn_settles_when_next_message_arrives_with_it_unchanged(harness: Harness) -> None:
    harness.upstream.reply = "生日快乐提前说"
    harness.chat([_u("我下周三生日")])
    harness.tick()
    assert harness.facts.writes == []  # 还是待定轮,不写

    harness.chat([_u("我下周三生日"), _a("生日快乐提前说"), _u("你记住了吗")])
    harness.tick()

    statuses = [t["status"] for t in harness.turns()]
    assert statuses == ["settled", "pending"]
    [write] = harness.facts.writes
    assert "我下周三生日" in write["transcript"]
    assert "你记住了吗" not in write["transcript"]


def test_regenerated_reply_supersedes_the_old_version(harness: Harness) -> None:
    harness.upstream.reply = "不太好的回复"
    harness.chat([_u("讲个笑话")])
    harness.upstream.reply = "好一点的回复"
    harness.chat([_u("讲个笑话")])  # Kelivo 重新生成:同样的历史再发一次
    harness.chat([_u("讲个笑话"), _a("好一点的回复"), _u("哈哈")])
    harness.tick()

    turns = harness.turns()
    assert [t["status"] for t in turns] == ["superseded", "settled", "pending"]
    assert turns[0]["assistant_text"] == "不太好的回复"  # 旧版本留在原文日志里
    assert all("不太好的回复" not in w["transcript"] for w in harness.facts.writes)


def test_edited_message_supersedes_the_old_version(harness: Harness) -> None:
    harness.chat([_u("我下周三生日")])
    harness.chat([_u("我下周四生日")])  # 在 Kelivo 里编辑了刚才那句
    harness.chat([_u("我下周四生日"), _a("好的"), _u("嗯")])
    harness.tick()

    assert [t["status"] for t in harness.turns()] == ["superseded", "settled", "pending"]
    assert all("周三" not in w["transcript"] for w in harness.facts.writes)
    assert "周四" in harness.facts.writes[-1]["transcript"]


def test_fact_writes_for_one_episode_share_one_document(harness: Harness) -> None:
    harness.chat([_u("一")])
    harness.chat([_u("一"), _a("好的"), _u("二")])
    harness.chat([_u("一"), _a("好的"), _u("二"), _a("好的"), _u("三")])
    harness.tick()

    episode_ids = {w["episode_id"] for w in harness.facts.writes}
    assert len(episode_ids) == 1
    assert "二" in harness.facts.writes[-1]["transcript"]


def test_failed_store_write_is_retried_on_next_tick(harness: Harness) -> None:
    harness.chat([_u("我叫小鱼")])
    harness.chat([_u("我叫小鱼"), _a("好的"), _u("嗯")])
    harness.facts.fail = True
    harness.tick()
    assert harness.facts.writes == []

    harness.facts.fail = False
    harness.tick()

    assert "我叫小鱼" in harness.facts.writes[-1]["transcript"]


def test_pending_writes_survive_a_gateway_restart(harness: Harness) -> None:
    harness.chat([_u("我叫小鱼")])
    harness.chat([_u("我叫小鱼"), _a("好的"), _u("嗯")])
    harness.facts.fail = True
    harness.tick()
    harness.facts.fail = False

    harness.restart()
    harness.tick()

    assert "我叫小鱼" in harness.facts.writes[-1]["transcript"]
