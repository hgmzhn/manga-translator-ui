import os
import shutil
import sys

def organize_dist_folder(dist_path):
    print(f"Organizing output directory: {dist_path}")

    if not os.path.isdir(dist_path):
        print(f"Error: Directory not found: {dist_path}")
        sys.exit(1)

    internal_path = os.path.join(dist_path, '_internal')
    libs_dir = os.path.join(dist_path, 'libs')

    # --- Step 1: Rename _internal to libs ---
    if os.path.isdir(internal_path):
        print("Renaming '_internal' to 'libs'...")
        os.rename(internal_path, libs_dir)
    elif os.path.isdir(libs_dir):
        print("'libs' directory already exists. Skipping rename.")
    else:
        print("Warning: Neither '_internal' nor 'libs' directory found.")
        return

    # --- Step 2: Pull data files out from libs to the root ---
    data_items_to_move_back = [
        'locales',
        'MangaStudio_Data',
        'fonts',
        'dict',
        'models',
        'examples',
        'prompts.json',
        'translations_cn.json',
    ]

    print("Pulling required data assets to the root directory...")
    for item_name in data_items_to_move_back:
        source_path = os.path.join(libs_dir, item_name)
        destination_path = os.path.join(dist_path, item_name)
        
        if os.path.exists(source_path):
            print(f"  - Moving: {item_name}")
            shutil.move(source_path, destination_path)
        else:
            print(f"  - Warning: Data item not found in libs: {item_name}")

    print("Organization complete.")

if __name__ == '__main__':
    output_dir_name = 'manga-translator-gpu'
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dist_folder_path = os.path.join(script_dir, 'dist', output_dir_name)
    
    if not os.path.exists(dist_folder_path):
        print(f"Error: Distribution path does not exist: {dist_folder_path}")
        print("Please run PyInstaller before running this script.")
        sys.exit(1)

    organize_dist_folder(dist_folder_path)
