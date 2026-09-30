"""Raw-terminal prompts in the Claude Code style. POSIX terminals only."""

from __future__ import annotations

import itertools
import os
import select
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from .choices import rank

COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ
ORANGE, DIM, BOLD = ("\033[38;2;217;119;87m", "\033[2m", "\033[1m") if COLOR else ("", "", "")
GREEN, RED, RESET = ("\033[32m", "\033[31m", "\033[0m") if COLOR else ("", "", "")
UP, DOWN = ("\x1b[A", "\x1bOA"), ("\x1b[B", "\x1bOB")


class Back(Exception):
    """Esc: return to the previous question."""


@contextmanager
def keys() -> Iterator[None]:
    """No echo or line buffering for the whole session, so keys typed mid-redraw never echo."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\033[?25l")
        yield
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key() -> str:
    # Read the raw fd and drain the rest of an escape sequence in the same call;
    # a buffered one-byte read splits arrow keys into a bare Esc.
    fd = sys.stdin.fileno()
    data = os.read(fd, 1)
    while select.select([fd], [], [], 0.03)[0]:
        data += os.read(fd, 1024)
    return data.decode(errors="ignore")


class Frame:
    """Redraws a block of lines in place, then erases it once answered."""

    def __init__(self) -> None:
        self.height = 0

    def draw(self, rows: Sequence[str]) -> None:
        self.clear()
        sys.stdout.write("\n".join(rows) + "\n")
        sys.stdout.flush()
        self.height = len(rows)

    def clear(self) -> None:
        if self.height:
            sys.stdout.write(f"\033[{self.height}A\033[J")
            self.height = 0


def question(text: str) -> str:
    return f"{ORANGE}?{RESET} {BOLD}{text}{RESET}"


def handle_control(key: str, frame: Frame) -> None:
    if key == "\x03":
        frame.clear()
        raise KeyboardInterrupt
    if key == "\x1b":
        frame.clear()
        raise Back


def menu(text: str, options: Sequence[tuple[str, str]], *, selected: int = 0) -> int:
    frame = Frame()
    while True:
        rows = [question(text)]
        for index, (label, _) in enumerate(options):
            tag = f"  {DIM}recommended{RESET}" if index == 0 and len(options) > 2 else ""
            rows.append(
                f"{ORANGE}❯ {label}{RESET}{tag}" if index == selected else f"  {label}{tag}"
            )
        hint = options[selected][1]
        rows.append(f"  {DIM}{hint + ' · ' if hint else ''}↑↓ move · enter select · esc back{RESET}")
        frame.draw(rows)
        key = read_key()
        handle_control(key, frame)
        if key in UP or key == "k":
            selected = (selected - 1) % len(options)
        elif key in DOWN or key == "j":
            selected = (selected + 1) % len(options)
        elif key in ("\r", "\n"):
            frame.clear()
            return selected


def search(text: str, items: Sequence[str], *, window: int = 8) -> str:
    """Type-to-filter picker; Enter on the last row uses the typed ID as-is."""
    frame, query, selected = Frame(), "", 0
    while True:
        ranked = rank(query, items)
        matches = ranked + ([query.strip()] if query.strip() and query.strip() not in ranked else [])
        selected = min(selected, max(len(matches) - 1, 0))
        top = max(0, min(selected - window // 2, len(matches) - window))
        rows = [f"{question(text)} {ORANGE}❯{RESET} {query}\033[7m \033[0m"]
        for index in range(top, min(top + window, len(matches))):
            label = matches[index] if index < len(ranked) else f'Use "{matches[index]}"'
            rows.append(f"{ORANGE}❯ {label}{RESET}" if index == selected else f"  {label}")
        if not matches:
            rows.append(f"  {DIM}type a model ID{RESET}")
        rows.append(
            f"  {DIM}{len(ranked)} of {len(items)} · type to filter · ↑↓ move · enter select"
            f" · esc back{RESET}"
        )
        frame.draw(rows)
        key = read_key()
        handle_control(key, frame)
        if key in UP:
            selected = max(selected - 1, 0)
        elif key in DOWN:
            selected = min(selected + 1, len(matches) - 1)
        elif key in ("\r", "\n") and matches:
            frame.clear()
            return matches[selected]
        elif key in ("\x7f", "\b"):
            query, selected = query[:-1], 0
        elif not key.startswith("\x1b"):
            typed = "".join(c for c in key if c.isprintable())
            if typed:
                query, selected = query + typed, 0


def text_input(
    text: str, *, default: str = "", initial: str = "", secret: bool = False,
    validate: Callable[[str], str | None] = lambda _: None,
) -> str:
    frame, value, error = Frame(), initial, ""
    shown_default = f" {DIM}({default}){RESET}" if default else ""
    while True:
        echo = "•" * len(value) if secret else value
        rows = [f"{question(text)}{shown_default} {ORANGE}❯{RESET} {echo}\033[7m \033[0m"]
        if error:
            rows.append(f"  {RED}{error}{RESET}")
        frame.draw(rows)
        key = read_key()
        handle_control(key, frame)
        if key in ("\x7f", "\b"):
            value = value[:-1]
        elif key.startswith("\x1b"):
            continue
        else:
            value += "".join(c for c in key if c.isprintable())
            if "\r" in key or "\n" in key:
                answer = value.strip() or default
                error = validate(answer) or ("" if answer else "Required")
                if not error:
                    frame.clear()
                    return answer


def answered(text: str, answer: str) -> None:
    print(f"{GREEN}✔{RESET} {DIM}{text}{RESET} {ORANGE}{answer}{RESET}")


def erase_lines(count: int) -> None:
    if count:
        sys.stdout.write(f"\033[{count}A\033[J")
        sys.stdout.flush()


def banner() -> None:
    print(f"\n{ORANGE}╭──────────────────────────────────────╮{RESET}")
    print(f"{ORANGE}│{RESET} {ORANGE}✻{RESET} {BOLD}Welcome to LiteAgents{RESET}              {ORANGE}│{RESET}")
    print(f"{ORANGE}│{RESET}   {DIM}Spin up an agent on any harness{RESET}    {ORANGE}│{RESET}")
    print(f"{ORANGE}╰──────────────────────────────────────╯{RESET}\n")


def run_step(label: str, action: Callable[[], str | None], *, animate: bool) -> str | None:
    """Show a spinner while action runs. Prints ✓ only when it returns no error."""
    done = threading.Event()

    def spin() -> None:
        for frame in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            sys.stdout.write(f"\r\033[K{ORANGE}{frame}{RESET} {DIM}{label}…{RESET}")
            sys.stdout.flush()
            if done.wait(0.1):
                return

    spinner = threading.Thread(target=spin, daemon=True) if animate else None
    if spinner:
        spinner.start()
    try:
        error = action()
    except Exception as exc:  # noqa: BLE001 - every failed step reports the same way
        error = f"{type(exc).__name__}: {exc}"
    finally:
        done.set()
        if spinner:
            spinner.join()
            sys.stdout.write("\r\033[K")
    print(f"{RED}✗ {label}{RESET}\n  {error}" if error else f"{GREEN}✓{RESET} {label}", flush=True)
    return error
