# PyInstaller spec for the single-file grip binary.
# Build with:  pyinstaller scripts/build/grip.spec
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for package in ("anthropic", "pydantic", "pydantic_core", "rich", "click"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

# The whiteboard page is read with importlib.resources at run time.
datas.append(("../../src/grip_hook/whiteboard.html", "grip_hook"))

a = Analysis(
    ["entry.py"],
    pathex=["../../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "test", "unittest", "pydoc", "doctest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="grip",
    console=True,
    strip=False,
    upx=False,
    bootloader_ignore_signals=False,
    disable_windowed_traceback=False,
)
