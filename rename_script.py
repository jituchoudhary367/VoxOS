import os
import re

def rename_in_files():
    root_dir = "."
    
    # Extensions to modify
    exts = ('.py', '.md', '.toml', '.yml', '.yaml', '.ini', '.txt', '.spec')
    
    # Folders to ignore
    ignore_dirs = {'.git', '__pycache__', 'venv', 'env', 'dist', 'build', '.pytest_cache', '.gemini'}
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # filter ignored directories
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs]
        
        for filename in filenames:
            if filename.endswith(exts):
                filepath = os.path.join(dirpath, filename)
                
                # Read content
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                except UnicodeDecodeError:
                    continue
                
                # Replacements
                # 1. Package imports and string references
                new_content = re.sub(r'\bjarvis\b', 'voxos', content)
                new_content = re.sub(r'\bJARVIS\b', 'VoxOS', new_content)
                new_content = re.sub(r'\bJarvis\b', 'VoxOS', new_content)
                
                if new_content != content:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    print(f"Updated {filepath}")

def rename_directory():
    if os.path.exists('voxos'):
        os.rename('voxos', 'voxos')
        print("Renamed directory 'voxos' to 'voxos'")

if __name__ == '__main__':
    rename_in_files()
    rename_directory()
