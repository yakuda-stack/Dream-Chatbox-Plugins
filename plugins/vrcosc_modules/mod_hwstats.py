"""Linux Hardware Stats - CPU, GPU, RAM, VRAM, network, FPS, VR mode.

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
NAME = "Linux Hardware Stats"
SCRIPT = "vrcosc_hwstats.sh"

KEYS = (
    "hw_cpu", "hw_cpu_usage", "hw_cpu_temp", "hw_cpu_power", "hw_cpu_name",
    "hw_gpu", "hw_gpu_usage", "hw_gpu_temp", "hw_gpu_power", "hw_gpu_name",
    "hw_ram", "hw_ram_usage", "hw_ram_used", "hw_ram_total",
    "hw_vram", "hw_vram_usage", "hw_vram_used", "hw_vram_total",
    "hw_net", "hw_net_rx", "hw_net_tx",
    "hw_temp_max", "hw_temp_sys", "hw_fps",
    "hw_vr_mode", "hw_window", "hw_process", "hw_vrchat",
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
    wanted = (ctx.num("hw_gpu_index", 0, 0, 7), ctx.num("hw_cpu_index", 0, 0, 7),
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

    fahrenheit = ctx.get("hw_temp_unit", "celsius") == "fahrenheit"
    power = ctx.flag("hw_power")

    def temp(value):
        if not value:
            return None
        return (f"{round(value * 9 / 5 + 32)}°F" if fahrenheit
                else f"{round(value)}°C")

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

    # ------------------------------------------------------------- CPU
    if ctx.flag("hw_show_cpu", True):
        vals["hw_cpu_usage"] = pct(snap.get("cpu_usage"))
        vals["hw_cpu_temp"] = temp(snap.get("cpu_temp"))
        watt = snap.get("cpu_power") or 0
        vals["hw_cpu_power"] = f"{watt}W" if watt else None
        vals["hw_cpu_name"] = _name(snap.get("cpu_name"), "Generic CPU")
        vals["hw_cpu"] = util.join(ctx.text("hw_icon_cpu"),
                                   vals["hw_cpu_usage"], vals["hw_cpu_temp"],
                                   vals["hw_cpu_power"] if power else None)

    # ------------------------------------------------------------- GPU
    # A card that reports nothing at all (no driver, VM, headless) would
    # otherwise sit in the line as a permanent "0%".
    if ctx.flag("hw_show_gpu", True):
        vals["hw_gpu_usage"] = pct(snap.get("gpu_usage"))
        vals["hw_gpu_temp"] = temp(snap.get("gpu_temp"))
        watt = snap.get("gpu_power") or 0
        vals["hw_gpu_power"] = f"{watt}W" if watt else None
        vals["hw_gpu_name"] = _name(snap.get("gpu_name"), "Unknown GPU")
        if (snap.get("gpu_usage") or snap.get("gpu_temp") or watt
                or vals["hw_gpu_name"]):
            vals["hw_gpu"] = util.join(
                ctx.text("hw_icon_gpu"), vals["hw_gpu_usage"],
                vals["hw_gpu_temp"], vals["hw_gpu_power"] if power else None)
        else:
            vals["hw_gpu_usage"] = vals["hw_gpu_temp"] = None

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

    vals["hw_temp_max"] = temp(snap.get("max_temp"))
    vals["hw_temp_sys"] = temp(snap.get("system_temp"))

    if ctx.flag("hw_show_fps"):
        fps = snap.get("fps") or 0
        vals["hw_fps"] = f"{fps} FPS" if fps > 0 else None

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


def _name(value, placeholder):
    """The script falls back to 'Unknown GPU' / 'Generic CPU' when it
    finds nothing - not worth a slot in the chatbox."""
    value = (value or "").strip()
    return value if value and value != placeholder else None


def _speed(kibps):
    if kibps is None:
        return None
    return f"{kibps / 1024:.1f} MB/s" if kibps >= 1024 else f"{int(kibps)} KB/s"


def line(vals):
    """This module's contribution to the combined {vrcosc_modules} line."""
    return [vals["hw_cpu"], vals["hw_gpu"], vals["hw_ram"], vals["hw_vram"],
            vals["hw_net"], vals["hw_fps"], vals["hw_vr_mode"]]
