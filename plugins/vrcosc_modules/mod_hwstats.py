"""System - RAM/VRAM in GB, network, active window, VR mode.

v2.0.0: CPU, GPU, temperatures, power draw and FPS were removed here -
the app's own Hardware card and World Stats show them already, and two
places for the same number only made the settings confusing. What stays
is what the app does not have.

Port of `LinuxHardwareStats` from Bluscream's VRCOSC-Modules. The reading
is done by his `vrcosc_hwstats.sh`, bundled here unchanged: it walks
/proc, /sys/class/hwmon, /sys/class/drm, Intel RAPL, nvidia-smi, MangoHud
logs, xdotool/kdotool and the VR compositor processes, then writes 27
lines. We deploy a patched copy (indices + output path) and parse it.
"""

# Copyright (C) 2026 yakuda
# Bundled script: Copyright (c) Bluscream
# SPDX-License-Identifier: GPL-3.0-or-later

from . import util

ID = "hw"
NAME = "System"
SCRIPT = "vrcosc_hwstats.sh"

KEYS = (
    "hw_ram", "hw_ram_usage", "hw_ram_used", "hw_ram_total",
    "hw_vram", "hw_vram_usage", "hw_vram_used", "hw_vram_total",
    "hw_net", "hw_net_rx", "hw_net_tx",
    "hw_vr_mode", "hw_window", "hw_process", "hw_vrchat",
    # v1.2.0, names as in the chatbox converter
    "net_max_down", "net_max_up", "net_total_down", "net_total_up",
    "net_utilization",
)

# Field order of the file the script writes - documented at the bottom of
# vrcosc_hwstats.sh. Extra lines a future version adds are ignored.
_FIELDS = [
    ("cpu_usage", "int"), ("cpu_power", "int"), ("cpu_temp", "int"),
    ("gpu_usage", "int"), ("gpu_power", "int"), ("gpu_temp", "int"),
    ("ram_usage", "frac"), ("ram_total", "float"), ("ram_used", "float"),
    ("ram_free", "float"), ("vram_usage", "frac"), ("vram_total", "float"),
    ("vram_used", "float"), ("vram_free", "float"),
    ("cpu_name", "str"), ("gpu_name", "str"),
    ("net_rx_kibps", "int"), ("net_tx_kibps", "int"),
    ("net_rx_total_mb", "float"), ("net_tx_total_mb", "float"),
    ("system_temp", "int"), ("max_temp", "int"),
    ("window_title", "str"), ("process_name", "str"), ("fps", "int"),
    ("vr_mode", "str"), ("vrchat_running", "int"),
]

_poller = None
_script = None
# network extras: peaks of this session, and the byte counters as they
# were at the first sample (totals "since the app started")
_peak = {"rx": 0, "tx": 0}
_base = None
_config = None          # (gpu_index, cpu_index, iface, override)
_out = util.cache_dir("hwstats") / "hwstats.txt"


# ---------------------------------------------------------------- setup
def start(ctx):
    global _poller
    stop()
    _apply(ctx)
    _poller = util.Poller(_tick, ctx.log, ctx.num("hw_interval", 3, 1, 30),
                          "hwstats")
    _poller.start()


def stop():
    global _poller, _script, _config
    if _poller is not None:
        _poller.stop()
    _poller = None
    _script = None
    _config = None


def on_settings(ctx):
    if _poller is None:
        return
    _apply(ctx)
    _poller.set_interval(ctx.num("hw_interval", 3, 1, 30))
    _poller.poke()


def _apply(ctx):
    """Re-deploy the script whenever an index or the interface changed."""
    global _script, _config
    # the CPU index only picked the CPU temperature, which is gone - the
    # script still wants a value
    wanted = (ctx.num("hw_gpu_index", 0, 0, 7), 0,
              ctx.text("hw_iface"), ctx.text("hw_script"))
    if wanted == _config and _script is not None:
        return
    _config = wanted
    gpu, cpu, iface, override = wanted
    _script = util.deploy(
        SCRIPT, "hwstats", override=override, patches=[
            (r"^GPU_INDEX=.*$", f"GPU_INDEX={gpu}"),
            (r"^CPU_INDEX=.*$", f"CPU_INDEX={cpu}"),
            (r"^NET_IFACE=.*$", f'NET_IFACE="{_iface(iface)}"'),
            # away from ~/.vrcosc_hwstats.txt so a real VRCOSC install
            # running next to us keeps its own file
            (r"^cat <<EOF > .*$", f'cat <<EOF > "{_out}"'),
        ])


def _iface(value):
    return "".join(c for c in str(value or "")
                   if c.isalnum() or c in "_.:@-")


# ----------------------------------------------------------------- tick
def _tick():
    if _script is None:
        return None
    util.run_script(_script, timeout=20)
    lines = util.read_lines(_out, minimum=len(_FIELDS))
    if lines is None:
        return None                    # half-written file, try again
    snap = {}
    for idx, (key, kind) in enumerate(_FIELDS):
        raw = lines[idx].strip()
        if kind == "int":
            snap[key] = util.to_int(raw)
        elif kind == "float":
            snap[key] = util.to_float(raw)
        elif kind == "frac":
            snap[key] = util.to_float(raw) * 100.0
        else:
            snap[key] = raw
    snap["vrchat_running"] = bool(snap.get("vrchat_running"))
    return snap


