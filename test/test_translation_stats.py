import _bootstrap  # noqa: I001

from types import SimpleNamespace

from desktop_qt_ui.ui.main_page import runtime


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _ProgressBar:
    def __init__(self):
        self.maximum = None
        self.value = None

    def setMaximum(self, value):
        self.maximum = value

    def setValue(self, value):
        self.value = value


class _VisibleWidget:
    def __init__(self):
        self.visible = False

    def setVisible(self, value):
        self.visible = bool(value)


def _view():
    translations = {
        "Task Completed": "任务完成",
        "Translation elapsed time: {duration}": "本次耗时：{duration} 秒",
        "Average translation speed: {average}": "单张均速：{average} 秒/张",
    }
    return SimpleNamespace(
        progress_bar=_ProgressBar(),
        progress_count_label=_Label(),
        progress_info_label=_Label(),
        progress_stats_widget=_VisibleWidget(),
        progress_elapsed_label=_Label(),
        progress_average_label=_Label(),
        _t=lambda key, **kwargs: translations[key].format(**kwargs),
    )


def test_show_translation_stats_displays_elapsed_and_average_speed():
    view = _view()

    runtime.show_translation_stats(view, 62.3, 3)

    assert view.progress_bar.maximum == 3
    assert view.progress_bar.value == 3
    assert view.progress_count_label.text == "3/3 (100%)"
    assert view.progress_info_label.text == "任务完成"
    assert view.progress_elapsed_label.text == "本次耗时：1:02.3 秒"
    assert view.progress_average_label.text == "单张均速：20.8 秒/张"
    assert view.progress_stats_widget.visible is True


def test_update_progress_hides_previous_completion_stats():
    view = _view()
    view.progress_stats_widget.visible = True

    runtime.update_progress(view, 0, 4, "处理中")

    assert view.progress_stats_widget.visible is False
    assert view.progress_count_label.text == "0/4 (0%)"
