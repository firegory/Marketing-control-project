from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

project_root = Path(SPECPATH).parent.parent
google_ads_datas, google_ads_binaries, google_ads_hiddenimports = collect_all(
    "google.ads.googleads"
)
tzdata_datas, tzdata_binaries, tzdata_hiddenimports = collect_all("tzdata")

a = Analysis(
    [str(project_root / "src" / "marketing_control" / "__main__.py")],
    pathex=[str(project_root / "src")],
    binaries=[*google_ads_binaries, *tzdata_binaries],
    datas=[
        (str(project_root / "src" / "marketing_control" / "templates"), "marketing_control/templates"),
        (str(project_root / "src" / "marketing_control" / "migrations"), "marketing_control/migrations"),
        *google_ads_datas,
        *tzdata_datas,
    ],
    hiddenimports=[
        *google_ads_hiddenimports,
        *tzdata_hiddenimports,
        *collect_submodules("keyring.backends"),
        *collect_submodules("uvicorn"),
    ],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Marketing Control", console=False)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="Marketing Control")
