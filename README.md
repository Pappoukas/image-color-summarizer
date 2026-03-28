# Image Color Summarizer 🎨

Python equivalent of [mk.bcgsc.ca/color-summarizer](https://mk.bcgsc.ca/color-summarizer/) — analyzes image colors using descriptive statistics across multiple color spaces and groups pixels with k-means clustering.

## What it does

For any image (JPEG, PNG, TIFF, WebP, BMP…) the tool produces:

| Output | Description |
|--------|-------------|
| **Statistics** | Mean, median, min, max, std for every channel of RGB, HSV, Lab and LCH |
| **Color clusters** | k-means groups of visually similar pixels with hex, RGB, HSV, Lab, LCH values and a human-readable color name |
| **PNG visualization** | Original image · color bar · cluster swatches · partition image · histograms · cluster table |
| **JSON report** | Machine-readable version of all the above |

## Quick start

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/image-color-summarizer.git
cd image-color-summarizer

# 2. Install dependencies (Python 3.9+)
pip install -r requirements.txt

# 3. Run
python color_summarizer.py photo.jpg
```

Output files land in the same folder as the input image (or in `-o DIR`):
- `photo_color_summary.png`
- `photo_color_summary.json`

## Usage

```
python color_summarizer.py [-h] [-k N] [-o DIR] [--max-pixels N]
                           [--no-vis] [--no-json] [--rgb-space]
                           IMAGE [IMAGE ...]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `-k N` / `--clusters N` | `6` | Number of color clusters |
| `-o DIR` / `--output DIR` | input folder | Output directory |
| `--max-pixels N` | `300000` | Pixels used for analysis (larger = slower, more accurate) |
| `--no-vis` | — | Skip PNG visualization |
| `--no-json` | — | Skip JSON report |
| `--rgb-space` | — | Cluster in RGB space (default: perceptual Lab space) |

### Examples

```bash
# Default: 6 clusters, save PNG + JSON next to the image
python color_summarizer.py castle.jpg

# 8 clusters, save to a results folder
python color_summarizer.py castle.jpg -k 8 -o ./results

# Batch — all JPEGs in current directory
python color_summarizer.py *.jpg -k 6 -o ./results

# High-accuracy analysis of a large image
python color_summarizer.py panorama.tif --max-pixels 1000000

# JSON only (no visualization)
python color_summarizer.py logo.png --no-vis
```

## Color spaces

| Space | Channels | Notes |
|-------|----------|-------|
| **RGB** | R, G, B (0–255) | Standard display values |
| **HSV** | H (0–360°), S (0–100%), V (0–100%) | Hue / Saturation / Value |
| **Lab** | L (0–100), a, b (≈ –128 … +127) | CIE L\*a\*b\*, D65 illuminant |
| **LCH** | L (0–100), C ≥ 0, H (0–360°) | Cylindrical form of Lab (perceptual) |

Clustering is performed in Lab space by default because it is perceptually uniform — equal distances correspond to equally noticeable color differences.

## Output example (console)

```
════════════════════════════════════════════════════════════════════════
  IMAGE COLOR SUMMARIZER
  File : photo.jpg
  Size : 1920 × 1080 px  |  Mode: RGB
════════════════════════════════════════════════════════════════════════

  COLOR SPACE STATISTICS
  ────────────────────────────────────────────────────────────────────
  Space  Ch     Mean    Median       Min       Max       Std
  ────────────────────────────────────────────────────────────────────
  RGB    R     118.43   117.00      0.00    255.00     64.21
  RGB    G     102.11    99.00      0.00    255.00     58.33
  ...

  COLOR CLUSTERS
  ────────────────────────────────────────────────────────────────────
   #    Pixels       %     HEX     R   G   B    H°   S%   V%     L    C   H°  Name
   1   432,110   20.84  #7A8F6B  122 143 107   97   25   56    56   16  120  green
  ...
```

## Requirements

- Python **3.9+**
- `Pillow` — image I/O
- `numpy` — array math
- `scikit-learn` — k-means
- `matplotlib` — visualization

Install all with: `pip install -r requirements.txt`

## License

MIT — free to use, modify and distribute.

---

> Inspired by Martin Krzywinski's [Image Color Summarizer](https://mk.bcgsc.ca/color-summarizer/).  
> Color space math follows the IEC 61966-2-1 sRGB standard and CIE recommendations.
