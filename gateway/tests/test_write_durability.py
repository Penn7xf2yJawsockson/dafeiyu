"""工单 05 剩余:写入失败按指数退避重试;启动时与原文日志对账补写。

- 退避:第 N 次失败后等 30s·2^(N-1),最长 1 小时。记忆库长时间不可用时不每 30 秒撞一次。
- 对账:确定轮和「写入任务入队」以前不在同一个事务里,中间崩溃会让这一段永远不写进记忆库。
  启动时对照原文日志:有确定轮但事实库还没写到的段补一个写入任务;已关闭但从没写过情感库的段补一个。
"""

import sqlite3
from typing import Any

from conftest import Harness


def _u(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _a(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def _settled_turn(harness: Harness) -> None:
    harness.chat([_u("我叫小鱼")])
    harness.chat([_u("我叫小鱼"), _a("好的"), _u("嗯")])  # 第一轮确定,事实写入任务入队


def _outbox(harness: Harness) -> list[tuple[str, int, int]]:
    db = sqlite3.connect(harness.data_dir / "transcript.sqlite3")
    rows = db.execute("SELECT kind, done, attempts FROM outbox ORDER BY seq").fetchall()
    db.close()
    return [(r[0], r[1], r[2]) for r in rows]


def test_failed_write_waits_before_retrying(harness: Harness) -> None:
    _settled_turn(harness)
    harness.facts.fail = True
    harness.tick()                       # 第 1 次失败
    harness.facts.fail = False

    harness.clock.advance(seconds=10)
    harness.tick()
    assert harness.facts.writes == []    # 还没到 30 秒,不重试

    harness.clock.advance(seconds=25)
    harness.tick()
    assert "我叫小鱼" in harness.facts.writes[-1]["transcript"]


def test_retry_delay_doubles_and_is_capped(harness: Harness) -> None:
    _settled_turn(harness)
    harness.facts.fail = True
    waits = []
    for _ in range(9):
        before = _outbox(harness)[0][2]
        waited = 0
        while _outbox(harness)[0][2] == before:  # 一秒一秒往前推,看多久后才再次尝试
            harness.tick()
            if _outbox(harness)[0][2] != before:
                break
            harness.clock.advance(seconds=30)
            waited += 30
        waits.append(waited)

    assert waits[:4] == [0, 30, 60, 120]
    assert max(waits) == 3600


def test_successful_write_after_failures_clears_the_job(harness: Harness) -> None:
    _settled_turn(harness)
    harness.facts.fail = True
    harness.tick()
    harness.facts.fail = False
    harness.clock.advance(minutes=1)
    harness.tick()

    assert _outbox(harness) == [("facts", 1, 2)]


def test_backoff_survives_a_restart(harness: Harness) -> None:
    _settled_turn(harness)
    harness.facts.fail = True
    harness.tick()
    harness.facts.fail = False

    harness.restart()
    harness.tick()
    assert harness.facts.writes == []

    harness.clock.advance(seconds=31)
    harness.tick()
    assert "我叫小鱼" in harness.facts.writes[-1]["transcript"]


def _lose_the_queue(harness: Harness) -> None:
    """模拟旧版本在「轮已确定」和「任务入队」之间崩溃:日志里有确定轮,队列里没有任务。"""
    db = sqlite3.connect(harness.data_dir / "transcript.sqlite3")
    db.execute("DELETE FROM outbox")
    db.commit()
    db.close()


def test_restart_reconciles_settled_turns_that_never_reached_the_fact_store(harness: Harness) -> None:
    _settled_turn(harness)
    _lose_the_queue(harness)

    harness.restart()
    harness.tick()

    assert "我叫小鱼" in harness.facts.writes[-1]["transcript"]


def test_restart_reconciles_closed_episode_never_written_to_affect(harness: Harness) -> None:
    _settled_turn(harness)
    harness.clock.advance(minutes=31)
    harness.tick()                       # 段结束
    harness.affect.writes.clear()
    db = sqlite3.connect(harness.data_dir / "transcript.sqlite3")
    db.execute("DELETE FROM outbox WHERE kind = 'affect'")
    db.commit()
    db.close()

    harness.restart()
    harness.tick()

    assert "我叫小鱼" in harness.affect.writes[-1]["transcript"]


def test_reconcile_does_not_rewrite_what_is_already_written(harness: Harness) -> None:
    _settled_turn(harness)
    harness.clock.advance(minutes=31)
    harness.tick()
    facts, affect = len(harness.facts.writes), len(harness.affect.writes)

    harness.restart()
    harness.tick()
    harness.restart()
    harness.tick()

    assert (len(harness.facts.writes), len(harness.affect.writes)) == (facts, affect)


def test_reconcile_catches_a_later_turn_whose_job_was_lost(harness: Harness) -> None:
    """这一段之前写过一次事实库,后来又确定了一轮,但那一轮的任务丢了:要补写,带上新的那一轮。"""
    _settled_turn(harness)
    harness.tick()
    harness.chat([_u("我叫小鱼"), _a("好的"), _u("嗯"), _a("嗯嗯"), _u("我住杭州")])  # 第二轮确定
    db = sqlite3.connect(harness.data_dir / "transcript.sqlite3")
    db.execute("DELETE FROM outbox WHERE done = 0")
    db.commit()
    db.close()

    harness.restart()
    harness.tick()

    assert "用户:嗯" in harness.facts.writes[-1]["transcript"]