# --------------------------------------------------------------- values
def values(ctx):
    vals = dict.fromkeys(KEYS)
    if _poller is None:
        return vals
    # four intervals without a fresh sample means something is wrong -
    # better an empty line than numbers from ten minutes ago
    snap = _poller.snapshot(max_age=max(15.0,
                                        ctx.num("hw_interval", 3, 1, 30) * 4))
    if not snap:
        return vals

    def pct(value):
        return f"{round(value)}%" if value is not None else None

    def gb(value):
        return f"{value:.1f}" if value else "0.0"

    def memory(icon, percent, used, total, have):
        if not have:
            return None
        style = ctx.get("hw_mem_style", "used_total")
        if style == "percent":
            body = percent
        elif style == "used":
            body = f"{used} GB"
        else:
            body = f"{used}/{total} GB"
        return util.join(icon, body)

    # ------------------------------------------------------- RAM / VRAM
    if ctx.flag("hw_show_ram", True):
        vals["hw_ram_usage"] = pct(snap.get("ram_usage"))
        vals["hw_ram_used"] = gb(snap.get("ram_used"))
        vals["hw_ram_total"] = gb(snap.get("ram_total"))
        vals["hw_ram"] = memory(ctx.text("hw_icon_ram"), vals["hw_ram_usage"],
                                vals["hw_ram_used"], vals["hw_ram_total"],
                                snap.get("ram_total"))
    # a card without a readable VRAM counter would otherwise report a
    # very convincing 0.0/0.0 GB
    if ctx.flag("hw_show_vram") and snap.get("vram_total"):
        vals["hw_vram_usage"] = pct(snap.get("vram_usage"))
        vals["hw_vram_used"] = gb(snap.get("vram_used"))
        vals["hw_vram_total"] = gb(snap.get("vram_total"))
        vals["hw_vram"] = memory(ctx.text("hw_icon_vram"),
                                 vals["hw_vram_usage"], vals["hw_vram_used"],
                                 vals["hw_vram_total"], snap.get("vram_total"))

    # --------------------------------------------------------- network
    if ctx.flag("hw_show_net"):
        vals["hw_net_rx"] = _speed(snap.get("net_rx_kibps"))
        vals["hw_net_tx"] = _speed(snap.get("net_tx_kibps"))
        vals["hw_net"] = util.join("⬇", vals["hw_net_rx"],
                                   "⬆", vals["hw_net_tx"])
        _net_extras(ctx, snap, vals)

    if ctx.flag("hw_show_vr"):
        mode = (snap.get("vr_mode") or "").strip()
        if mode:
            # the goggles only make sense when a compositor is actually up
            icon = ctx.text("hw_icon_vr") if mode != "Desktop" else ""
            vals["hw_vr_mode"] = util.join(icon, mode)

    if ctx.flag("hw_show_win"):
        title = (snap.get("window_title") or "").strip()
        if title and title != "Unknown":
            vals["hw_window"] = util.cut(title,
                                         ctx.num("hw_win_max", 24, 6, 64))
        process = (snap.get("process_name") or "").strip()
        if process and process != "Unknown":
            vals["hw_process"] = process

    if ctx.flag("hw_show_vrc") and snap.get("vrchat_running"):
        vals["hw_vrchat"] = ctx.text("hw_vrc_text", "VRChat") or None
    return vals


def _net_extras(ctx, snap, vals):
    """{net_max_down/up} {net_total_down/up} {net_utilization}."""
    global _base
    rx, tx = snap.get("net_rx_kibps") or 0, snap.get("net_tx_kibps") or 0
    _peak["rx"] = max(_peak["rx"], rx)
    _peak["tx"] = max(_peak["tx"], tx)
    vals["net_max_down"] = _speed(_peak["rx"]) if _peak["rx"] else None
    vals["net_max_up"] = _speed(_peak["tx"]) if _peak["tx"] else None

    total_rx = snap.get("net_rx_total_mb")
    total_tx = snap.get("net_tx_total_mb")
    if total_rx is not None and total_tx is not None:
        if _base is None or total_rx < _base[0] or total_tx < _base[1]:
            # first sample, or the counters were reset (interface
            # re-created, other iface picked): start counting again
            _base = (total_rx, total_tx)
        since_boot = ctx.get("hw_net_total_since", "app") == "boot"
        down = total_rx if since_boot else total_rx - _base[0]
        up = total_tx if since_boot else total_tx - _base[1]
        vals["net_total_down"] = _size(down)
        vals["net_total_up"] = _size(up)

    link = _link_mbps(ctx.text("hw_iface"))
    if link:
        used = (rx + tx) * 1024 * 8 / (link * 1_000_000) * 100
        vals["net_utilization"] = f"{min(100, round(used))}%"


def _size(mb):
    if mb is None or mb < 0:
        return None
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def _link_mbps(iface):
    """Link speed in Mbit/s from /sys/class/net/<iface>/speed - of the
    chosen interface, or the fastest wired one that is up. Wi-Fi often
    reports nothing there, then there is no utilisation to show."""
    import os
    base = "/sys/class/net"
    names = [iface] if iface else (os.listdir(base) if os.path.isdir(base)
                                   else [])
    best = 0
    for name in names:
        if name == "lo":
            continue
        try:
            with open(os.path.join(base, name, "operstate")) as fh:
                if fh.read().strip() != "up":
                    continue
            with open(os.path.join(base, name, "speed")) as fh:
                speed = int(fh.read().strip())
        except (OSError, ValueError):
            continue
        best = max(best, speed)
    return best if best > 0 else None


def _speed(kibps):
    if kibps is None:
        return None
    return f"{kibps / 1024:.1f} MB/s" if kibps >= 1024 else f"{int(kibps)} KB/s"


def line(vals):
    """This module's contribution to the combined {vrcosc_modules} line."""
    return [vals["hw_ram"], vals["hw_vram"], vals["hw_net"],
            vals["hw_vr_mode"]]
