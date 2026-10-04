"""单一文件工作区：文件夹占一行，展开后在原位排列图片缩略图。"""

from __future__ import annotations

import os

from PyQt6.QtCore import (
    QAbstractListModel, QItemSelectionModel, QModelIndex, QPoint, QRect,
    QSignalBlocker, QSize, Qt, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFontMetrics, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QListView, QStackedWidget, QStyle,
    QStyleOptionButton, QStyleOptionViewItem, QStyledItemDelegate, QVBoxLayout, QWidget,
)
from qfluentwidgets import FluentIcon as FIF, isDarkTheme, setFont, themeColor

from services.file_list_data_service import KIND_FOLDER, KIND_IMAGE, canonical_path_key
from ui.widgets.file_list_view import (
    KIND_ROLE, NODE_ROLE, PATH_ROLE, THUMBNAIL_ROLE, FileListView, _single_shot,
)


class _WorkspaceModel(QAbstractListModel):
    """只投影展开的目录，复用原文件模型与缩略图缓存。"""

    def __init__(self, source, parent):
        super().__init__(parent)
        self.source = source
        self.expanded = set()
        self._paths = list(source.visible_paths())
        self._rows = {}
        self._removal = None
        self._reindex()
        source.modelAboutToBeReset.connect(self.beginResetModel)
        source.modelReset.connect(self._reset)
        source.rowsAboutToBeRemoved.connect(self._begin_remove)
        source.rowsRemoved.connect(self._end_remove)
        source.dataChanged.connect(self._data_changed)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._paths)

    def data(self, index, role=int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid() or not 0 <= index.row() < len(self._paths):
            return None
        return self.source.data(self.source.index_for_path(self._paths[index.row()]), role)

    def index_for_path(self, path):
        row = self._rows.get(canonical_path_key(path)) if path else None
        return self.index(row, 0) if row is not None else QModelIndex()

    def _reindex(self):
        self._rows = {canonical_path_key(path): row for row, path in enumerate(self._paths)}

    def _reset(self):
        self.expanded = {key for key in self.expanded if self.source.item_for_path(key)}
        self._paths = list(self.source.visible_paths(self.expanded))
        self._reindex()
        self.endResetModel()

    def toggle_folder(self, path):
        node = self.source.item_for_path(path)
        if node is None or node.kind != KIND_FOLDER:
            return
        self.beginResetModel()
        key = canonical_path_key(path)
        if key in self.expanded:
            self.expanded.remove(key)
        else:
            self.expanded.add(key)
        self._reset()

    def _begin_remove(self, parent, first, last):
        rows = []

        def collect(item):
            if item is None:
                return
            key = canonical_path_key(item.path)
            row = self._rows.get(key)
            if row is not None:
                rows.append(row)
            self.expanded.discard(key)
            for child in item.children:
                collect(child)

        for row in range(first, last + 1):
            collect(self.source.index(row, 0, parent).data(NODE_ROLE))
        if rows:
            self._removal = (min(rows), max(rows))
            self.beginRemoveRows(QModelIndex(), *self._removal)

    def _end_remove(self, *_args):
        if self._removal is None:
            return
        first, last = self._removal
        del self._paths[first:last + 1]
        self._reindex()
        self._removal = None
        self.endRemoveRows()

    def _data_changed(self, top, bottom, roles):
        for row in range(top.row(), bottom.row() + 1):
            path = self.source.index(row, 0, top.parent()).data(PATH_ROLE)
            index = self.index_for_path(path)
            if index.isValid():
                self.dataChanged.emit(index, index, roles)


class _WorkspaceDelegate(QStyledItemDelegate):
    TILE_SIZE = QSize(140, 180)
    FOLDER_HEIGHT = 44

    def __init__(self, parent, translate):
        super().__init__(parent)
        self._t = translate

    def sizeHint(self, _option, index):
        if index.data(KIND_ROLE) == KIND_FOLDER:
            # 整行目录和缩略图自动换行，共享一个滚动区域。
            return QSize(max(1, self.parent().viewport().width()), self.FOLDER_HEIGHT)
        return self.TILE_SIZE

    @staticmethod
    def folder_indent(index):
        node = index.data(NODE_ROLE)
        depth = 0
        while node and node.parent:
            depth += 1
            node = node.parent
        return min(depth * 18, 144)

    def arrow_rect(self, rect, index):
        return QRect(rect.left() + self.folder_indent(index) + 4, rect.center().y() - 11, 22, 22)

    def check_rect(self, rect, index):
        if index.data(KIND_ROLE) == KIND_FOLDER:
            return QRect(self.arrow_rect(rect, index).right() + 5, rect.center().y() - 8, 16, 16)
        return QRect(rect.left() + 12, rect.top() + 12, 16, 16)

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        painter.save()
        painter.setFont(option.font)
        folder = index.data(KIND_ROLE) == KIND_FOLDER
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect.adjusted(4, 3, -4, -3)
        if selected or hovered or not folder:
            fill = themeColor() if selected else QColor(127, 127, 127)
            fill.setAlpha(32 if selected else (18 if hovered else 7))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 6, 6)

        check = QStyleOptionButton()
        check.rect = self.check_rect(option.rect, index)
        check.state = QStyle.StateFlag.State_Enabled | (
            QStyle.StateFlag.State_On if selected else QStyle.StateFlag.State_Off
        )
        self.parent().style().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, check, painter)
        text_color = QColor("#eeeeee" if isDarkTheme() else "#222222")
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        metrics = QFontMetrics(option.font)
        if folder:
            expanded = canonical_path_key(index.data(PATH_ROLE)) in index.model().expanded
            arrow = FIF.CHEVRON_DOWN_MED if expanded else FIF.CHEVRON_RIGHT_MED
            arrow.render(painter, self.arrow_rect(option.rect, index).adjusted(5, 5, -5, -5))
            icon_rect = QRect(check.rect.right() + 12, rect.center().y() - 13, 26, 26)
            FIF.FOLDER.render(painter, icon_rect)
            text_rect = rect.adjusted(icon_rect.right() - rect.left() + 12, 0, -12, 0)
            painter.setPen(text_color)
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter),
                             metrics.elidedText(title, Qt.TextElideMode.ElideMiddle, text_rect.width()))
        else:
            image_rect = QRect(rect.left() + 14, rect.top() + 27, rect.width() - 28, 100)
            pixmap = index.data(THUMBNAIL_ROLE)
            if isinstance(pixmap, QPixmap) and not pixmap.isNull():
                pixmap = pixmap.scaled(image_rect.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                painter.drawPixmap(image_rect.center() - QPoint(pixmap.width() // 2, pixmap.height() // 2), pixmap)
            else:
                icon = FIF.PHOTO if index.data(KIND_ROLE) == KIND_IMAGE else FIF.ZIP_FOLDER
                icon.render(painter, QRect(image_rect.center() - QPoint(18, 18), QSize(36, 36)))
            painter.setPen(text_color)
            painter.drawText(QRect(rect.left() + 8, rect.bottom() - 39, rect.width() - 16, 20),
                             int(Qt.AlignmentFlag.AlignCenter),
                             metrics.elidedText(title, Qt.TextElideMode.ElideMiddle, rect.width() - 16))
            node = index.data(NODE_ROLE)
            status = self._t("Translated" if node and node.json_path else "Untranslated")
            if index.data(KIND_ROLE) != KIND_IMAGE:
                status = os.path.splitext(title)[1].lstrip(".").upper()
            painter.setPen(QColor(127, 127, 127))
            painter.drawText(QRect(rect.left() + 8, rect.bottom() - 19, rect.width() - 16, 18),
                             int(Qt.AlignmentFlag.AlignCenter), status)
        painter.restore()


class _WorkspaceView(QListView):
    state_restored = pyqtSignal()
    file_selected = pyqtSignal(str)

    def __init__(self, tree, parent, translate):
        super().__init__(parent)
        self.tree = tree
        self.workspace_model = _WorkspaceModel(tree.catalog_model, self)
        self._saved_paths = []
        self._saved_current = None
        self._saved_top = None
        self._saved_offset = 0
        self._saved_scroll = 0
        self._scheduled = False
        self._pressed_control = None
        self._control_press_position = QPoint()
        self._control_dragged = False
        self.workspace_model.modelAboutToBeReset.connect(self._capture_state)
        self.workspace_model.rowsAboutToBeRemoved.connect(self._capture_state)
        self.setModel(self.workspace_model)
        self._delegate = _WorkspaceDelegate(self, translate)
        self.setItemDelegate(self._delegate)
        setFont(self, 14)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QListView { background: transparent; border: none; }")
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.workspace_model.modelReset.connect(self._restore_state)
        self.workspace_model.rowsRemoved.connect(self._restore_state)
        self.verticalScrollBar().valueChanged.connect(self._schedule_thumbnails)
        self.doubleClicked.connect(self._activate)

    def visible_indexes(self):
        # 顺序布局的 y 坐标单调；二分定位首个可见项目。
        low, high = 0, self.model().rowCount()
        while low < high:
            mid = (low + high) // 2
            if self.visualRect(self.model().index(mid, 0)).bottom() < 0:
                low = mid + 1
            else:
                high = mid
        for row in range(low, self.model().rowCount()):
            index = self.model().index(row, 0)
            rect = self.visualRect(index)
            if rect.top() >= self.viewport().height():
                break
            if rect.intersects(self.viewport().rect()):
                yield index

    def _capture_state(self, *_args):
        self._saved_paths = [index.data(PATH_ROLE) for index in self.selectionModel().selectedIndexes()]
        self._saved_current = self.currentIndex().data(PATH_ROLE)
        self._saved_scroll = self.verticalScrollBar().value()
        top = next(self.visible_indexes(), QModelIndex())
        self._saved_top = top.data(PATH_ROLE)
        self._saved_offset = self.visualRect(top).top() if top.isValid() else 0

    def _restore_state(self, *_args):
        self.doItemsLayout()
        with QSignalBlocker(self.selectionModel()):
            for path in self._saved_paths:
                index = self.workspace_model.index_for_path(path)
                if index.isValid():
                    self.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select)
            index = self.workspace_model.index_for_path(self._saved_current)
            if index.isValid():
                self.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        top = self.workspace_model.index_for_path(self._saved_top)
        value = self._saved_scroll
        if top.isValid():
            value = self.verticalScrollBar().value() + self.visualRect(top).top() - self._saved_offset
        self.verticalScrollBar().setValue(value)
        self._schedule_thumbnails()
        self.state_restored.emit()

    def _activate(self, index):
        if index.data(KIND_ROLE) == KIND_FOLDER:
            path = index.data(PATH_ROLE)
            # 打开目录不把它保留为待删除项；勾选目录仍可移除整个目录。
            self.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Deselect)
            self.workspace_model.toggle_folder(path)
        elif index.data(KIND_ROLE) == KIND_IMAGE:
            self.file_selected.emit(index.data(PATH_ROLE))

    def _control_at(self, point):
        index = self.indexAt(point)
        if not index.isValid():
            return None
        rect = self.visualRect(index)
        if self._delegate.check_rect(rect, index).adjusted(-3, -3, 3, 3).contains(point):
            return "check", index.data(PATH_ROLE)
        if index.data(KIND_ROLE) == KIND_FOLDER and self._delegate.arrow_rect(rect, index).contains(point):
            return "folder", index.data(PATH_ROLE)
        return None

    def mousePressEvent(self, event):
        self._pressed_control = self._control_at(event.position().toPoint()) if event.button() == Qt.MouseButton.LeftButton else None
        if self._pressed_control:
            self._control_press_position = event.position().toPoint()
            self._control_dragged = False
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pressed_control:
            # 控件已接管按下事件，移动也不能交给 Qt 用旧起点开始框选。
            distance = (event.position().toPoint() - self._control_press_position).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._control_dragged = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        control = self._pressed_control
        if control and event.button() == Qt.MouseButton.LeftButton:
            self._pressed_control = None
            if not self._control_dragged and control == self._control_at(event.position().toPoint()):
                kind, path = control
                index = self.workspace_model.index_for_path(path)
                if kind == "check":
                    self.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Toggle)
                    self.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
                else:
                    self._activate(index)
                self.viewport().update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._control_at(event.position().toPoint()):
            # 第二次按下会作为双击事件送达，也要接管后续移动和松开。
            self.mousePressEvent(event)
            self._control_dragged = True
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        index = self.currentIndex()
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and index.isValid():
            self._activate(index)
            event.accept()
            return
        super().keyPressEvent(event)

    def _schedule_thumbnails(self, *_args):
        if not self._scheduled:
            self._scheduled = True
            _single_shot(16, self, self._load_thumbnails)

    def _load_thumbnails(self):
        self._scheduled = False
        if self.isVisible():
            for index in self.visible_indexes():
                if index.data(KIND_ROLE) == KIND_IMAGE:
                    self.tree._request_thumbnail(self.tree.catalog_model.index_for_path(index.data(PATH_ROLE)))

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_thumbnails()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.doItemsLayout()
        self._schedule_thumbnails()

    def dragEnterEvent(self, event):
        self.tree.dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.tree.dragMoveEvent(event)

    def dropEvent(self, event):
        self.tree.dropEvent(event)


