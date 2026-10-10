# -*- coding: utf-8 -*-
"""
화면 기억 자동완성 - 핵심 로직 (OS와 무관, 단독 테스트 가능)

- 한글을 '키 입력 단위 자모'로 풀어서 비교합니다.
  예) "안ㄴ" -> ㅇㅏㄴㄴ,  "안녕하세요" -> ㅇㅏㄴㄴㅕㅇㅎㅏㅅㅔㅇㅛ  => 접두어 일치
- 화면에서 읽은 글자를 단어/짧은 구절로 쪼개 메모리에만 보관합니다(디스크 저장 없음).
"""
import re
import threading
import time

# ---------------------------------------------------------------- 한글 자모
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ",
         "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ",
         "ㅍ", "ㅎ"]

# 두 번 눌러 만드는 겹모음/겹받침은 키 입력 순서대로 풀어 줍니다.
_SPLIT = {
    "ㅘ": "ㅗㅏ", "ㅙ": "ㅗㅐ", "ㅚ": "ㅗㅣ", "ㅝ": "ㅜㅓ", "ㅞ": "ㅜㅔ", "ㅟ": "ㅜㅣ",
    "ㅢ": "ㅡㅣ",
    "ㄳ": "ㄱㅅ", "ㄵ": "ㄴㅈ", "ㄶ": "ㄴㅎ", "ㄺ": "ㄹㄱ", "ㄻ": "ㄹㅁ", "ㄼ": "ㄹㅂ",
    "ㄽ": "ㄹㅅ", "ㄾ": "ㄹㅌ", "ㄿ": "ㄹㅍ", "ㅀ": "ㄹㅎ", "ㅄ": "ㅂㅅ",
}


def is_syllable(ch):
    return "\uac00" <= ch <= "\ud7a3"


def is_jamo(ch):
    return "\u3131" <= ch <= "\u318e"


def is_hangul(ch):
    return is_syllable(ch) or is_jamo(ch)


def to_keys(text):
    """글자를 키 입력 단위 자모 문자열로 변환 (영문은 소문자)."""
    out = []
    for ch in text:
        if is_syllable(ch):
            code = ord(ch) - 0xAC00
            cho, rest = divmod(code, 21 * 28)
            jung, jong = divmod(rest, 28)
            for j in (_CHO[cho], _JUNG[jung], _JONG[jong]):
                if j:
                    out.append(_SPLIT.get(j, j))
        elif is_jamo(ch):
            out.append(_SPLIT.get(ch, ch))
        else:
            out.append(ch.lower())
    return "".join(out)


# ---------------------------------------------------------------- 토큰화
_STRIP = " \t\r\n.,!?;:\"'`()[]{}<>「」『』【】〈〉《》…·•~-—–_=+*/\\|“”‘’"
_SPACE = re.compile(r"\s+")


def clean_token(tok):
    return tok.strip(_STRIP)


def has_loose_jamo(s):
    return any(is_jamo(c) for c in s)


def tokenize(text, min_len=2, max_len=40, phrase_max_words=6):
    """화면 글자 -> (후보 문자열) 목록. 단어 + 짧은 줄(구절)."""
    result = []
    if not text:
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        words = []
        for raw in _SPACE.split(line):
            w = clean_token(raw)
            if min_len <= len(w) <= max_len and not has_loose_jamo(w):
                words.append(w)
        result.extend(words)
        # 짧은 줄은 통째로도 후보 (예: "홍길동 원장님", "010-1234-5678")
        phrase = clean_token(_SPACE.sub(" ", line))
        if (" " in phrase and len(phrase) <= max_len
                and len(phrase.split(" ")) <= phrase_max_words
                and not has_loose_jamo(phrase)):
            result.append(phrase)
    return result


def tail_token(text_before_caret):
    """커서 앞에서 지금 치고 있는 마지막 덩어리."""
    if not text_before_caret:
        return ""
    m = re.search(r"(\S+)$", text_before_caret)
    if not m:
        return ""
    tok = m.group(1)
    # 앞쪽 괄호/따옴표는 떼고 비교
    return tok.lstrip(_STRIP)


# ---------------------------------------------------------------- 저장소
class RecallStore:
    """본 글자를 최근성 기준으로 기억. 메모리에만 존재."""

    def __init__(self, max_items=8000, ttl_sec=3 * 60 * 60):
        self.max_items = max_items
        self.ttl = ttl_sec
        self._items = {}   # text -> [keys, last_seen, count]
        self._lock = threading.Lock()
        self._typed = {}   # 사용자가 직접 치던 덩어리 -> 시각 (수집에서 제외용)

    def note_typed(self, tok):
        if tok:
            with self._lock:
                self._typed[tok] = time.time()

    def add_text(self, text, now=None):
        now = now or time.time()
        toks = tokenize(text)
        with self._lock:
            # 최근 10초 안에 사용자가 직접 치던 건 '본 글자'가 아님
            typed = {t for t, ts in self._typed.items() if now - ts < 10}
            for t in toks:
                if t in typed:
                    continue
                it = self._items.get(t)
                if it:
                    it[1] = now
                    it[2] += 1
                else:
                    self._items[t] = [to_keys(t), now, 1]
            self._prune(now)
        return len(toks)

    def _prune(self, now):
        if self._typed:
            self._typed = {t: ts for t, ts in self._typed.items() if now - ts < 60}
        dead = [t for t, it in self._items.items() if now - it[1] > self.ttl]
        for t in dead:
            del self._items[t]
        if len(self._items) > self.max_items:
            keep = sorted(self._items.items(), key=lambda kv: kv[1][1], reverse=True)
            self._items = dict(keep[: self.max_items])

    def suggest(self, prefix, limit=5, min_keys=3):
        pk = to_keys(prefix)
        if len(pk) < min_keys:
            return []
        hits = []
        with self._lock:
            for t, (keys, last, cnt) in self._items.items():
                if t != prefix and len(keys) > len(pk) and keys.startswith(pk):
                    hits.append((last, cnt, -len(t), t))
        hits.sort(reverse=True)  # 가장 최근에 본 것 먼저
        return [h[3] for h in hits[:limit]]

    def clear(self):
        with self._lock:
            self._items.clear()

    def __len__(self):
        return len(self._items)
