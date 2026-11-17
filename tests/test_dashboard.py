import pytest

import configure
import dashboard
import runtime


@pytest.fixture(scope="module")
def tk_root():
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        pytest.skip(f"当前测试环境无法创建窗口: {e}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(monkeypatch, tmp_path, tk_root):
    root = tk_root
    monkeypatch.setattr(configure, "CONFIG_PATH", tmp_path / "config.yaml")
    app = dashboard.Dashboard(root)
    root.update_idletasks()
    yield app
    for callback in root.tk.call("after", "info"):
        root.after_cancel(callback)
    for widget in root.winfo_children():
        widget.destroy()


def test_window_save_and_order_candidates(window):
    window.student.set("B21000001")
    window.available = [("1", "仙林1号", "18:00", "19:00", True, "2026-10-01")]
    window.table.insert("", "end", iid="0", values=("仙林1号", "18:00-19:00", "可用"))
    window.table.selection_set("0")
    window.add_selected(False)
    window.add_selected(False)
    assert len(window.targets) == 1
    assert window.save()
    assert configure.load_config()["booking"]["targets"] == window.targets
    assert window.primary_list.size() == 1


def test_window_stop_requests_cooperative_shutdown(window):
    window.stop()
    assert (runtime.DATA_DIR / "booking.stop").exists()
    assert (runtime.DATA_DIR / "capture.stop").exists()


def test_window_refuses_save_during_booking(window, monkeypatch):
    messages = []
    monkeypatch.setattr(window.messagebox, "showinfo", lambda *a: messages.append(a))
    with runtime.single_instance("booking"):
        assert not window.save()
    assert messages
