"""网关世界书:按关键词确定性注入的条目(工单 09,ADR-0003)。

文件格式与 SillyTavern 1.17 的世界书 JSON 相同(`{"entries": {"<uid>": {...}}}`),酒馆导出的世界书
可以直接放进来,网关面板导出的也能导回酒馆。匹配规则按 SillyTavern 的行为重新实现(没有拷贝它的代码,
它是 AGPL-3.0):

- 主关键词任一命中才考虑这条;`/正则/flags` 形式的关键词按正则匹配,否则按纯文本
  (默认不区分大小写、不要求整词;条目自己的 caseSensitive / matchWholeWords 优先)
- 次关键词按 selectiveLogic 组合:0 AND_ANY、1 NOT_ALL、2 NOT_ANY、3 AND_ALL
- constant 常驻条目每轮都注入;disable 的条目不参与
- probability / useProbability:命中后按概率注入
- group / groupOverride / groupWeight:同一互斥组只留一条
- 递归:注入的条目正文可以再触发别的条目(excludeRecursion / preventRecursion / delayUntilRecursion)
- order:数值大的优先占用预算,渲染时排在后面(更靠近用户这句话)

网关没有「角色卡 / 作者注释 / 示例对话」,所以 position、depth、role 不改变注入位置:
所有条目都进召回块的「相关条目」一节(ADR-0003)。这些字段原样保留,导回酒馆不丢。
sticky / cooldown / delay、characterFilter、vectorized、automationId 同理:保留,但网关不用。
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from gateway.stores import MemoryHit

AND_ANY, NOT_ALL, NOT_ANY, AND_ALL = 0, 1, 2, 3
DEFAULT_ORDER, DEFAULT_WEIGHT = 100, 100
MAX_RECURSION_STEPS = 5

# 新建条目时的默认值(与 SillyTavern 的 newWorldInfoEntryDefinition 一致),导出时补齐,酒馆能直接读
ENTRY_DEFAULTS: dict[str, Any] = {
    "key": [], "keysecondary": [], "comment": "", "content": "", "constant": False, "vectorized": False,
    "selective": True, "selectiveLogic": AND_ANY, "addMemo": False, "order": DEFAULT_ORDER, "position": 0,
    "disable": False, "ignoreBudget": False, "excludeRecursion": False, "preventRecursion": False,
    "matchPersonaDescription": False, "matchCharacterDescription": False, "matchCharacterPersonality": False,
    "matchCharacterDepthPrompt": False, "matchScenario": False, "matchCreatorNotes": False,
    "delayUntilRecursion": 0, "probability": 100, "useProbability": True, "depth": 4, "outletName": "",
    "group": "", "groupOverride": False, "groupWeight": DEFAULT_WEIGHT, "scanDepth": None, "caseSensitive": None,
    "matchWholeWords": None, "useGroupScoring": None, "automationId": "", "role": 0, "sticky": None,
    "cooldown": None, "delay": None, "triggers": [],
}


@dataclass(frozen=True)
class LoreSettings:
    """全局设置,对应 SillyTavern 的 world_info_* 设置。"""

    scan_depth: int = 2              # 往回看几条消息(含本轮用户这句话)
    budget_chars: int = 4000         # 注入正文总字数上限;ignoreBudget 的条目不受限
    recursive: bool = False          # 注入的条目正文能否再触发别的条目
    case_sensitive: bool = False
    match_whole_words: bool = False
    user_name: str = "用户"           # 关键词和正文里的 {{user}}
    char_name: str = "大肥鱼"         # {{char}}


@dataclass
class Entry:
    world: str
    uid: str
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str) -> Any:
        value = self.data.get(name)
        return ENTRY_DEFAULTS.get(name) if value is None else value

    @property
    def hit_id(self) -> str:
        return f"{self.world}/{self.uid}"


def normalize_world(raw: Any) -> dict[str, Any]:
    """接受 SillyTavern 世界书 JSON(entries 是 dict 或 list),补齐缺省字段,uid 统一成字符串键。"""
    entries = raw.get("entries", {}) if isinstance(raw, dict) else {}
    items = entries.values() if isinstance(entries, dict) else entries if isinstance(entries, list) else []
    out: dict[str, dict[str, Any]] = {}
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        uid = item.get("uid", i)
        entry = {**ENTRY_DEFAULTS, **item, "uid": uid}
        for name in ("key", "keysecondary"):
            value = entry.get(name)
            entry[name] = [str(k) for k in value] if isinstance(value, list) else \
                [k.strip() for k in str(value).split(",") if k.strip()] if value else []
        out[str(uid)] = entry
    return {**(raw if isinstance(raw, dict) else {}), "entries": out}


def entries_of(worlds: dict[str, dict[str, Any]]) -> list[Entry]:
    return [Entry(name, uid, data) for name, world in worlds.items()
            for uid, data in (world.get("entries") or {}).items()]


def _macros(text: str, s: LoreSettings) -> str:
    text = re.sub(r"\{\{user\}\}", lambda _: s.user_name, text, flags=re.I)
    return re.sub(r"\{\{char\}\}", lambda _: s.char_name, text, flags=re.I)


_REGEX_KEY = re.compile(r"^/([\s\S]+?)/([gimsuy]*)$")
_JS_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


def _as_regex(key: str) -> re.Pattern[str] | None:
    """`/pattern/flags` 形式的关键词。不是这个形式、或者语法错,返回 None,按纯文本匹配。"""
    m = _REGEX_KEY.match(key)
    if not m or re.search(r"(^|[^\\])/", m.group(1)):
        return None
    flags = 0
    for f in m.group(2):
        flags |= _JS_FLAGS.get(f, 0)
    try:
        return re.compile(m.group(1).replace("\\/", "/"), flags)
    except re.error:
        return None


def match_key(haystack: str, key: str, entry: Entry, s: LoreSettings) -> bool:
    key = key.strip()
    if not key:
        return False
    pattern = _as_regex(key)
    if pattern is not None:
        return bool(pattern.search(haystack))
    case = entry.get("caseSensitive")
    if not (s.case_sensitive if case is None else case):
        haystack, key = haystack.lower(), key.lower()
    whole = entry.get("matchWholeWords")
    if not (s.match_whole_words if whole is None else whole) or len(key.split()) > 1:
        return key in haystack
    # 整词:前后是字符串边界或非 ASCII 字母数字(与 JS 的 \W 一致,中文字符算边界)
    return re.search(rf"(?:^|\W){re.escape(key)}(?:$|\W)", haystack, re.ASCII) is not None


def _matches(entry: Entry, text: str, s: LoreSettings) -> bool:
    primary = [_macros(k, s) for k in entry.get("key")]
    if not any(match_key(text, k, entry, s) for k in primary):
        return False
    secondary = [_macros(k, s) for k in entry.get("keysecondary") if str(k).strip()]
    if not entry.get("selective") or not secondary:
        return True
    hits = [match_key(text, k, entry, s) for k in secondary]
    logic = entry.get("selectiveLogic")
    return {AND_ANY: any(hits), NOT_ALL: not all(hits), NOT_ANY: not any(hits), AND_ALL: all(hits)}.get(logic, any(hits))


def _by_priority(entries: Iterable[Entry]) -> list[Entry]:
    """order 大的优先(占预算、互斥组里先选),order 相同按 uid 保持稳定。"""
    return sorted(entries, key=lambda e: (-float(e.get("order")), e.world, e.uid))


def _groups_of(entry: Entry) -> list[str]:
    return [g for g in re.split(r",\s*", str(entry.get("group") or "")) if g]


def _filter_groups(new: list[Entry], chosen: list[Entry], rand: Callable[[], float]) -> list[Entry]:
    """同一互斥组只留一条:组里已有被选中的就全部去掉;有 groupOverride 的取 order 最高的;否则按权重抽一条。"""
    keep = list(new)
    groups: dict[str, list[Entry]] = {}
    for e in new:
        for g in _groups_of(e):
            groups.setdefault(g, []).append(e)
    for g, members in groups.items():
        members = [m for m in members if m in keep]
        if any(g in _groups_of(c) for c in chosen):
            keep = [e for e in keep if e not in members]
            continue
        if len(members) <= 1:
            continue
        overrides = _by_priority(m for m in members if m.get("groupOverride"))
        if overrides:
            winner = overrides[0]
        else:
            weights = [float(m.get("groupWeight") or DEFAULT_WEIGHT) for m in members]
            roll, acc, winner = rand() * sum(weights), 0.0, members[-1]
            for m, w in zip(members, weights, strict=True):
                acc += w
                if roll <= acc:
                    winner = m
                    break
        keep = [e for e in keep if e not in members or e is winner]
    return keep


def activate(entries: list[Entry], recent: list[str], s: LoreSettings,
             rand: Callable[[], float] = random.random) -> list[Entry]:
    """返回本轮要注入的条目,已按渲染顺序排好(order 小的在前)。

    recent 是最近的消息正文,最后一条是本轮用户这句话;每条条目按自己的 scanDepth(没有就用全局的)往回看。
    """
    live = [e for e in entries if not e.get("disable") and str(e.get("content") or "").strip()]
    chosen: list[Entry] = []
    used = 0
    overflowed = False  # 与 SillyTavern 一致:预算一旦溢出,后面不忽略预算的条目都不再加入
    recursion = ""
    for step in range(MAX_RECURSION_STEPS + 1):
        candidates = []
        for e in live:
            if e in chosen:
                continue
            if step == 0 and e.get("constant"):
                candidates.append(e)
                continue
            if step == 0 and e.get("delayUntilRecursion"):
                continue
            if step > 0 and e.get("excludeRecursion"):
                continue
            depth = e.get("scanDepth")
            window = recent[-int(depth):] if depth is not None and int(depth) > 0 else \
                [] if depth is not None else recent[-s.scan_depth:]
            text = "\n".join(window) + (f"\n{recursion}" if step > 0 else "")
            if _matches(e, text, s):
                candidates.append(e)
        if not candidates:
            break
        added: list[Entry] = []
        for e in _filter_groups(_by_priority(candidates), chosen, rand):
            probability = float(e.get("probability") if e.get("probability") is not None else 100)
            if e.get("useProbability") and probability < 100 and rand() * 100 > probability:
                continue
            size = len(str(e.get("content")))
            if not e.get("ignoreBudget"):
                if overflowed or used + size > s.budget_chars:
                    overflowed = True
                    continue
                used += size
            added.append(e)
        if not added:
            break
        chosen.extend(added)
        if not s.recursive:
            break
        recursion = "\n".join(_macros(str(e.get("content")), s) for e in added if not e.get("preventRecursion"))
        if not recursion:
            break
    return sorted(chosen, key=lambda e: (float(e.get("order")), e.world, e.uid))


def render(entries: list[Entry], s: LoreSettings) -> list[MemoryHit]:
    return [MemoryHit(e.hit_id, _macros(str(e.get("content")), s).strip()) for e in entries]
