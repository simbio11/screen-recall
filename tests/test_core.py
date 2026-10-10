# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from recall_core import RecallStore, tail_token, to_keys, tokenize  # noqa: E402


def make_store():
    s = RecallStore()
    s.add_text("안녕하세요 반갑습니다\n홍길동 원장님\n010-1234-5678 예약 확인")
    return s


def test_to_keys_splits_compound():
    assert to_keys("왔") == "ㅇㅗㅏㅆ"
    assert to_keys("없") == "ㅇㅓㅂㅅ"
    assert to_keys("Hello") == "hello"


def test_composing_prefix_matches():
    s = make_store()
    assert s.suggest("안ㄴ")[0] == "안녕하세요"
    assert s.suggest("예야") == ["예약"]          # '약' 조합 중
    assert "홍길동 원장님" in s.suggest("홍길")   # 구절 후보
    assert s.suggest("010")[0] == "010-1234-5678"


def test_too_short_prefix():
    assert make_store().suggest("아") == []


def test_tokenize_skips_loose_jamo():
    assert "안ㄴ" not in tokenize("안ㄴ 테스트")


def test_tail_token():
    assert tail_token("오늘 (안ㄴ") == "안ㄴ"
    assert tail_token("끝 ") == ""


def test_typed_text_not_collected():
    s = RecallStore()
    s.note_typed("안녕하")
    s.add_text("안녕하")
    assert s.suggest("안ㄴ") == []


def test_recent_first():
    s = RecallStore()
    s.add_text("가나다라", now=1000)
    s.add_text("가나다마", now=2000)
    assert s.suggest("가나ㄷ") == ["가나다마", "가나다라"]
