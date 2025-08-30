import numpy as np
from typing import Callable, List

class TransformService:
    def __init__(self):
        self.zoom_level = 1.0
        self.x_offset = 0
        self.y_offset = 0
        self._callbacks: List[Callable] = []

    def subscribe(self, callback: Callable):
        """Subscribe to transform changes."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def unsubscribe(self, callback: Callable):
        """Unsubscribe from transform changes."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify(self):
        """Notify all subscribers."""
        for callback in self._callbacks:
            try:
                callback()
            except Exception as e:
                print(f"Error in transform callback: {e}")

    def set_transform(self, zoom_level, x_offset, y_offset):
        self.zoom_level = zoom_level
        self.x_offset = x_offset
        self.y_offset = y_offset
        self._notify()

    def get_transform_matrix(self):
        return np.array([
            [self.zoom_level, 0, self.x_offset],
            [0, self.zoom_level, self.y_offset],
            [0, 0, 1]
        ])

    def get_inverse_transform_matrix(self):
        if abs(self.zoom_level) < 1e-6:
            return np.identity(3)
        # Use numpy's pseudo-inverse for better stability if the matrix is singular
        return np.linalg.pinv(self.get_transform_matrix())

    def screen_to_image(self, x, y):
        point = np.array([x, y, 1])
        transformed_point = np.dot(self.get_inverse_transform_matrix(), point)
        return transformed_point[0], transformed_point[1]

    def image_to_screen(self, x, y):
        point = np.array([x, y, 1])
        transformed_point = np.dot(self.get_transform_matrix(), point)
        return transformed_point[0], transformed_point[1]

    def zoom(self, factor: float, center_x: float, center_y: float):
        """
        Zooms in or out, keeping the point under the cursor stationary.
        center_x, center_y are screen coordinates.
        """
        img_x, img_y = self.screen_to_image(center_x, center_y)
        self.zoom_level *= factor
        self.x_offset = center_x - img_x * self.zoom_level
        self.y_offset = center_y - img_y * self.zoom_level
        self._notify()

    def pan(self, dx: float, dy: float):
        """Pans the view by a delta in screen coordinates."""
        self.x_offset += dx
        self.y_offset += dy
        self._notify()
