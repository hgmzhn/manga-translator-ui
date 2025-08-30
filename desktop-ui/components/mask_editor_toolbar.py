import customtkinter as ctk

class MaskEditorToolbar(ctk.CTkFrame):
    def __init__(self, parent, on_tool_change=None, on_brush_size_change=None):
        super().__init__(parent)
        self.on_tool_change = on_tool_change
        self.on_brush_size_change = on_brush_size_change

        self.tool_variable = ctk.StringVar(value="brush")

        self.brush_button = ctk.CTkRadioButton(self, text="Brush", variable=self.tool_variable, value="brush", command=self._on_tool_change)
        self.brush_button.pack(side="left", padx=5, pady=5)

        self.eraser_button = ctk.CTkRadioButton(self, text="Eraser", variable=self.tool_variable, value="eraser", command=self._on_tool_change)
        self.eraser_button.pack(side="left", padx=5, pady=5)

        self.brush_size_slider = ctk.CTkSlider(self, from_=1, to=100, command=self._on_brush_size_change)
        self.brush_size_slider.set(10)
        self.brush_size_slider.pack(side="left", padx=5, pady=5)

    def _on_tool_change(self):
        if self.on_tool_change:
            self.on_tool_change(self.tool_variable.get())

    def _on_brush_size_change(self, value):
        if self.on_brush_size_change:
            self.on_brush_size_change(int(value))
