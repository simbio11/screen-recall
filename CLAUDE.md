# 작업 안내 (Claude용)

비오님(simbio11)의 개인 도구 프로젝트입니다. 대화는 한국어로 합니다.

## 정해진 것
- 1차 대상: **Windows PC** (휴대폰은 나중에, 안드로이드 키보드 앱으로 별도)
- 화면 글자 수집: **UI 접근성(UIA)** 읽기. OCR은 아직 안 씀
- 확정 키: **추천창이 떠 있을 때만 Shift+Enter** (없으면 원래 줄바꿈)
- 기억은 메모리에만 — 디스크 저장 금지 (한의원 업무 화면 글자가 섞일 수 있음)

## 구조
- `recall_core.py` — OS와 무관한 로직. 여기 바꾸면 `python -m pytest tests` 통과시킬 것
- `screen_recall.py` — Windows 전용(ctypes, uiautomation, tkinter). 리눅스에서는 실행 불가, 문법 검사만 가능
- `run.bat` — 비오님 PC에서 실행하는 입구

## 다음 할 일 후보
1. 비오님 PC에서 첫 실행 → 메모장·크롬·카톡에서 동작 확인, 문제 기록
2. 접근성으로 못 읽는 앱이 있으면 OCR(Windows 내장 OCR) 보조 추가
3. 트레이 아이콘, PyInstaller로 exe 만들기
4. 안드로이드 버전 설계
