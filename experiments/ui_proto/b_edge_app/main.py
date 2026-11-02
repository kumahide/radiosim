"""候補 B＝Python のローカル HTTP サーバ＋Edge のアプリモード（実行時の依存なし）。

Edge は専用のプロファイルで開く＝利用者の Edge と別のプロセスになり、閉じたことが分かる。
"""
import os
import subprocess  # nosec B404 ─ 自分の機械の msedge を起こすだけ
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(_HERE), os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))]

from common import boot, probe  # noqa: E402

#: これより早く Edge の親が終わったら、窓は別の Edge へ渡された（閉じたのではない）。
_HANDOFF_S = 3.0


def main() -> None:
    ctx = boot.start("B Edge アプリ")
    edge = probe.find_edge()
    if not edge:
        boot.fatal("Microsoft Edge が見つかりません")

    args = [edge, f"--app={ctx.app.url}", f"--user-data-dir={boot.app_dir('edge_profile')}",
            "--no-first-run", "--no-default-browser-check", "--disable-sync"]
    if ctx.size:
        args.append(f"--window-size={ctx.size[0]},{ctx.size[1]}")
    else:
        args.append("--start-maximized")
    started = time.time()
    proc = subprocess.Popen(args)  # nosec B603 ─ 上で確かめた絶対パス

    if ctx.measure_out:
        ctx.app.measured.wait(90)
        boot.finish(ctx)
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],  # nosec B603 B607
                       capture_output=True, check=False)
        return
    while not ctx.app.closed.wait(0.5):
        if proc.poll() is not None and time.time() - started > _HANDOFF_S:
            break


if __name__ == "__main__":
    main()
