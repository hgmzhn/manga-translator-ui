import customtkinter as ctk

def show_toast(parent, message, duration=2000, level="info"):
    toast = ctk.CTkToplevel(parent)
    toast.overrideredirect(True) # Remove window decorations
    toast.wm_attributes("-topmost", True)

    colors = {
        "info": ("#3366CC", "#FFFFFF"), # Blue background, white text
        "success": ("#339966", "#FFFFFF"), # Green background, white text
        "error": ("#CC3333", "#FFFFFF") # Red background, white text
    }
    bg_color, text_color = colors.get(level, colors["info"])

    label = ctk.CTkLabel(toast, text=message, padx=20, pady=10, corner_radius=10, fg_color=bg_color, text_color=text_color)
    label.pack()

    # Position the toast at the bottom center of the parent window
    parent.update_idletasks()
    parent_x = parent.winfo_x()
    parent_y = parent.winfo_y()
    parent_width = parent.winfo_width()
    parent_height = parent.winfo_height()
    toast_width = label.winfo_reqwidth()
    toast_height = label.winfo_reqheight()

    x = parent_x + (parent_width // 2) - (toast_width // 2)
    y = parent_y + parent_height - toast_height - 20 # 20 pixels from the bottom

    toast.geometry(f"+{x}+{y}")
    toast.after(duration, toast.destroy)

class CollapsibleFrame(ctk.CTkFrame):
    def __init__(self, parent, title="", start_expanded=True):
        super().__init__(parent, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        
        self.title = title
        self.is_expanded = start_expanded

        self.header = ctk.CTkFrame(self, corner_radius=0, fg_color="#33A333")
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.bind("<Button-1>", self.toggle)
        self.header.grid_columnconfigure(1, weight=1)

        self.arrow_label = ctk.CTkLabel(self.header, text="▼" if self.is_expanded else "▶")
        self.arrow_label.grid(row=0, column=0, padx=5)
        self.arrow_label.bind("<Button-1>", self.toggle)

        self.title_label = ctk.CTkLabel(self.header, text=self.title, font=ctk.CTkFont(weight="bold"))
        self.title_label.grid(row=0, column=1, sticky="w")
        self.title_label.bind("<Button-1>", self.toggle)

        self.content_frame = ctk.CTkFrame(self)
        if self.is_expanded:
            self.content_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        else:
            self.content_frame.grid_remove()

    def toggle(self, event=None):
        self.is_expanded = not self.is_expanded
        if self.is_expanded:
            self.content_frame.grid()
            self.arrow_label.configure(text="▼")
        else:
            self.content_frame.grid_remove()
            self.arrow_label.configure(text="▶")
