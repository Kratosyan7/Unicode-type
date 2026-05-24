import platform
import sys

import host_guard

if __name__ == "__main__":
    if platform.system() != "Windows":
        print("Этот файл предназначен для запуска только в Windows. Используй macos.py.")
        sys.exit(1)
    host_guard.run("windows.enc")