class FileWorkspace(QWidget):
    selected_remove_requested = pyqtSignal(list)
    clear_requested = pyqtSignal()
    file_selected = pyqtSignal(str)
    files_dropped = pyqtSignal(list)
    selection_count_changed = pyqtSignal(int)

    def __init__(self, parent=None, *, data_service=None, translate=None):
        super().__init__(parent)
        self.tree = FileListView(parent=self, data_service=data_service, thumbnail_size=160)
        self.catalog_model = self.tree.catalog_model
        self.tree._emit_selection_on_click = False
        self.view = _WorkspaceView(self.tree, self, translate or self.tree._t)
        self._remove_enabled = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.view)
        layout.addWidget(self.stack)
        self.tree.files_dropped.connect(self.files_dropped)
        self.view.file_selected.connect(self.file_selected)
        self.view.selectionModel().selectionChanged.connect(self._update_selection_count)
        self.view.state_restored.connect(self._update_selection_count)
        self.catalog_model.modelReset.connect(self._update_content)
        self.catalog_model.rowsRemoved.connect(self._update_content)

    def selected_paths(self):
        return [index.data(PATH_ROLE) for index in self.view.selectionModel().selectedRows()]

    def _update_selection_count(self, *_args):
        self.selection_count_changed.emit(len(self.selected_paths()))

    def clear_action(self):
        if not self._remove_enabled:
            return
        paths = self.selected_paths()
        if not paths:
            self.clear_requested.emit()
            return
        # 目录和子项同时选中时只移除目录，避免重复处理已经移除的后代。
        keys = {canonical_path_key(path) for path in paths}
        roots = []
        for path in paths:
            item = self.catalog_model.item_for_path(path)
            ancestor = item.parent if item else None
            while ancestor and canonical_path_key(ancestor.path) not in keys:
                ancestor = ancestor.parent
            if item and ancestor is None:
                roots.append(path)
        if roots:
            self.selected_remove_requested.emit(roots)

    def _update_content(self, *_args):
        self.stack.setCurrentWidget(self.view if self.catalog_model.rowCount() else self.tree)
        self._update_selection_count()

    def set_snapshot(self, snapshot):
        self.tree.set_snapshot(snapshot)
        self._update_content()

    def set_loading(self, text=None, *, keep_items=False):
        self.tree.set_loading(text, keep_items=keep_items)
        self._update_content()

    def set_error(self, message):
        self.tree.set_error(message)
        self._update_content()

    def remove_file(self, path):
        self.tree.remove_file(path)
        self._update_content()

    def clear(self):
        self.view.workspace_model.expanded.clear()
        self.tree.clear()
        self.view.verticalScrollBar().setValue(0)
        self._update_content()

    def set_remove_enabled(self, enabled):
        self._remove_enabled = bool(enabled)
        self.tree.set_remove_enabled(enabled)

    def refresh_empty_state_text(self):
        self.tree.refresh_empty_state_text()
        self.view.viewport().update()
