from PyQt6.QtCore import QTimer

def _set_progress_state(self, state: str):
    return


def _set_start_button_state(self, state: str):
    return


def _format_elapsed_duration(seconds: float) -> str:
    total_seconds = max(0.0, float(seconds or 0.0))
    if total_seconds >= 60:
        minutes = int(total_seconds // 60)
        remainder = total_seconds - minutes * 60
        return f"{minutes}:{remainder:04.1f}"
    return f"{total_seconds:.1f}"


def update_workflow_mode_description(self, index: int | None = None):
    """根据翻译流程模式更新翻译页标题下方的介绍文字。"""
    if not hasattr(self, "translation_page_subtitle"):
        return

    if index is None:
        if hasattr(self, "workflow_mode_combo"):
            index = self.workflow_mode_combo.currentIndex()
        else:
            index = 0

    mode_keys = {
        0: "Normal Translation",
        1: "Export Translation",
        2: "Export Original Text",
        3: "Translate JSON Only",
        4: "Import Translation and Render",
        5: "Colorize Only",
        6: "Upscale Only",
        7: "Inpaint Only",
        8: "Replace Translation",
    }
    tip_keys = {
        0: "Tip: Standard translation pipeline with detection, OCR, translation and rendering",
        1: "Tip: After exporting, check manga_translator_work/translations/ for imagename_translated.txt files",
        2: "Tip: After exporting, manually translate imagename_original.txt in manga_translator_work/originals/, then use 'Import Translation and Render' mode",
        3: "Tip: Requires existing JSON data. The app reads original text from JSON, translates it, writes results back to JSON, and deletes imagename_original.txt after success",
        4: "Tip: Will read TXT files from manga_translator_work/originals/ or translations/ and render (prioritize _original.txt)",
        5: "Tip: Only colorize images, no detection, OCR, translation or rendering",
        6: "Tip: Only upscale images, no detection, OCR, translation or rendering",
        7: "Tip: Detect text regions and inpaint to output clean images, no translation or rendering",
        8: "Tip: Place translated images in manga_translator_work/translated_images with matching filenames. The app extracts translated text, matches regions on raw images, inpaints originals, and renders translated text.",
    }
    try:
        local_json_export = bool(
            self.config_service.get_config().cli.export_from_local_json
        )
    except Exception:
        local_json_export = False
    if local_json_export:
        tip_keys[1] = "Tip: Reads existing local JSON and exports translated text only; no detection, OCR, API translation, or JSON write-back"
        tip_keys[2] = "Tip: Reads existing local JSON and exports original text only; no detection, OCR, API translation, or JSON write-back"
    mode_key = mode_keys.get(index, mode_keys[0])
    tip_key = tip_keys.get(index, tip_keys[0])
    if hasattr(self, "translation_page_title"):
        self.translation_page_title.setText(self._t(mode_key))
    self.translation_page_subtitle.setText(self._t(tip_key))







def update_progress(self, current: int, total: int, message: str = ""):
    """更新进度条。"""
    progress_state = (int(current), int(total), str(message or ""))
    if getattr(self, "_last_progress_state", None) == progress_state:
        return
    self._last_progress_state = progress_state

    if total > 0:
        if hasattr(self, "progress_stats_widget"):
            self.progress_stats_widget.setVisible(False)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        percentage = int((current / total) * 100) if total > 0 else 0
        self.progress_count_label.setText(f"{current}/{total} ({percentage}%)")
        if hasattr(self, "progress_info_label"):
            self.progress_info_label.setText(message or f"已完成 {current}/{total}")

        if not getattr(self, "_progress_active", False):
            self._progress_active = True
            _set_progress_state(self, "active")
    else:
        self._progress_active = False
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_count_label.setText("0/0 (0%)")
        if hasattr(self, "progress_info_label"):
            self.progress_info_label.setText("")
        if hasattr(self, "progress_stats_widget"):
            self.progress_stats_widget.setVisible(False)
        _set_progress_state(self, "idle")


def show_translation_stats(self, elapsed_seconds: float, image_count: int):
    """在任务完成后保留进度并显示本次翻译统计。"""
    elapsed_seconds = max(0.0, float(elapsed_seconds or 0.0))
    image_count = max(0, int(image_count or 0))
    average_seconds = elapsed_seconds / image_count if image_count > 0 else 0.0

    if image_count > 0:
        self.progress_bar.setMaximum(image_count)
        self.progress_bar.setValue(image_count)
        self.progress_count_label.setText(f"{image_count}/{image_count} (100%)")
    else:
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_count_label.setText("0/0 (0%)")

    if hasattr(self, "progress_info_label"):
        self.progress_info_label.setText(self._t("Task Completed"))
    if hasattr(self, "progress_elapsed_label"):
        self.progress_elapsed_label.setText(
            self._t(
                "Translation elapsed time: {duration}",
                duration=_format_elapsed_duration(elapsed_seconds),
            )
        )
    if hasattr(self, "progress_average_label"):
        self.progress_average_label.setText(
            self._t(
                "Average translation speed: {average}",
                average=f"{average_seconds:.1f}",
            )
        )
    if hasattr(self, "progress_stats_widget"):
        self.progress_stats_widget.setVisible(True)


def reset_progress(self):
    """重置进度条为初始状态（灰色）。"""
    self._last_progress_state = None
    self._progress_active = False
    self.progress_bar.setMaximum(100)
    self.progress_bar.setValue(0)
    self.progress_count_label.setText("0/0 (0%)")
    if hasattr(self, "progress_info_label"):
        self.progress_info_label.setText("")
    if hasattr(self, "progress_stats_widget"):
        self.progress_stats_widget.setVisible(False)
    if hasattr(self, "progress_elapsed_label"):
        self.progress_elapsed_label.setText("")
    if hasattr(self, "progress_average_label"):
        self.progress_average_label.setText("")
    _set_progress_state(self, "idle")


def on_task_queue_changed(self, waiting_count: int):
    """Show whether submitted tasks are waiting behind the active task."""
    label = getattr(self, "task_queue_status_label", None)
    if label is None:
        return
    waiting_count = max(0, int(waiting_count or 0))
    if waiting_count:
        label.setText(self._t("Queued tasks: {count}", count=waiting_count))
    elif self.controller.state_manager.is_translating():
        label.setText(self._t("Processing current task"))
    else:
        label.setText(self._t("Queue is empty"))


def on_html_sources_changed(self, pending_count: int):
    """Show that HTML/URL inputs are registered but not downloaded yet."""
    label = getattr(self, "html_source_status_label", None)
    if label is None:
        return
    pending_count = max(0, int(pending_count or 0))
    if pending_count:
        label.setText(
            self._t("Pending HTML sources: {count}", count=pending_count)
        )
    else:
        label.setText(self._t("No pending HTML sources"))


def on_translation_state_changed(self, is_translating: bool):
    """根据翻译状态更新开始/停止按钮。"""
    # 当前任务使用提交时的输入快照，因此翻译期间仍可准备下一批文件。
    for name in (
        "add_files_button",
        "add_folder_button",
        "import_html_button",
        "import_url_button",
        "clear_list_button",
        "url_input",
    ):
        widget = getattr(self, name, None)
        if widget is not None:
            widget.setEnabled(True)

    env_page = getattr(self, "env_page", None)
    if env_page is not None:
        env_page.setEnabled(not is_translating)

    file_list = getattr(self, "file_list", None)
    if file_list is not None and hasattr(file_list, "set_remove_enabled"):
        file_list.set_remove_enabled(True)

    if is_translating:
        self.start_button.setEnabled(True)
        self.start_button.setText(self._t("Queue Translation"))
        _set_start_button_state(self, "queue")
        stop_button = getattr(self, "stop_button", None)
        if stop_button is not None:
            stop_button.setEnabled(True)
            stop_button.setText(self._t("Stop Translation"))
    else:
        self.start_button.setEnabled(True)
        _set_start_button_state(self, "ready")
        stop_button = getattr(self, "stop_button", None)
        if stop_button is not None:
            stop_button.setEnabled(False)

        try:
            self.start_button.clicked.disconnect()
        except TypeError:
            pass
        self.start_button.clicked.connect(self.controller.start_backend_task)
        self.update_start_button_text()


def enable_stop_button(self):
    """启用停止按钮（延迟调用）。"""
    if (
        self.controller.state_manager.is_translating()
        and not getattr(self.controller, "_stop_requested", False)
    ):
        stop_button = getattr(self, "stop_button", None)
        if stop_button is not None:
            stop_button.setEnabled(True)
            stop_button.setText(self._t("Stop Translation"))


def set_stopping_state(self):
    """设置按钮为“停止中...”状态，避免重复点击。"""
    self.start_button.setEnabled(False)
    stop_button = getattr(self, "stop_button", None)
    if stop_button is not None:
        stop_button.setEnabled(False)
        stop_button.setText(self._t("Stopping..."))


def sync_workflow_mode_from_config(self):
    """从配置同步下拉框的选择。"""
    try:
        config = self.config_service.get_config()
        self.workflow_mode_combo.blockSignals(True)
        try:
            if config.cli.replace_translation:
                self.workflow_mode_combo.setCurrentIndex(8)
            elif config.cli.inpaint_only:
                self.workflow_mode_combo.setCurrentIndex(7)
            elif config.cli.upscale_only:
                self.workflow_mode_combo.setCurrentIndex(6)
            elif config.cli.colorize_only:
                self.workflow_mode_combo.setCurrentIndex(5)
            elif config.cli.load_text:
                self.workflow_mode_combo.setCurrentIndex(4)
            elif config.cli.translate_json_only:
                self.workflow_mode_combo.setCurrentIndex(3)
            elif config.cli.template:
                self.workflow_mode_combo.setCurrentIndex(2)
            elif config.cli.generate_and_export:
                self.workflow_mode_combo.setCurrentIndex(1)
            else:
                self.workflow_mode_combo.setCurrentIndex(0)
        finally:
            self.workflow_mode_combo.blockSignals(False)
        update_workflow_mode_description(self, self.workflow_mode_combo.currentIndex())
    except Exception as e:
        print(f"Error syncing workflow mode: {e}")


def on_workflow_mode_changed(self, index: int):
    """处理翻译流程模式改变并持久化。"""
    config = self.config_service.get_config()

    config.cli.load_text = False
    config.cli.translate_json_only = False
    config.cli.template = False
    config.cli.generate_and_export = False
    config.cli.colorize_only = False
    config.cli.upscale_only = False
    config.cli.inpaint_only = False
    config.cli.replace_translation = False

    if index == 1:
        config.cli.generate_and_export = True
    elif index == 2:
        config.cli.template = True
    elif index == 3:
        config.cli.translate_json_only = True
    elif index == 4:
        config.cli.load_text = True
    elif index == 5:
        config.cli.colorize_only = True
    elif index == 6:
        config.cli.upscale_only = True
    elif index == 7:
        config.cli.inpaint_only = True
    elif index == 8:
        config.cli.replace_translation = True

    self.config_service.set_config(config)
    self.config_service.save_config_file()
    self.update_start_button_text()
    update_workflow_mode_description(self, index)


def update_start_button_text(self):
    """根据当前模式更新开始按钮文案。"""
    if self.controller.state_manager.is_translating():
        return

    try:
        config = self.config_service.get_config()
        if config.cli.replace_translation:
            self.start_button.setText(self._t("Start Replace Translation"))
        elif config.cli.inpaint_only:
            self.start_button.setText(self._t("Start Inpainting"))
        elif config.cli.upscale_only:
            self.start_button.setText(self._t("Start Upscaling"))
        elif config.cli.colorize_only:
            self.start_button.setText(self._t("Start Colorizing"))
        elif config.cli.translate_json_only:
            self.start_button.setText(self._t("Start JSON Translation"))
        elif config.cli.load_text:
            self.start_button.setText(self._t("Import Translation and Render"))
        elif config.cli.template:
            self.start_button.setText(self._t("Generate Original Text Template"))
        elif config.cli.generate_and_export:
            self.start_button.setText(self._t("Export Translation"))
        else:
            self.start_button.setText(self._t("Start Translation"))
    except Exception as e:
        self.start_button.setText(self._t("Start Translation"))
        print(f"Could not update button text: {e}")
