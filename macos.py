import platform
import sys

import host_guard

if __name__ == "__main__":
    if platform.system() != "Darwin":
        print("Этот файл предназначен для запуска только на macOS. Используй windows.py.")
        sys.exit(1)
    host_guard.run("macos.enc")
