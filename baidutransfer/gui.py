# -*- coding: utf-8 -*-

import asyncio
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import pygame

from . import utils
from .runner import TransferJob, load_cookie, parse_share_url, run_transfer
from .settings import TransferSettings


COLORS = {
    "bg": "#0b1220",
    "panel": "#0f192a",
    "card": "#111c30",
    "input": "#09111f",
    "border": "#263b5c",
    "border_focus": "#5b8cff",
    "text": "#edf3fb",
    "muted": "#8fa1ba",
    "primary": "#4f7cff",
    "primary_hover": "#638cff",
    "success": "#52d3a6",
    "warning": "#f6c768",
    "danger": "#ff7383",
    "notice": "#12253b",
}


def resource_path(relative):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return str(base / relative)


def choose_font(size, bold=False):
    names = ["Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "Arial"]
    for name in names:
        path = pygame.font.match_font(name, bold=bold)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, size)


class TextField:
    def __init__(self, value="", placeholder="", password=False, numeric=False):
        self.value = str(value)
        self.placeholder = placeholder
        self.password = password
        self.numeric = numeric
        self.focused = False
        self.rect = pygame.Rect(0, 0, 100, 42)
        self.cursor = len(self.value)

    def set_value(self, value):
        self.value = str(value)
        self.cursor = len(self.value)

    def handle(self, event):
        changed = False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.focused = self.rect.collidepoint(event.pos)
            if self.focused:
                self.cursor = len(self.value)
        if not self.focused:
            return changed
        if event.type == pygame.KEYDOWN:
            ctrl = bool(event.mod & pygame.KMOD_CTRL)
            if ctrl and event.key == pygame.K_a:
                self.cursor = len(self.value)
                self.value = ""
                return True
            if ctrl and event.key == pygame.K_v:
                try:
                    raw = pygame.scrap.get(pygame.SCRAP_TEXT) or b""
                    text = raw.decode("utf-8", errors="ignore").rstrip("\x00")
                    if self.numeric:
                        text = "".join(ch for ch in text if ch.isdigit())
                    self.value = self.value[: self.cursor] + text + self.value[self.cursor :]
                    self.cursor += len(text)
                    return True
                except pygame.error:
                    return False
            if event.key == pygame.K_BACKSPACE and self.cursor:
                self.value = self.value[: self.cursor - 1] + self.value[self.cursor :]
                self.cursor -= 1
                return True
            if event.key == pygame.K_DELETE and self.cursor < len(self.value):
                self.value = self.value[: self.cursor] + self.value[self.cursor + 1 :]
                return True
            if event.key == pygame.K_LEFT:
                self.cursor = max(0, self.cursor - 1)
            elif event.key == pygame.K_RIGHT:
                self.cursor = min(len(self.value), self.cursor + 1)
            elif event.key in (pygame.K_HOME, pygame.K_UP):
                self.cursor = 0
            elif event.key in (pygame.K_END, pygame.K_DOWN):
                self.cursor = len(self.value)
        if event.type == pygame.TEXTINPUT:
            text = event.text
            if self.numeric:
                text = "".join(ch for ch in text if ch.isdigit())
            if text:
                self.value = self.value[: self.cursor] + text + self.value[self.cursor :]
                self.cursor += len(text)
                changed = True
        return changed

    def draw(self, surface, font, suffix=""):
        pygame.draw.rect(surface, COLORS["input"], self.rect, border_radius=8)
        border = COLORS["border_focus"] if self.focused else COLORS["border"]
        pygame.draw.rect(surface, border, self.rect, 1, border_radius=8)
        raw = "•" * len(self.value) if self.password and self.value else self.value
        display = raw + suffix if raw else self.placeholder
        color = COLORS["text"] if raw else COLORS["muted"]
        available = self.rect.width - 22
        while display and font.size(display)[0] > available:
            display = "…" + display[2:]
        image = font.render(display, True, color)
        surface.blit(image, (self.rect.x + 11, self.rect.centery - image.get_height() // 2))
        if self.focused and (pygame.time.get_ticks() // 500) % 2 == 0:
            caret_text = raw[: self.cursor]
            caret_x = min(self.rect.right - 10, self.rect.x + 11 + font.size(caret_text)[0])
            pygame.draw.line(surface, COLORS["text"], (caret_x, self.rect.y + 10), (caret_x, self.rect.bottom - 10), 2)


class QueueLogHandler(logging.Handler):
    def __init__(self, messages):
        super().__init__()
        self.messages = messages

    def emit(self, record):
        self.messages.put(("log", self.format(record)))


class TransferWorker:
    def __init__(self, job, settings, messages):
        self.job = job
        self.settings = settings
        self.messages = messages
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.loop = None
        self.task = None

    def start(self):
        self.thread.start()

    def _run(self):
        handler = QueueLogHandler(self.messages)
        handler.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s]%(message)s"))
        utils.logger.setLevel(logging.INFO)
        utils.logger.propagate = False
        utils.logger.addHandler(handler)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.task = self.loop.create_task(run_transfer(self.job, self.settings))
        try:
            self.loop.run_until_complete(self.task)
        except asyncio.CancelledError:
            self.messages.put(("cancelled", "任务已停止，已完成内容将保留。"))
        except Exception as exc:
            self.messages.put(("log", traceback.format_exc()))
            self.messages.put(("failed", str(exc)))
        else:
            self.messages.put(("completed", "任务已全部完成。"))
        finally:
            utils.logger.removeHandler(handler)
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()
            self.loop = None
            self.task = None

    def cancel(self):
        if self.loop and self.task and not self.task.done():
            self.loop.call_soon_threadsafe(self.task.cancel)

    def is_alive(self):
        return self.thread.is_alive()


class DesktopApp:
    WIDTH = 1180
    HEIGHT = 820

    def __init__(self, demo=False, headless=False):
        pygame.init()
        flags = 0 if headless else pygame.RESIZABLE
        self.screen = pygame.display.set_mode((self.WIDTH, self.HEIGHT), flags)
        try:
            pygame.scrap.init()
        except pygame.error:
            pass
        pygame.display.set_caption("百度网盘批量转存助手")
        self.clock = pygame.time.Clock()
        self.font = choose_font(15)
        self.small = choose_font(13)
        self.medium = choose_font(17, True)
        self.large = choose_font(29, True)
        self.metric_font = choose_font(23, True)
        self.mono = choose_font(13)
        self.running = True
        self.headless = headless
        self.active_tab = 0
        self.messages = queue.Queue()
        self.logs = []
        self.log_scroll = 0
        self.worker = None
        self.batch_count = 0
        self.file_count = 0
        self.status = "就绪"
        self.stage = "等待开始"
        self.banner = ""
        self.banner_color = COLORS["notice"]
        self.fields = {
            "url": TextField(placeholder="https://pan.baidu.com/s/...?...pwd=...."),
            "password": TextField(placeholder="链接已包含提取码时可留空"),
            "destination": TextField(placeholder="例如 /纸样2000"),
            "resume": TextField(placeholder="例如 /纸样2000/儿童/A4；首次运行留空"),
            "config": TextField("config.yaml", "config.yaml（可选）"),
            "cookie": TextField(placeholder="仅本次运行使用，不写入设置", password=True),
        }
        defaults = TransferSettings()
        self.advanced = {
            "max_files_per_request": TextField(defaults.max_files_per_request, numeric=True),
            "request_interval_min": TextField(defaults.request_interval_min, numeric=True),
            "request_interval_max": TextField(defaults.request_interval_max, numeric=True),
            "rest_after_min_requests": TextField(defaults.rest_after_min_requests, numeric=True),
            "rest_after_max_requests": TextField(defaults.rest_after_max_requests, numeric=True),
            "rest_duration_min": TextField(defaults.rest_duration_min, numeric=True),
            "rest_duration_max": TextField(defaults.rest_duration_max, numeric=True),
            "max_retries": TextField(defaults.max_retries, numeric=True),
            "http_total_timeout": TextField(defaults.http_total_timeout, numeric=True),
            "http_connect_timeout": TextField(defaults.http_connect_timeout, numeric=True),
            "http_read_timeout": TextField(defaults.http_read_timeout, numeric=True),
        }
        self.show_cookie = False
        self.tab_rects = []
        self.start_rect = pygame.Rect(0, 0, 142, 44)
        self.stop_rect = pygame.Rect(0, 0, 100, 44)
        self.browse_rect = pygame.Rect(0, 0, 84, 42)
        self.eye_rect = pygame.Rect(0, 0, 68, 42)
        if demo:
            self._demo()
        self._set_icon()

    def _set_icon(self):
        path = resource_path("assets/app.png")
        if os.path.isfile(path):
            try:
                pygame.display.set_icon(pygame.image.load(path))
            except pygame.error:
                pass

    def _demo(self):
        self.fields["url"].set_value("https://pan.baidu.com/s/1example?pwd=1234")
        self.fields["destination"].set_value("/纸样2000")
        self.batch_count = 18
        self.file_count = 8764
        self.status = "运行中"
        self.stage = "限速等待"

    def _draw_text(self, text, position, font=None, color=None):
        image = (font or self.font).render(str(text), True, color or COLORS["text"])
        self.screen.blit(image, position)
        return image.get_rect(topleft=position)

    def _draw_button(self, rect, text, primary=False, enabled=True):
        hovered = rect.collidepoint(pygame.mouse.get_pos()) and enabled
        if not enabled:
            color = "#172033"
            text_color = "#5d6a80"
        elif primary:
            color = COLORS["primary_hover"] if hovered else COLORS["primary"]
            text_color = COLORS["text"]
        else:
            color = "#1a2a44" if hovered else "#142238"
            text_color = COLORS["text"]
        pygame.draw.rect(self.screen, color, rect, border_radius=8)
        pygame.draw.rect(self.screen, COLORS["border"], rect, 1, border_radius=8)
        image = self.medium.render(text, True, text_color)
        self.screen.blit(image, image.get_rect(center=rect.center))

    def _layout(self):
        width, height = self.screen.get_size()
        margin = 28
        # Keep an explicit gap below the 42 px tab strip (y=182..224).
        # Without it, the panel border cuts through the rounded tab buttons.
        panel = pygame.Rect(margin, 232, width - margin * 2, height - 314)
        self.start_rect = pygame.Rect(width - margin - 142, height - 62, 142, 44)
        self.stop_rect = pygame.Rect(self.start_rect.x - 112, height - 62, 100, 44)
        return width, height, margin, panel

    def draw(self):
        width, height, margin, panel = self._layout()
        self.screen.fill(COLORS["bg"])
        self._draw_text("百度网盘批量转存助手", (margin, 22), self.large)
        self._draw_text(
            "目录保持完整 · 接近上限智能组批 · 可控限速与断点续传",
            (margin, 62),
            self.font,
            COLORS["muted"],
        )
        status_w = self.font.size("●  " + self.status)[0] + 28
        status_rect = pygame.Rect(width - margin - status_w, 28, status_w, 34)
        pygame.draw.rect(self.screen, "#123b32", status_rect, border_radius=17)
        pygame.draw.rect(self.screen, "#1b6b58", status_rect, 1, border_radius=17)
        self._draw_text("●  " + self.status, (status_rect.x + 14, status_rect.y + 7), self.font, "#67e8b5")

        metrics = [
            (str(self.batch_count), "成功批次"),
            (f"{self.file_count:,}", "已转存文件"),
            (self.stage, "当前阶段"),
        ]
        card_w = (width - margin * 2 - 24) // 3
        for index, (value, caption) in enumerate(metrics):
            rect = pygame.Rect(margin + index * (card_w + 12), 96, card_w, 82)
            pygame.draw.rect(self.screen, COLORS["card"], rect, border_radius=12)
            pygame.draw.rect(self.screen, COLORS["border"], rect, 1, border_radius=12)
            self._draw_text(value, (rect.x + 18, rect.y + 13), self.metric_font)
            self._draw_text(caption, (rect.x + 18, rect.y + 49), self.small, COLORS["muted"])

        tab_names = ["转存任务", "限速与高级参数", "运行日志"]
        self.tab_rects = []
        x = margin
        for index, name in enumerate(tab_names):
            tw = self.font.size(name)[0] + 42
            rect = pygame.Rect(x, 182, tw, 42)
            self.tab_rects.append(rect)
            color = "#182740" if index == self.active_tab else "#101a2b"
            pygame.draw.rect(self.screen, color, rect, border_radius=8)
            if index == self.active_tab:
                pygame.draw.rect(self.screen, COLORS["primary"], (rect.x, rect.bottom - 3, rect.w, 3))
            self._draw_text(name, (rect.x + 21, rect.y + 11), self.font, COLORS["text"] if index == self.active_tab else COLORS["muted"])
            x += tw + 6

        pygame.draw.rect(self.screen, COLORS["panel"], panel, border_radius=12)
        pygame.draw.rect(self.screen, COLORS["border"], panel, 1, border_radius=12)
        if self.active_tab == 0:
            self._draw_basic(panel)
        elif self.active_tab == 1:
            self._draw_advanced(panel)
        else:
            self._draw_logs(panel)

        if self.banner:
            banner = pygame.Rect(margin, height - 62, max(300, self.stop_rect.x - margin - 18), 44)
            pygame.draw.rect(self.screen, self.banner_color, banner, border_radius=8)
            text = self.banner
            while text and self.font.size(text)[0] > banner.w - 24:
                text = text[:-2] + "…"
            self._draw_text(text, (banner.x + 12, banner.y + 12), self.font)
        else:
            bar = pygame.Rect(margin, height - 38, max(300, self.stop_rect.x - margin - 18), 8)
            pygame.draw.rect(self.screen, "#101a2b", bar, border_radius=4)
            if self.worker and self.worker.is_alive():
                span = max(44, bar.w // 5)
                offset = (pygame.time.get_ticks() // 4) % (bar.w + span) - span
                pygame.draw.rect(self.screen, COLORS["success"], (bar.x + offset, bar.y, span, bar.h), border_radius=4)
        active = bool(self.worker and self.worker.is_alive())
        self._draw_button(self.stop_rect, "停止", enabled=active)
        self._draw_button(self.start_rect, "开始转存", primary=True, enabled=not active)
        pygame.display.flip()

    def _draw_basic(self, panel):
        labels = [
            ("分享链接", "url"),
            ("提取码", "password"),
            ("目标目录", "destination"),
            ("断点路径", "resume"),
            ("配置文件", "config"),
            ("Cookie", "cookie"),
        ]
        label_x = panel.x + 28
        field_x = panel.x + 158
        field_w = panel.w - 296
        y = panel.y + 28
        for label, key in labels:
            self._draw_text(label, (label_x, y + 11), self.medium, "#c8d4e6")
            extra = 96 if key in ("config", "cookie") else 0
            field = self.fields[key]
            field.rect = pygame.Rect(field_x, y, field_w - extra, 42)
            field.password = key == "cookie" and not self.show_cookie
            field.draw(self.screen, self.font)
            if key == "config":
                self.browse_rect = pygame.Rect(field.rect.right + 12, y, 84, 42)
                self._draw_button(self.browse_rect, "浏览")
            elif key == "cookie":
                self.eye_rect = pygame.Rect(field.rect.right + 12, y, 84, 42)
                self._draw_button(self.eye_rect, "隐藏" if self.show_cookie else "显示")
            y += 57
        note = pygame.Rect(panel.x + 28, panel.bottom - 72, panel.w - 56, 44)
        pygame.draw.rect(self.screen, COLORS["notice"], note, border_radius=8)
        self._draw_text("Cookie 不会显示在日志中，也不会保存到应用设置。建议优先选择本地 config.yaml。", (note.x + 12, note.y + 12), self.small, "#a8c8ea")

    def _draw_advanced(self, panel):
        rows = [
            ("每批文件上限", "max_files_per_request", "1–500；目录不会为了凑批而拆分", " 个"),
            ("请求间隔最小", "request_interval_min", "每次请求前随机等待", " 秒"),
            ("请求间隔最大", "request_interval_max", "需大于等于最小值", " 秒"),
            ("休息触发最少请求", "rest_after_min_requests", "随机请求次数区间", " 次"),
            ("休息触发最多请求", "rest_after_max_requests", "需大于等于最少次数", " 次"),
            ("休息时长最小", "rest_duration_min", "随机休息区间", " 秒"),
            ("休息时长最大", "rest_duration_max", "需大于等于最小时长", " 秒"),
            ("错误重试上限", "max_retries", "网络与临时错误", " 次"),
            ("HTTP 总超时", "http_total_timeout", "单次读取的总时限", " 秒"),
            ("连接超时", "http_connect_timeout", "不得超过总超时", " 秒"),
            ("读取超时", "http_read_timeout", "不得超过总超时", " 秒"),
        ]
        y = panel.y + 18
        for label, key, hint, suffix in rows:
            self._draw_text(label, (panel.x + 28, y + 9), self.font, "#c8d4e6")
            field = self.advanced[key]
            field.rect = pygame.Rect(panel.x + 220, y, 170, 38)
            field.draw(self.screen, self.font, suffix)
            self._draw_text(hint, (panel.x + 416, y + 10), self.small, COLORS["muted"])
            y += 43

    def _draw_logs(self, panel):
        self._draw_text("实时输出（不会记录 Cookie）", (panel.x + 20, panel.y + 16), self.small, COLORS["muted"])
        log_rect = pygame.Rect(panel.x + 16, panel.y + 44, panel.w - 32, panel.h - 60)
        pygame.draw.rect(self.screen, COLORS["input"], log_rect, border_radius=8)
        previous = self.screen.get_clip()
        self.screen.set_clip(log_rect.inflate(-12, -10))
        visible = max(1, (log_rect.h - 14) // 20)
        start = max(0, len(self.logs) - visible - self.log_scroll)
        lines = self.logs[start : start + visible]
        y = log_rect.y + 8
        for line in lines:
            text = line.replace("\t", "    ")
            while text and self.mono.size(text)[0] > log_rect.w - 20:
                text = text[:-2] + "…"
            self._draw_text(text, (log_rect.x + 10, y), self.mono, "#bfd0e8")
            y += 20
        self.screen.set_clip(previous)

    def _all_fields(self):
        return list(self.fields.values()) + list(self.advanced.values())

    def _visible_fields(self):
        if self.active_tab == 0:
            return list(self.fields.values())
        if self.active_tab == 1:
            return list(self.advanced.values())
        return []

    def _handle_event(self, event):
        if event.type == pygame.QUIT:
            if self.worker and self.worker.is_alive():
                self.worker.cancel()
            self.running = False
            return
        if event.type == pygame.VIDEORESIZE:
            self.screen = pygame.display.set_mode((max(960, event.w), max(720, event.h)), pygame.RESIZABLE)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for index, rect in enumerate(self.tab_rects):
                if rect.collidepoint(event.pos):
                    self.active_tab = index
                    return
            active = bool(self.worker and self.worker.is_alive())
            if self.start_rect.collidepoint(event.pos) and not active:
                self._start()
                return
            if self.stop_rect.collidepoint(event.pos) and active:
                self.status = "正在停止"
                self.stage = "取消中"
                self.worker.cancel()
                return
            if self.active_tab == 0 and self.browse_rect.collidepoint(event.pos):
                self._browse()
                return
            if self.active_tab == 0 and self.eye_rect.collidepoint(event.pos):
                self.show_cookie = not self.show_cookie
                return
        if event.type == pygame.MOUSEWHEEL and self.active_tab == 2:
            visible = max(1, (self.screen.get_height() - 360) // 20)
            self.log_scroll = max(0, min(max(0, len(self.logs) - visible), self.log_scroll + event.y))
        for field in self._visible_fields():
            field.handle(event)

    def _browse(self):
        try:
            script = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$dialog=New-Object System.Windows.Forms.OpenFileDialog;"
                "$dialog.Title='选择配置文件';"
                "$dialog.Filter='YAML 配置 (*.yaml;*.yml)|*.yaml;*.yml|所有文件 (*.*)|*.*';"
                "if($dialog.ShowDialog() -eq 'OK'){[Console]::Write($dialog.FileName)}"
            )
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            path = completed.stdout.strip()
            if path:
                self.fields["config"].set_value(path)
        except Exception as exc:
            self._show_error("无法打开文件选择窗口：%s" % exc)

    def _settings(self):
        values = {}
        for key, field in self.advanced.items():
            if not field.value:
                raise ValueError("高级参数不能为空")
            values[key] = int(field.value)
        return TransferSettings(**values).validate()

    def _job(self):
        return TransferJob(
            share_url=self.fields["url"].value,
            cookie=self.fields["cookie"].value,
            config_path=self.fields["config"].value,
            password=self.fields["password"].value,
            destination=self.fields["destination"].value,
            resume_path=self.fields["resume"].value,
        )

    def _start(self):
        try:
            settings = self._settings()
            job = self._job()
            parse_share_url(job.share_url)
            load_cookie(job.cookie, job.config_path)
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.logs.clear()
        self.log_scroll = 0
        self.batch_count = 0
        self.file_count = 0
        self.status = "运行中"
        self.stage = "连接中"
        self.banner = ""
        self.worker = TransferWorker(job, settings, self.messages)
        self.worker.start()

    def _show_error(self, message):
        self.banner = message
        self.banner_color = "#542533"
        self.status = "需要处理"

    def _drain_messages(self):
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.logs.extend(payload.splitlines())
                self.logs = self.logs[-5000:]
                self.log_scroll = 0
                if "Counting progress:" in payload:
                    self.stage = "递归统计"
                elif "Waiting" in payload or "resting" in payload:
                    self.stage = "限速等待"
                elif "Transfer packed batch" in payload and "success" in payload:
                    self.stage = "批次完成"
                    self.batch_count += 1
                    match = re.search(r"(\d+) recursive files", payload)
                    if match:
                        self.file_count += int(match.group(1))
            elif kind == "completed":
                self.status = "已完成"
                self.stage = "全部完成"
                self.banner = payload
                self.banner_color = "#123b32"
            elif kind == "cancelled":
                self.status = "已停止"
                self.stage = "已保留进度"
                self.banner = payload
                self.banner_color = COLORS["notice"]
            elif kind == "failed":
                self.active_tab = 2
                self.stage = "运行异常"
                self._show_error(payload)

    def run(self):
        pygame.key.start_text_input()
        while self.running:
            for event in pygame.event.get():
                self._handle_event(event)
            self._drain_messages()
            self.draw()
            self.clock.tick(60)
        pygame.quit()
        return 0

    def capture(self, path):
        self.draw()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        pygame.image.save(self.screen, path)
        return str(Path(path).resolve())


def main():
    return DesktopApp().run()


if __name__ == "__main__":
    raise SystemExit(main())
