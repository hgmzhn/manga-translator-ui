import customtkinter as ctk
from typing import Callable, List
from PIL import Image, ImageTk
import os
from tkinter import messagebox

class FileListFrame(ctk.CTkFrame):
    def __init__(self, parent, on_file_select: Callable[[str], None], on_load_files: Callable, on_load_folder: Callable, on_file_unload: Callable[[str], None] = None):
        super().__init__(parent)
        self.on_file_select = on_file_select
        self.on_load_files = on_load_files
        self.on_load_folder = on_load_folder
        self.on_file_unload = on_file_unload
        self.file_paths: List[str] = []
        self.current_selection = None
        self.file_frames = {}  # 用于追踪文件对应的frame

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # --- Header and Buttons ---
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        header_frame.grid_columnconfigure(1, weight=1)

        self.load_file_button = ctk.CTkButton(header_frame, text="Load Files", command=self.on_load_files, width=100)
        self.load_file_button.pack(side="left", padx=(0, 5))

        self.load_folder_button = ctk.CTkButton(header_frame, text="Load Folder", command=self.on_load_folder, width=100)
        self.load_folder_button.pack(side="left")

        # --- Scrollable File List ---
        self.scrollable_frame = ctk.CTkScrollableFrame(self)
        self.scrollable_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        self.scrollable_frame.grid_columnconfigure(0, weight=1)

    def add_files(self, file_paths: List[str]):
        for file_path in file_paths:
            if file_path not in self.file_paths:
                self.file_paths.append(file_path)
                self._add_file_entry(file_path)

    def _add_file_entry(self, file_path: str):
        entry_frame = ctk.CTkFrame(self.scrollable_frame, fg_color="gray20", corner_radius=5)
        entry_frame.pack(fill="x", padx=5, pady=3)
        entry_frame.grid_columnconfigure(1, weight=1)
        
        # 保存file_path和frame的映射关系
        self.file_frames[file_path] = entry_frame

        try:
            image = Image.open(file_path)
            image.thumbnail((40, 40))
            tk_image = ImageTk.PhotoImage(image)
            thumb_label = ctk.CTkLabel(entry_frame, image=tk_image, text="")
            thumb_label.image = tk_image
            thumb_label.grid(row=0, column=0, padx=5, pady=5)
        except Exception as e:
            print(f"Error creating thumbnail for {file_path}: {e}")
            thumb_label = ctk.CTkLabel(entry_frame, text="ERR", width=40, height=40, fg_color="red")
            thumb_label.grid(row=0, column=0, padx=5, pady=5)

        file_name = os.path.basename(file_path)
        name_label = ctk.CTkLabel(entry_frame, text=file_name, anchor="w")
        name_label.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        # 添加卸载按钮
        unload_button = ctk.CTkButton(
            entry_frame, 
            text="✕", 
            width=30, 
            height=30, 
            font=("Arial", 14),
            command=lambda p=file_path: self._on_unload_file(p)
        )
        unload_button.grid(row=0, column=2, padx=5, pady=5)

        entry_frame.bind("<Button-1>", lambda e, p=file_path, f=entry_frame: self._on_entry_click(p, f))
        thumb_label.bind("<Button-1>", lambda e, p=file_path, f=entry_frame: self._on_entry_click(p, f))
        name_label.bind("<Button-1>", lambda e, p=file_path, f=entry_frame: self._on_entry_click(p, f))

    def _on_entry_click(self, file_path, frame):
        if self.current_selection:
            self.current_selection.configure(fg_color="gray20")
        
        frame.configure(fg_color="#3a7ebf")
        self.current_selection = frame

        if self.on_file_select:
            self.on_file_select(file_path)
    
    def _on_unload_file(self, file_path: str):
        """卸载文件，询问用户是否保存"""
        if self.on_file_unload:
            self.on_file_unload(file_path)
    
    def remove_file(self, file_path: str):
        """从列表中移除文件（由外部调用）"""
        if file_path in self.file_paths:
            self.file_paths.remove(file_path)
            
            # 移除对应的frame
            if file_path in self.file_frames:
                frame = self.file_frames[file_path]
                if self.current_selection == frame:
                    self.current_selection = None
                frame.destroy()
                del self.file_frames[file_path]
                
            print(f"已从列表中移除文件: {os.path.basename(file_path)}")