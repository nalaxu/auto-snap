import clients


def _mk(tmp_path, name, files):
    d = tmp_path / name
    d.mkdir()
    for f in files:
        (d / f).write_bytes(b"")
    exe = d / "SnapCN.exe"
    exe.write_bytes(b"")
    return str(exe)


def test_cn_channels_distinguished_by_marker_files(tmp_path):
    ps = clients.profiles({})
    steam = _mk(tmp_path, "steam", ["NtUniSdkSteam.dll", "steam_api64.dll"])
    official = _mk(tmp_path, "official", ["cube_key.dll"])
    assert ps["cn_steam"].matches_exe("SnapCN.exe", steam)
    assert not ps["cn_official"].matches_exe("SnapCN.exe", steam)
    assert ps["cn_official"].matches_exe("SnapCN.exe", official)
    assert not ps["cn_steam"].matches_exe("SnapCN.exe", official)
    assert not ps["global"].matches_exe("SnapCN.exe", official)


def test_install_dir_pins_exact_folder(tmp_path):
    a = _mk(tmp_path, "a", [])
    b = _mk(tmp_path, "b", [])
    p = clients.profiles({"game": {"clients": {"cn_official": {"install_dir": str(tmp_path / "a")}}}})["cn_official"]
    assert p.matches_exe("SnapCN.exe", a) and not p.matches_exe("SnapCN.exe", b)


def test_selection_priority(monkeypatch):
    s = {"game": {"client": "cn_steam"}}
    monkeypatch.delenv("SNAP_CLIENT", raising=False)
    assert clients.resolve(s).key == "cn_steam"
    monkeypatch.setenv("SNAP_CLIENT", "global")
    assert clients.resolve(s).key == "global"
    assert clients.resolve(s, "cn_official").key == "cn_official"


def test_template_dirs_fall_back_to_shared():
    p = clients.profiles({"game": {"clients": {"global": {"templates_dir": "data/templates/ui_global"}}}})["global"]
    assert p.template_dirs[-1] == clients.SHARED_TEMPLATES
    assert p.template_dirs[0].name == "ui_global"
    assert clients.profiles({})["cn_steam"].template_dirs == [clients.SHARED_TEMPLATES]
