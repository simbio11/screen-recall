# -*- coding: utf-8 -*-
"""
화면 기억 자동완성 (Windows)

전에 본 화면의 글자를 기억했다가, 입력할 때 앞부분만 쳐도 추천해 줍니다.
  - 추천창이 떠 있을 때만 Shift+Enter = 1순위(선택된) 추천 입력
  - Shift+↑ / Shift+↓ = 추천 선택 이동
  - Esc = 추천창 닫기
  - Ctrl+Alt+Shift+R = 일시정지/재개
  - Ctrl+Alt+Shift+X = 기억 전부 지우기

구조
  수집 스레드 : 맨 앞 창의 글자를 UI 접근성(UIA)으로 읽어 RecallStore에 넣음
  감시 스레드 : 지금 입력 중인 칸의 커서 앞 글자를 읽어 추천 계산
  키 훅 스레드: Shift+Enter 등을 '추천창이 떠 있을 때만' 가로챔
  입력 스레드 : 추천 글자를 실제로 쳐 넣음(SendInput)
  메인(tk)   : 커서 근처에 작은 추천창 표시(포커스를 뺏지 않음)
"""
import ctypes
import ctypes.wintypes as wt
import os
import queue
import sys
import threading
import time
import tkinter as tk

import uiautomation as auto

from recall_core import RecallStore, is_hangul, tail_token

# ------------------------------------------------------------------ 설정
SCAN_INTERVAL = 1.5          # 화면 글자 수집 주기(초)
WATCH_INTERVAL = 0.12        # 입력 칸 감시 주기(초)
SCAN_MAX_NODES = 3000        # 한 번 수집할 때 훑을 최대 UI 요소 수
SCAN_MAX_SEC = 1.2           # 한 번 수집에 쓸 최대 시간
DOC_MAX_CHARS = 60000        # 문서/웹페이지 한 번에 읽을 최대 글자 수
MAX_SUGGEST = 5
MIN_KEYS = 3                 # 자모/글자 3개 이상 쳐야 추천 (예: '안ㄴ' = 4)
EXCLUDE_PROCESSES = {        # 이 프로그램들의 화면은 기억하지 않음
    "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe",
}

# ------------------------------------------------------------------ Win32
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

try:  # 좌표를 실제 픽셀 기준으로
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
LLKHF_INJECTED = 0x10
VK_BACK, VK_RETURN, VK_SHIFT, VK_CONTROL, VK_MENU = 0x08, 0x0D, 0x10, 0x11, 0x12
VK_ESCAPE, VK_RIGHT, VK_UP, VK_DOWN = 0x1B, 0x27, 0x26, 0x28
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_R, VK_X = 0x52, 0x58

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4

SW_HIDE = 0
HWND_TOPMOST = wt.HWND(-1)
SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x8, 0x80, 0x08000000
GA_ROOT = 2


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hwndActive", wt.HWND),
                ("hwndFocus", wt.HWND), ("hwndCapture", wt.HWND),
                ("hwndMenuOwner", wt.HWND), ("hwndMoveSize", wt.HWND),
                ("hwndCaret", wt.HWND), ("rcCaret", wt.RECT)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetAsyncKeyState.restype = ctypes.c_short

MY_PID = os.getpid()


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def process_name(pid):
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def foreground_pid():
    hwnd = user32.GetForegroundWindow()
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return hwnd, pid.value


# ------------------------------------------------------------------ 입력 보내기
def _key_input(vk=0, scan=0, flags=0):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def send_inputs(items):
    if not items:
        return
    arr = (INPUT * len(items))(*items)
    user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))


def tap(vk, extended=False, times=1):
    fl = KEYEVENTF_EXTENDEDKEY if extended else 0
    items = []
    for _ in range(times):
        items += [_key_input(vk, 0, fl), _key_input(vk, 0, fl | KEYEVENTF_KEYUP)]
    send_inputs(items)


