import socket
import threading
import time

from vector1a.orchestration import port_is_open, wait_for_port


def test_wait_for_port_returns_when_service_appears():
    probe = socket.socket(); probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]; probe.close()

    def delayed_server():
        time.sleep(0.08)
        server = socket.socket(); server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port)); server.listen(1)
        conn, _ = server.accept(); conn.close(); server.close()

    threading.Thread(target=delayed_server, daemon=True).start()
    assert wait_for_port("127.0.0.1", port, timeout=1.0, poll_interval=0.02)


def test_wait_for_port_times_out_for_missing_service():
    probe = socket.socket(); probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]; probe.close()
    assert not port_is_open("127.0.0.1", port, timeout=0.02)
    assert not wait_for_port("127.0.0.1", port, timeout=0.08, poll_interval=0.02)


def test_windows_exe_launch_uses_target_folder_as_cwd(monkeypatch, tmp_path):
    from vector1a.orchestration import SessionOrchestrator
    import vector1a.orchestration as orchestration

    folder = tmp_path / "Primary ReStim"
    folder.mkdir()
    exe = folder / "restim.exe"
    exe.write_bytes(b"")
    calls = []

    monkeypatch.setattr(orchestration, "IS_WINDOWS", True)
    monkeypatch.setattr(orchestration.subprocess, "Popen", lambda args, cwd=None: calls.append((args, cwd)))

    result = SessionOrchestrator().launch("Primary ReStim", str(exe))
    assert result.launched
    assert calls == [([str(exe)], str(folder))]


def test_two_restim_exes_in_different_folders_are_distinct_launches(monkeypatch, tmp_path):
    from vector1a.orchestration import SessionOrchestrator
    import vector1a.orchestration as orchestration

    primary = tmp_path / "Primary" / "restim.exe"
    prostate = tmp_path / "Prostate" / "restim.exe"
    primary.parent.mkdir(); prostate.parent.mkdir()
    primary.write_bytes(b""); prostate.write_bytes(b"")
    calls = []

    monkeypatch.setattr(orchestration, "IS_WINDOWS", True)
    monkeypatch.setattr(orchestration.subprocess, "Popen", lambda args, cwd=None: calls.append((args, cwd)))

    o = SessionOrchestrator()
    assert o.launch("Primary ReStim", str(primary)).launched
    assert o.launch("Prostate ReStim", str(prostate)).launched
    assert calls == [
        ([str(primary)], str(primary.parent)),
        ([str(prostate)], str(prostate.parent)),
    ]
