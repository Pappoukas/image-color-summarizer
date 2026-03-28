#!/usr/bin/env python3
"""
Image Color Summarizer
======================
Python equivalent of https://mk.bcgsc.ca/color-summarizer/

Analyzes image color statistics across RGB, HSV, Lab and LCH color spaces,
clusters pixels by perceptual similarity, names colors, and exports
a visualization + JSON report.

Usage:
    python color_summarizer.py photo.jpg
    python color_summarizer.py photo.jpg -k 8
    python color_summarizer.py photo.jpg -k 6 -o ./results
    python color_summarizer.py *.jpg -k 6 -o ./results --max-pixels 500000
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from sklearn.cluster import KMeans
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec


# ─────────────────────────────────────────────────────────────────────────────
# Color space conversions
# ─────────────────────────────────────────────────────────────────────────────

def rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    """
    Convert an array of RGB pixels (shape N×3, values 0-255) to HSV.
    Returns H: 0-360°, S: 0-100%, V: 0-100%.
    """
    r, g, b = rgb[:, 0] / 255.0, rgb[:, 1] / 255.0, rgb[:, 2] / 255.0
    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    # Value
    v = cmax

    # Saturation
    s = np.where(cmax > 0, delta / cmax, 0.0)

    # Hue
    h = np.zeros_like(r)
    m = delta > 0

    m_r = m & (cmax == r)
    h[m_r] = (60.0 * ((g[m_r] - b[m_r]) / delta[m_r])) % 360.0

    m_g = m & (cmax == g)
    h[m_g] = 60.0 * ((b[m_g] - r[m_g]) / delta[m_g]) + 120.0

    m_b = m & (cmax == b)
    h[m_b] = 60.0 * ((r[m_b] - g[m_b]) / delta[m_b]) + 240.0

    return np.column_stack([h, s * 100.0, v * 100.0])


def _linearize_srgb(c: np.ndarray) -> np.ndarray:
    """Apply sRGB gamma linearization."""
    return np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """
    Convert RGB pixels (N×3, 0-255) to CIE L*a*b* (D65 illuminant).
    Returns L: 0-100, a/b: typically -128 to +127.
    """
    linear = _linearize_srgb(rgb / 255.0)

    # sRGB → XYZ (D65)
    m = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ])
    xyz = linear @ m.T  # N×3

    # Normalise by D65 white point
    xyz /= np.array([0.95047, 1.00000, 1.08883])

    def f(t: np.ndarray) -> np.ndarray:
        return np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16.0 / 116.0)

    fx, fy, fz = f(xyz[:, 0]), f(xyz[:, 1]), f(xyz[:, 2])

    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)

    return np.column_stack([L, a, b])


def lab_to_lch(lab: np.ndarray) -> np.ndarray:
    """
    Convert L*a*b* (N×3) to LCH (cylindrical form).
    Returns L: 0-100, C ≥ 0, H: 0-360°.
    """
    L = lab[:, 0]
    C = np.sqrt(lab[:, 1] ** 2 + lab[:, 2] ** 2)
    H = np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360.0
    return np.column_stack([L, C, H])


def rgb_to_lch(rgb: np.ndarray) -> np.ndarray:
    return lab_to_lch(rgb_to_lab(rgb))


# ─────────────────────────────────────────────────────────────────────────────
# Color naming
# ─────────────────────────────────────────────────────────────────────────────

def color_name(r: int, g: int, b: int) -> str:
    """
    Return a human-readable English color name for an RGB triplet.
    Uses HSV heuristics similar to the original tool.
    """
    pixel = np.array([[r, g, b]], dtype=np.float64)
    h, s, v = rgb_to_hsv(pixel)[0]

    # ── Achromatic ────────────────────────────────────────────
    if s < 8:
        if v < 10:   return "black"
        if v < 22:   return "very dark grey"
        if v < 40:   return "dark grey"
        if v < 60:   return "grey"
        if v < 78:   return "light grey"
        if v < 93:   return "very light grey"
        return "white"

    # ── Hue name ──────────────────────────────────────────────
    hue_table = [
        (15,  "red"),
        (30,  "red-orange"),
        (45,  "orange"),
        (60,  "yellow-orange"),
        (75,  "yellow"),
        (105, "yellow-green"),
        (135, "green"),
        (165, "blue-green"),
        (195, "cyan"),
        (225, "blue-cyan"),
        (255, "blue"),
        (285, "blue-violet"),
        (315, "violet"),
        (345, "red-violet"),
        (360, "red"),
    ]
    hue = next(name for limit, name in hue_table if h < limit)

    # ── Brightness / saturation modifiers ────────────────────
    if v < 20:                        return f"very dark {hue}"
    if v < 38:                        return f"dark {hue}"
    if s > 75 and v > 65:             return f"vivid {hue}"
    if s < 25:                        return f"pale {hue}"
    if v > 82 and s < 45:             return f"light {hue}"
    return hue


# ─────────────────────────────────────────────────────────────────────────────
# Statistics
# ─────────────────────────────────────────────────────────────────────────────

def channel_stats(data: np.ndarray) -> dict:
    return {
        "mean":   round(float(np.mean(data)),   2),
        "median": round(float(np.median(data)), 2),
        "min":    round(float(np.min(data)),    2),
        "max":    round(float(np.max(data)),    2),
        "std":    round(float(np.std(data)),    2),
    }


def image_statistics(pixels: np.ndarray) -> dict:
    """
    Compute per-channel statistics across RGB, HSV, Lab and LCH.
    pixels: N×3 uint8 array.
    """
    hsv = rgb_to_hsv(pixels.astype(np.float64))
    lab = rgb_to_lab(pixels.astype(np.float64))
    lch = lab_to_lch(lab)

    return {
        "RGB": {
            "R": channel_stats(pixels[:, 0]),
            "G": channel_stats(pixels[:, 1]),
            "B": channel_stats(pixels[:, 2]),
        },
        "HSV": {
            "H": channel_stats(hsv[:, 0]),
            "S": channel_stats(hsv[:, 1]),
            "V": channel_stats(hsv[:, 2]),
        },
        "Lab": {
            "L": channel_stats(lab[:, 0]),
            "a": channel_stats(lab[:, 1]),
            "b": channel_stats(lab[:, 2]),
        },
        "LCH": {
            "L": channel_stats(lch[:, 0]),
            "C": channel_stats(lch[:, 1]),
            "H": channel_stats(lch[:, 2]),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# K-means clustering
# ─────────────────────────────────────────────────────────────────────────────

def cluster_pixels(pixels: np.ndarray, k: int, use_lab: bool = True):
    """
    Cluster pixels with k-means.

    Parameters
    ----------
    pixels  : N×3 uint8 array
    k       : number of clusters
    use_lab : cluster in perceptual Lab space (recommended)

    Returns
    -------
    clusters : list of dicts, sorted by pixel count descending
    labels   : N-length int array (cluster index per pixel)
    """
    feature_space = rgb_to_lab(pixels.astype(np.float64)) if use_lab else pixels.astype(np.float64)

    km = KMeans(n_clusters=k, random_state=42, n_init=10, max_iter=300)
    labels = km.fit_predict(feature_space)

    clusters = []
    total = len(pixels)

    for i in range(k):
        mask = labels == i
        if not mask.any():
            continue

        cluster_px = pixels[mask]
        # Representative color = mean of member pixels (in RGB)
        mean_rgb = np.mean(cluster_px, axis=0).astype(int)
        r, g, b = int(mean_rgb[0]), int(mean_rgb[1]), int(mean_rgb[2])

        p = np.array([[r, g, b]], dtype=np.float64)
        hsv = rgb_to_hsv(p)[0]
        lab = rgb_to_lab(p)[0]
        lch = lab_to_lch(lab.reshape(1, 3))[0]

        count = int(mask.sum())
        clusters.append({
            "index":      i,
            "pixels":     count,
            "percentage": round(count / total * 100, 2),
            "hex":        f"#{r:02X}{g:02X}{b:02X}",
            "rgb":        (r, g, b),
            "hsv":        (round(hsv[0], 1), round(hsv[1], 1), round(hsv[2], 1)),
            "lab":        (round(lab[0], 1), round(lab[1], 1), round(lab[2], 1)),
            "lch":        (round(lch[0], 1), round(lch[1], 1), round(lch[2], 1)),
            "name":       color_name(r, g, b),
            "_mask":      mask,   # internal – not exported to JSON
        })

    clusters.sort(key=lambda c: c["pixels"], reverse=True)
    return clusters, labels


# ─────────────────────────────────────────────────────────────────────────────
# Visualization
# ─────────────────────────────────────────────────────────────────────────────

def _text_color(rgb) -> str:
    """Return 'black' or 'white' for readable overlay text."""
    r, g, b = rgb
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "black" if luminance > 140 else "white"


def build_visualization(
    image_path: Path,
    img_small: Image.Image,
    pixels: np.ndarray,
    stats: dict,
    clusters: list,
    labels: np.ndarray,
    output_dir: Path,
) -> Path:
    """Render the full summary figure and save it as a PNG."""

    img_arr = np.array(img_small)
    h_img, w_img = img_arr.shape[:2]

    fig = plt.figure(figsize=(20, 15))
    fig.patch.set_facecolor("#f5f5f5")
    gs = GridSpec(
        4, 3,
        figure=fig,
        hspace=0.45,
        wspace=0.30,
        height_ratios=[1.4, 0.6, 1.3, 1.3],
    )

    # ── Row 0 ────────────────────────────────────────────────
    # Original image
    ax_orig = fig.add_subplot(gs[0, 0])
    ax_orig.imshow(img_arr)
    ax_orig.set_title("Original Image", fontsize=11, fontweight="bold", pad=6)
    ax_orig.axis("off")

    # Colour distribution bar
    ax_bar = fig.add_subplot(gs[0, 1:])
    x = 0
    for c in clusters:
        fc = [v / 255.0 for v in c["rgb"]]
        ax_bar.barh(0, c["percentage"], left=x, color=fc,
                    height=0.8, edgecolor="white", linewidth=0.8)
        if c["percentage"] > 2.5:
            tc = _text_color(c["rgb"])
            ax_bar.text(x + c["percentage"] / 2, 0,
                        f"{c['percentage']:.1f}%",
                        ha="center", va="center",
                        fontsize=8.5, color=tc, fontweight="bold")
        x += c["percentage"]

    ax_bar.set_xlim(0, 100)
    ax_bar.set_ylim(-0.5, 0.5)
    ax_bar.set_title("Color Distribution (%)", fontsize=11, fontweight="bold", pad=6)
    ax_bar.axis("off")

    # ── Row 1 ────────────────────────────────────────────────
    # Cluster swatches + info
    ax_sw = fig.add_subplot(gs[1, :2])
    n = len(clusters)
    ax_sw.set_xlim(0, n)
    ax_sw.set_ylim(0, 1)
    ax_sw.set_title(f"Color Clusters (k={n})", fontsize=11, fontweight="bold", pad=4)
    for i, c in enumerate(clusters):
        fc = [v / 255.0 for v in c["rgb"]]
        rect = mpatches.Rectangle(
            (i + 0.04, 0.22), 0.92, 0.72,
            facecolor=fc, edgecolor="#aaaaaa", linewidth=0.8
        )
        ax_sw.add_patch(rect)
        tc = _text_color(c["rgb"])
        ax_sw.text(i + 0.5, 0.58, c["hex"], ha="center", va="center",
                   fontsize=7.5, fontfamily="monospace", color=tc)
        ax_sw.text(i + 0.5, 0.14, f"{c['percentage']:.1f}%",
                   ha="center", va="top", fontsize=7.5, color="#444444")
        ax_sw.text(i + 0.5, 0.05, c["name"],
                   ha="center", va="top", fontsize=6.5, color="#666666",
                   style="italic")
    ax_sw.axis("off")

    # Partitioned image
    partition = np.zeros_like(img_arr)
    labels_2d = labels.reshape(h_img, w_img)
    for c in clusters:
        partition[labels_2d == c["index"]] = c["rgb"]

    ax_part = fig.add_subplot(gs[1, 2])
    ax_part.imshow(partition)
    ax_part.set_title("Partition by Cluster", fontsize=11, fontweight="bold", pad=4)
    ax_part.axis("off")

    # ── Row 2: Histograms ─────────────────────────────────────
    ax_rgb = fig.add_subplot(gs[2, 0])
    for ch, col, lbl in [(0, "#e74c3c", "R"), (1, "#27ae60", "G"), (2, "#2980b9", "B")]:
        ax_rgb.hist(pixels[:, ch], bins=64, alpha=0.55,
                    color=col, label=lbl, density=True)
    ax_rgb.set_title("RGB Histogram", fontsize=10, fontweight="bold")
    ax_rgb.set_xlabel("Value (0–255)", fontsize=8)
    ax_rgb.set_ylabel("Density", fontsize=8)
    ax_rgb.legend(fontsize=8, framealpha=0.7)
    ax_rgb.grid(True, alpha=0.25)

    hsv_all = rgb_to_hsv(pixels.astype(np.float64))
    ax_hue = fig.add_subplot(gs[2, 1])
    ax_hue.hist(hsv_all[:, 0], bins=72, color="#8e44ad",
                alpha=0.7, density=True)
    ax_hue.set_title("Hue Distribution (°)", fontsize=10, fontweight="bold")
    ax_hue.set_xlabel("Hue (0–360°)", fontsize=8)
    ax_hue.set_xlim(0, 360)
    ax_hue.grid(True, alpha=0.25)

    ax_sat = fig.add_subplot(gs[2, 2])
    ax_sat.hist(hsv_all[:, 1], bins=50, color="#e67e22",
                alpha=0.7, density=True)
    ax_sat.set_title("Saturation Distribution (%)", fontsize=10, fontweight="bold")
    ax_sat.set_xlabel("Saturation (0–100%)", fontsize=8)
    ax_sat.grid(True, alpha=0.25)

    # ── Row 3: Cluster table + stats text ────────────────────
    ax_tbl = fig.add_subplot(gs[3, :2])
    ax_tbl.axis("off")

    col_labels = ["#", "Pixels", "%", "HEX",
                  "R", "G", "B",
                  "H°", "S%", "V%",
                  "L", "C", "H(LCH)",
                  "Name"]
    rows = []
    cell_colors = []
    for i, c in enumerate(clusters):
        r_, g_, b_ = c["rgb"]
        h_, s_, v_ = c["hsv"]
        lc, cc, hc = c["lch"]
        row = [
            str(i + 1),
            f"{c['pixels']:,}",
            f"{c['percentage']:.2f}",
            c["hex"],
            str(r_), str(g_), str(b_),
            str(int(h_)), str(int(s_)), str(int(v_)),
            str(int(lc)), str(int(cc)), str(int(hc)),
            c["name"],
        ]
        rows.append(row)
        swatch_color = [v / 255.0 for v in c["rgb"]] + [1.0]
        cell_colors.append(
            [swatch_color] + ["#ffffff"] * (len(col_labels) - 1)
        )

    tbl = ax_tbl.table(
        cellText=rows,
        colLabels=col_labels,
        cellLoc="center",
        loc="center",
        cellColours=cell_colors,
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(7.5)
    tbl.scale(1, 1.4)
    # Style header row
    for col in range(len(col_labels)):
        tbl[0, col].set_facecolor("#2c3e50")
        tbl[0, col].set_text_props(color="white", fontweight="bold")
    ax_tbl.set_title("Cluster Details", fontsize=11, fontweight="bold", pad=8)

    # Stats text panel
    ax_stats = fig.add_subplot(gs[3, 2])
    ax_stats.axis("off")
    lines = ["IMAGE STATISTICS\n"]
    for space, channels in stats.items():
        lines.append(f"── {space} ──────────────────")
        for ch, v in channels.items():
            lines.append(
                f"  {ch:2s}  μ={v['mean']:7.1f}  "
                f"σ={v['std']:6.1f}  "
                f"[{v['min']:.0f}–{v['max']:.0f}]"
            )
        lines.append("")
    ax_stats.text(
        0.04, 0.97, "\n".join(lines),
        transform=ax_stats.transAxes,
        fontsize=7.2, va="top", fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="white",
                  edgecolor="#cccccc", alpha=0.95),
    )
    ax_stats.set_title("Per-Channel Statistics", fontsize=11, fontweight="bold", pad=8)

    # ── Title ────────────────────────────────────────────────
    fig.suptitle(
        f"Image Color Summarizer  ·  {image_path.name}  "
        f"({img_small.size[0]}×{img_small.size[1]} px)",
        fontsize=13, fontweight="bold", y=1.005,
    )

    out_path = output_dir / f"{image_path.stem}_color_summary.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor="#f5f5f5")
    plt.close(fig)
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Text output
# ─────────────────────────────────────────────────────────────────────────────

def print_report(image_path: Path, img: Image.Image,
                 stats: dict, clusters: list) -> None:
    """Print a nicely-formatted summary to stdout."""
    W = 72
    sep = "═" * W
    thin = "─" * W

    print(f"\n{sep}")
    print(f"  IMAGE COLOR SUMMARIZER")
    print(f"  File : {image_path.name}")
    print(f"  Size : {img.size[0]} × {img.size[1]} px  |  Mode: {img.mode}")
    print(f"{sep}\n")

    print("  COLOR SPACE STATISTICS")
    print(f"  {thin}")
    print(f"  {'Space':<5}  {'Ch':<3}  {'Mean':>8}  {'Median':>8}  "
          f"{'Min':>8}  {'Max':>8}  {'Std':>8}")
    print(f"  {thin}")
    for space, channels in stats.items():
        for ch, v in channels.items():
            print(f"  {space:<5}  {ch:<3}  "
                  f"{v['mean']:8.2f}  {v['median']:8.2f}  "
                  f"{v['min']:8.2f}  {v['max']:8.2f}  "
                  f"{v['std']:8.2f}")
        print()

    print(f"  COLOR CLUSTERS")
    print(f"  {thin}")
    hdr = (f"  {'#':>2}  {'Pixels':>8}  {'%':>6}  {'HEX':>7}  "
           f"{'R':>4}{'G':>4}{'B':>4}  "
           f"{'H°':>5}{'S%':>5}{'V%':>5}  "
           f"{'L':>5}{'C':>5}{'H°':>5}  Name")
    print(hdr)
    print(f"  {thin}")
    for i, c in enumerate(clusters, 1):
        r, g, b = c["rgb"]
        h, s, v = c["hsv"]
        lv, cv, hv = c["lch"]
        print(
            f"  {i:>2}  {c['pixels']:>8,}  {c['percentage']:>6.2f}  "
            f"{c['hex']:>7}  "
            f"{r:>4}{g:>4}{b:>4}  "
            f"{int(h):>5}{int(s):>5}{int(v):>5}  "
            f"{int(lv):>5}{int(cv):>5}{int(hv):>5}  "
            f"{c['name']}"
        )
    print(f"\n{sep}\n")


# ─────────────────────────────────────────────────────────────────────────────
# JSON export
# ─────────────────────────────────────────────────────────────────────────────

def save_json_report(
    image_path: Path,
    img: Image.Image,
    stats: dict,
    clusters: list,
    output_dir: Path,
) -> Path:
    clean = []
    for c in clusters:
        d = {k: v for k, v in c.items() if not k.startswith("_")}
        d["rgb"] = list(d["rgb"])
        d["hsv"] = list(d["hsv"])
        d["lab"] = list(d["lab"])
        d["lch"] = list(d["lch"])
        clean.append(d)

    payload = {
        "image":      str(image_path.resolve()),
        "width":      img.size[0],
        "height":     img.size[1],
        "statistics": stats,
        "clusters":   clean,
    }

    out = output_dir / f"{image_path.stem}_color_summary.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def analyze(
    image_path: str | Path,
    k: int = 6,
    output_dir: str | Path | None = None,
    max_pixels: int = 300_000,
    use_lab: bool = True,
    save_vis: bool = True,
    save_json: bool = True,
) -> tuple[dict, list]:
    """
    Run the full color analysis pipeline on one image.

    Returns
    -------
    stats    : nested dict of per-channel statistics
    clusters : list of cluster dicts
    """
    image_path = Path(image_path)
    if not image_path.exists():
        sys.exit(f"[ERROR] File not found: {image_path}")

    out_dir = Path(output_dir) if output_dir else image_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Load ────────────────────────────────────────────────
    print(f"\n[→] {image_path.name}")
    img = Image.open(image_path).convert("RGB")
    print(f"    {img.size[0]}×{img.size[1]} px — {img.size[0]*img.size[1]:,} pixels")

    # Downsample for speed while keeping representative statistics
    img_small = img
    total = img.size[0] * img.size[1]
    if total > max_pixels:
        scale = math.sqrt(max_pixels / total)
        nw = max(1, int(img.size[0] * scale))
        nh = max(1, int(img.size[1] * scale))
        img_small = img.resize((nw, nh), Image.LANCZOS)
        print(f"    Downsampled to {nw}×{nh} for analysis")

    pixels = np.array(img_small).reshape(-1, 3)

    # ── Statistics ──────────────────────────────────────────
    print(f"[→] Computing statistics…")
    stats = image_statistics(pixels)

    # ── Clustering ──────────────────────────────────────────
    print(f"[→] Clustering (k={k}, space={'Lab' if use_lab else 'RGB'})…")
    clusters, labels = cluster_pixels(pixels, k=k, use_lab=use_lab)

    # ── Report ──────────────────────────────────────────────
    print_report(image_path, img, stats, clusters)

    # ── Visualization ───────────────────────────────────────
    if save_vis:
        print(f"[→] Rendering visualization…")
        vis_path = build_visualization(
            image_path, img_small, pixels, stats, clusters, labels, out_dir
        )
        print(f"[✓] Saved: {vis_path}")

    # ── JSON ────────────────────────────────────────────────
    if save_json:
        json_path = save_json_report(image_path, img, stats, clusters, out_dir)
        print(f"[✓] Saved: {json_path}")

    return stats, clusters


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="color_summarizer",
        description=(
            "Image Color Summarizer — descriptive statistics (RGB/HSV/Lab/LCH)\n"
            "and k-means color clustering, equivalent to mk.bcgsc.ca/color-summarizer/"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python color_summarizer.py photo.jpg
  python color_summarizer.py photo.jpg -k 8
  python color_summarizer.py *.jpg -k 6 -o ./results
  python color_summarizer.py museum.png -k 5 --max-pixels 500000
  python color_summarizer.py logo.png --no-vis          # JSON only
        """,
    )
    p.add_argument("images", nargs="+", metavar="IMAGE",
                   help="Input image(s): JPEG, PNG, TIFF, WebP, BMP …")
    p.add_argument("-k", "--clusters", type=int, default=6, metavar="N",
                   help="Number of color clusters (default: 6)")
    p.add_argument("-o", "--output", default=None, metavar="DIR",
                   help="Output directory (default: same folder as input)")
    p.add_argument("--max-pixels", type=int, default=300_000, metavar="N",
                   help="Max pixels used for analysis; larger = slower (default: 300000)")
    p.add_argument("--no-vis",  action="store_true",
                   help="Skip PNG visualization output")
    p.add_argument("--no-json", action="store_true",
                   help="Skip JSON report output")
    p.add_argument("--rgb-space", action="store_true",
                   help="Cluster in RGB space instead of perceptual Lab space")
    return p


def main() -> None:
    args = build_parser().parse_args()
    for img_path in args.images:
        analyze(
            img_path,
            k=args.clusters,
            output_dir=args.output,
            max_pixels=args.max_pixels,
            use_lab=not args.rgb_space,
            save_vis=not args.no_vis,
            save_json=not args.no_json,
        )


if __name__ == "__main__":
    main()