def type_unicode(text):
    items = []
    data = text.encode("utf-16-le")  # 이모지 등은 서로게이트 2개로 나뉘어 들어감
    for i in range(0, len(data), 2):
        code = data[i] | (data[i + 1] << 8)
        items.append(_key_input(0, code, KEYEVENTF_UNICODE))
        items.append(_key_input(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    send_inputs(items)


# ------------------------------------------------------------------ 공유 상태
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.visible = False
        self.candidates = []
        self.sel = 0
        self.prefix = ""
        self.caret_at_end = True
        self.dismissed_prefix = None   # Esc/입력 직후: 이 접두어로는 다시 안 띄움
        self.paused = False
        self.swallow_up = set()        # 가로챈 키다운의 짝 키업도 삼키기


store = RecallStore()
state = State()
ui_q = queue.Queue()        # 메인(tk)으로 보내는 명령
insert_q = queue.Queue()    # 입력 스레드로 보내는 작업


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ UIA 도우미
EDITABLE_TYPES = {auto.ControlType.EditControl, auto.ControlType.DocumentControl,
                  auto.ControlType.ComboBoxControl}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def control_text(ctrl):
    """요소 하나에서 읽을 수 있는 글자."""
    parts = []
    name = _safe(lambda: ctrl.Name, "")
    if name:
        parts.append(name)
    ct = _safe(lambda: ctrl.ControlType)
    if ct in EDITABLE_TYPES:
        tp = _safe(lambda: ctrl.GetPattern(auto.PatternId.TextPattern))
        if tp:
            t = _safe(lambda: tp.DocumentRange.GetText(DOC_MAX_CHARS), "")
            if t:
                parts.append(t)
                return "\n".join(parts), True   # 문서 전체를 읽었으니 자식은 생략
        vp = _safe(lambda: ctrl.GetPattern(auto.PatternId.ValuePattern))
        if vp:
            v = _safe(lambda: vp.Value, "")
            if v:
                parts.append(v)
    return "\n".join(parts), False


def scan_window(root):
    """창 하나를 훑어 글자를 모음. 입력 중인 칸과 비밀번호 칸은 건너뜀."""
    texts = []
    deadline = time.time() + SCAN_MAX_SEC
    count = 0
    stack = [(root, 0)]
    while stack and count < SCAN_MAX_NODES and time.time() < deadline:
        ctrl, depth = stack.pop()
        count += 1
        if _safe(lambda: ctrl.IsPassword, False):
            continue
        ct = _safe(lambda: ctrl.ControlType)
        if ct in EDITABLE_TYPES and _safe(lambda: ctrl.HasKeyboardFocus, False):
            continue  # 지금 치고 있는 칸 = '본 글자'가 아님
        txt, skip_children = control_text(ctrl)
        if txt:
            texts.append(txt)
        if skip_children or depth > 40:
            continue
        kids = []
        child = _safe(lambda: ctrl.GetFirstChildControl())
        while child is not None and len(kids) < 500:
            kids.append(child)
            child = _safe(lambda c=child: c.GetNextSiblingControl())
        for k in reversed(kids):
            stack.append((k, depth + 1))
    return "\n".join(texts)


# ------------------------------------------------------------------ 수집 스레드
def collector():
    with auto.UIAutomationInitializerInThread():
        last_hwnd = None
        last_time = 0.0
        while True:
            time.sleep(0.3)
            if state.paused:
                continue
            hwnd, pid = foreground_pid()
            now = time.time()
            # 창이 바뀌었으면 즉시, 같은 창이면 주기마다
            if hwnd == last_hwnd and now - last_time < SCAN_INTERVAL:
                continue
            last_hwnd, last_time = hwnd, now
            if not hwnd or pid == MY_PID:
                continue
            if process_name(pid) in EXCLUDE_PROCESSES:
                continue
            try:
                root = auto.ControlFromHandle(hwnd)
                if root is None:
                    continue
                text = scan_window(root)
                store.add_text(text)
            except Exception as e:  # 수집 실패는 조용히 넘어감
                log("수집 오류:", e)


# ------------------------------------------------------------------ 감시 스레드
def caret_from_win32():
    hwnd, _ = foreground_pid()
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    gti = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(gti)) or not gti.hwndCaret:
        return None
    r = gti.rcCaret
    if r.right == 0 and r.bottom == 0:
        return None
    pt = wt.POINT(r.left, r.bottom)
    user32.ClientToScreen(gti.hwndCaret, ctypes.byref(pt))
    return pt.x, pt.y


def read_focused():
    """(커서 앞 글자, 커서가 끝에 있는지, 화면 좌표) 또는 None."""
    ctrl = _safe(auto.GetFocusedControl)
    if ctrl is None:
        return None
    if _safe(lambda: ctrl.ProcessId) == MY_PID:
        return None
    if _safe(lambda: ctrl.IsPassword, False):
        return None
    ct = _safe(lambda: ctrl.ControlType)
    if ct not in EDITABLE_TYPES:
        return None

    before, at_end, pos = None, True, None
    tp = _safe(lambda: ctrl.GetPattern(auto.PatternId.TextPattern))
    if tp:
        sel = _safe(lambda: tp.GetSelection())
        if sel:
            caret = sel[0]
            rng = _safe(lambda: caret.Clone())
            if rng is not None:
                _safe(lambda: rng.MoveEndpointByRange(
                    auto.TextPatternRangeEndpoint.End, caret,
                    auto.TextPatternRangeEndpoint.Start))
                _safe(lambda: rng.MoveEndpointByUnit(
                    auto.TextPatternRangeEndpoint.Start, auto.TextUnit.Character, -60))
                before = _safe(lambda: rng.GetText(200))
                after = _safe(lambda: caret.Clone())
                if after is not None:
                    _safe(lambda: after.MoveEndpointByUnit(
                        auto.TextPatternRangeEndpoint.End, auto.TextUnit.Character, 1))
                    nxt = _safe(lambda: after.GetText(5), "") or ""
                    at_end = nxt.strip("\r\n") == "" or nxt[:1].isspace()
                rects = _safe(lambda: rng.GetBoundingRectangles(), []) or []
                if rects:
                    r = rects[-1]
                    pos = (r.right, r.bottom)
    if before is None:
        vp = _safe(lambda: ctrl.GetPattern(auto.PatternId.ValuePattern))
        if vp:
            before = _safe(lambda: vp.Value)
            at_end = True
    if before is None:
        return None
    if pos is None:
        pos = caret_from_win32()
    if pos is None:
        br = _safe(lambda: ctrl.BoundingRectangle)
        if br:
            pos = (br.left + 8, br.bottom)
    return before, at_end, pos


def watcher():
    with auto.UIAutomationInitializerInThread():
        last_key = None
        while True:
            time.sleep(WATCH_INTERVAL)
            if state.paused:
                if state.visible:
                    hide_popup()
                continue
            info = _safe(read_focused)
            if not info:
                if state.visible:
                    hide_popup()
                last_key = None
                continue
            before, at_end, pos = info
            prefix = tail_token(before)
            store.note_typed(prefix)
            if prefix == state.dismissed_prefix:
                continue  # Esc로 닫았거나 방금 입력한 그 상태 그대로면 다시 안 띄움
            state.dismissed_prefix = None
            cands = store.suggest(prefix, limit=MAX_SUGGEST, min_keys=MIN_KEYS) if prefix else []
            key = (prefix, tuple(cands), pos)
            if key == last_key:
                continue
            last_key = key
            if cands:
                with state.lock:
                    keep = state.candidates[state.sel] if state.visible and state.candidates else None
                    state.candidates = cands
                    state.sel = cands.index(keep) if keep in cands else 0
                    state.prefix = prefix
                    state.caret_at_end = at_end
                    state.visible = True
                ui_q.put(("show", pos))
            elif state.visible:
                hide_popup()


def hide_popup():
    with state.lock:
        state.visible = False
    ui_q.put(("hide", None))


# ------------------------------------------------------------------ 키 훅
def hook_thread():
    def proc(nCode, wParam, lParam):
        if nCode == 0:
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            injected = bool(kb.flags & LLKHF_INJECTED)
            vk = kb.vkCode
            is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)
            if not injected:
                if is_up and vk in state.swallow_up:
                    state.swallow_up.discard(vk)
                    return 1
                if is_down and handle_key(vk):
                    state.swallow_up.add(vk)
                    return 1
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    cb = HOOKPROC(proc)  # 참조 유지 필수
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, cb, None, 0)
    if not hook:
        log("키보드 훅 설치 실패:", ctypes.get_last_error())
        return
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def handle_key(vk):
    """True를 돌려주면 그 키는 앱으로 안 감(가로챔)."""
    shift = key_down(VK_SHIFT)
    ctrl = key_down(VK_CONTROL)
    alt = key_down(VK_MENU)

    if ctrl and alt and shift and vk == VK_R:
        state.paused = not state.paused
        log("일시정지" if state.paused else "재개")
        return True
    if ctrl and alt and shift and vk == VK_X:
        store.clear()
        log("기억을 모두 지웠습니다")
        return True

    if not state.visible or state.paused:
        return False
    if vk == VK_RETURN and shift and not ctrl and not alt:
        with state.lock:
            if not state.candidates:
                return False
            text = state.candidates[state.sel]
            job = (state.prefix, text, state.caret_at_end)
            state.visible = False
            state.dismissed_prefix = text
        ui_q.put(("hide", None))
        insert_q.put(job)
        return True
    if shift and vk in (VK_UP, VK_DOWN) and not ctrl and not alt:
        with state.lock:
            n = len(state.candidates)
            if n:
                state.sel = (state.sel + (1 if vk == VK_DOWN else -1)) % n
        ui_q.put(("redraw", None))
        return True
    if vk == VK_ESCAPE:
        with state.lock:
            state.visible = False
            state.dismissed_prefix = state.prefix
        ui_q.put(("hide", None))
        return False  # Esc는 앱에도 그대로 전달
    return False


