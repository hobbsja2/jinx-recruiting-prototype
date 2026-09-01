import os
paths = [r'C:\Program Files', r'C:\Program Files (x86)', r'C:\Users\hobbs\AppData\Local', r'C:\Users\hobbs\AppData\Local\Programs', r'C:\Users\hobbs\AppData\Local\Microsoft\WindowsApps']
found = []
for base in paths:
    if os.path.isdir(base):
        for root, dirs, files in os.walk(base):
            if 'git.exe' in files:
                found.append(os.path.join(root, 'git.exe'))
                if len(found) >= 20:
                    break
    if found:
        break
print(found)
