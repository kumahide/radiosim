"""
main.py
=======
アプリケーションエントリーポイント。
tkinter ループを起動するだけ。依存の組み立て（DI）もここで行う。
"""

import tkinter as tk
from views.launcher import SimLauncher


def main() -> None:
    root = tk.Tk()
    SimLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
