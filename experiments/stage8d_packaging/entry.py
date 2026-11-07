"""PyInstaller で固める入口（4.0 ステージ8d の比較だけ）。

`RADIOSIM_STARTUP_PROBE` があれば起動の時刻を測り（probe.py）、無ければ `python -m webui` と同じ。
"""
import os
import sys

if os.environ.get("RADIOSIM_STARTUP_PROBE"):
    import probe
    sys.exit(probe.run())

from webui.__main__ import main  # noqa: E402

sys.exit(main())