# ------------------------------------------------------------------ 입력 스레드
def inserter():
    while True:
        prefix, text, at_end = insert_q.get()
        time.sleep(0.03)
        # 사용자가 누르고 있는 Shift를 잠깐 떼어 줌(선택 영역이 생기지 않게)
        send_inputs([_key_input(VK_LSHIFT, 0, KEYEVENTF_KEYUP),
                     _key_input(VK_RSHIFT, 0, KEYEVENTF_KEYUP),
                     _key_input(VK_SHIFT, 0, KEYEVENTF_KEYUP)])
        time.sleep(0.02)
        # 한글 조합 중이면 →키로 조합을 확정 (커서가 끝에 있을 때만 안전)
        if prefix and is_hangul(prefix[-1]) and at_end:
            tap(VK_RIGHT, extended=True)
            time.sleep(0.02)
        tap(VK_BACK, times=len(prefix))
        time.sleep(0.02)
        type_unicode(text)
        log(f"입력: {prefix!r} -> {text!r}")


# ------------------------------------------------------------------ 추천창(tk)
class Popup:
    BG, FG, SEL_BG, SEL_FG, HINT = "#1f2328", "#e6edf3", "#2f81f7", "#ffffff", "#8b949e"

    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=self.BG)
        self.root.geometry("+-10000+-10000")
        self.font = ("Malgun Gothic", 10)
        self.frame = tk.Frame(self.root, bg=self.BG, padx=6, pady=4)
        self.frame.pack()
        self.rows = [tk.Label(self.frame, font=self.font, anchor="w", padx=6, pady=1)
                     for _ in range(MAX_SUGGEST)]
        self.hint = tk.Label(self.frame, text="Shift+Enter 입력 · Shift+↑↓ 선택 · Esc 닫기",
                             font=("Malgun Gothic", 8), bg=self.BG, fg=self.HINT, anchor="w")
        self.root.update_idletasks()
        self.hwnd = user32.GetAncestor(self.root.winfo_id(), GA_ROOT)
        ex = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                              ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        user32.ShowWindow(self.hwnd, SW_HIDE)
        self.pos = (0, 0)
        self.root.after(30, self.poll)

    def draw(self):
        with state.lock:
            cands, sel = list(state.candidates), state.sel
        for i, lbl in enumerate(self.rows):
            if i < len(cands):
                on = i == sel
                lbl.configure(text=cands[i], bg=self.SEL_BG if on else self.BG,
                              fg=self.SEL_FG if on else self.FG)
                lbl.pack(fill="x")
            else:
                lbl.pack_forget()
        self.hint.pack_forget()
        self.hint.pack(fill="x", pady=(3, 0))

    def show(self, pos):
        if pos:
            self.pos = pos
        self.draw()
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x, y = int(self.pos[0]), int(self.pos[1]) + 4
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(0, min(x, sw - w))
        if y + h > sh:
            y = max(0, y - h - 28)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h,
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def hide(self):
        user32.ShowWindow(self.hwnd, SW_HIDE)

    def poll(self):
        try:
            while True:
                cmd, arg = ui_q.get_nowait()
                if cmd == "show" and state.visible:
                    self.show(arg)
                elif cmd == "redraw" and state.visible:
                    self.show(None)
                elif cmd == "hide":
                    self.hide()
        except queue.Empty:
            pass
        self.root.after(30, self.poll)


# ------------------------------------------------------------------ 시작
def main():
    if sys.platform != "win32":
        print("Windows에서만 동작합니다.")
        return
    log("화면 기억 자동완성 시작 — 이 창을 닫으면 종료됩니다.")
    log("Shift+Enter 입력 · Shift+↑↓ 선택 · Esc 닫기 · "
        "Ctrl+Alt+Shift+R 일시정지 · Ctrl+Alt+Shift+X 기억 지우기")
    for fn in (collector, watcher, hook_thread, inserter):
        threading.Thread(target=fn, daemon=True).start()
    Popup().root.mainloop()


if __name__ == "__main__":
    main()
