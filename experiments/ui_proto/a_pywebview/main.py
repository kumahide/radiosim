"""候補 A＝pywebview（WebView2）。Python 1 プロセスに窓が付く。

⚠️ 画面とのやり取りは pywebview の橋を使わず、B と同じ `127.0.0.1` の口を読む
＝A と B の差を「窓の出し方」だけにして比べるため。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(_HERE), os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))]

from common import boot  # noqa: E402


def main() -> None:
    ctx = boot.start("A pywebview")
    try:
        import webview
    except Exception as exc:  # noqa: BLE001 ─ 画面に理由を出す
        boot.fatal(f"pywebview を読めません: {exc}")

    width, height = ctx.size or (1600, 900)
    window = webview.create_window(
        "RadioSim 画面の試作 A", ctx.app.url,
        width=width, height=height, maximized=ctx.size is None)

    def watch() -> None:
        if ctx.measure_out:
            ctx.app.measured.wait(90)
            boot.finish(ctx)
            window.destroy()

    try:
        webview.start(watch, gui="edgechromium", private_mode=True)
    except Exception as exc:  # noqa: BLE001 ─ WebView2 が無い機械で起きる
        boot.fatal(f"WebView2 で窓を開けません: {exc}")


if __name__ == "__main__":
    main()
