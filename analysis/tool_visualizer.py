"""
Tool diagnosis visualizer.

Renders a PNG snapshot capturing the full context of a stabilization iteration:
  - Cyclomatic complexity score + gauge
  - Revision timeline (hash history from tool_change_log)
  - Diagnosis text
  - Source heatmap (line-token-count proxy for complexity hotspots)

The snapshot is stored as a base64 data URI in tool_stabilization_log.diagnosis_img.
At 640x640 resolution this costs ~100 VLM vision tokens — roughly 10x compression
vs the equivalent text, consistent with DeepSeek-OCR (arXiv:2510.18234) findings.
Older snapshots can be downsampled progressively to simulate the forgetting curve
described in Figure 13 of that paper.
"""

from __future__ import annotations

import base64
import inspect
import io
import logging
import textwrap
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_RENDER_DPI = 96
_RENDER_W_IN = 640 / _RENDER_DPI
_RENDER_H_IN = 640 / _RENDER_DPI


def compute_complexity(fn: Callable) -> int:
    """Return the cyclomatic complexity of *fn* using radon.

    Falls back to 0 if radon is unavailable or source cannot be retrieved.
    Higher values indicate more branching paths and maintenance risk.
    """
    try:
        from radon.complexity import cc_visit
        source = inspect.getsource(fn)
        results = cc_visit(source)
        if not results:
            return 1
        return max(r.complexity for r in results)
    except Exception as exc:
        logger.warning("compute_complexity: failed for %r — %s", getattr(fn, "__name__", fn), exc)
        return 0


def _source_heatmap_data(fn: Callable) -> list[int]:
    """Return per-line token counts as a simple complexity proxy."""
    try:
        source = inspect.getsource(fn)
        return [len(line.split()) for line in source.splitlines()]
    except OSError:
        return []


def render_diagnosis_snapshot(
    tool_name: str,
    fn: Callable,
    diagnosis_text: str,
    revision_history: Optional[list[dict]] = None,
    complexity: Optional[int] = None,
    downsample_factor: float = 1.0,
) -> str:
    """Render a diagnosis snapshot PNG and return it as a base64 data URI.

    Args:
        tool_name:        Logical tool name (displayed in title).
        fn:               The Python callable being diagnosed.
        diagnosis_text:   Human/AI-written diagnosis of why the tool is unstable.
        revision_history: List of dicts from tool_change_log (keys: revision,
                          old_hash, new_hash, changed_at, reason). Optional.
        complexity:       Pre-computed cyclomatic complexity. Computed if None.
        downsample_factor: 1.0 = full 640x640; 0.5 = 320x320 (forgetting curve).

    Returns:
        Data URI string: ``"data:image/png;base64,<encoded>"``
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    import warnings

    # Suppress CJK glyph warnings — diagnosis text may contain Chinese;
    # the heatmap / gauge panels are unaffected and remain readable.
    warnings.filterwarnings("ignore", message="Glyph.*missing from font")

    if complexity is None:
        complexity = compute_complexity(fn)

    w = _RENDER_W_IN * downsample_factor
    h = _RENDER_H_IN * downsample_factor
    dpi = max(int(_RENDER_DPI * downsample_factor), 48)

    fig = plt.figure(figsize=(w, h), dpi=dpi, facecolor="#0f1117")
    gs = gridspec.GridSpec(
        3, 2, figure=fig,
        hspace=0.55, wspace=0.35,
        left=0.08, right=0.97, top=0.88, bottom=0.06,
    )

    # Title
    complexity_color = (
        "#e05252" if complexity >= 10 else
        "#f0c040" if complexity >= 5 else
        "#52c07a"
    )
    fig.suptitle(
        f"{tool_name}   CC={complexity}",
        color=complexity_color,
        fontsize=7 * downsample_factor,
        fontweight="bold", y=0.96,
    )

    # Panel 1: Source heatmap
    ax_heat = fig.add_subplot(gs[0, 0])
    line_weights = _source_heatmap_data(fn)
    if line_weights:
        colors = [
            "#e05252" if w > 12 else "#f0c040" if w > 6 else "#4a90d9"
            for w in line_weights
        ]
        ax_heat.barh(range(len(line_weights)), line_weights,
                     color=colors, height=1.0, linewidth=0)
        ax_heat.set_xlim(0, max(line_weights) * 1.1)
        ax_heat.invert_yaxis()
    ax_heat.set_facecolor("#1a1d27")
    ax_heat.set_title("Source Density", color="#aaa",
                      fontsize=5 * downsample_factor, pad=2)
    ax_heat.tick_params(colors="#555", labelsize=4 * downsample_factor)
    for spine in ax_heat.spines.values():
        spine.set_color("#333")

    # Panel 2: Complexity gauge
    ax_gauge = fig.add_subplot(gs[0, 1])
    ax_gauge.set_facecolor("#1a1d27")
    ax_gauge.set_xlim(0, 20)
    ax_gauge.set_ylim(0, 1)
    prev = 0
    for thresh, color in [(5, "#52c07a"), (10, "#f0c040"), (20, "#e05252")]:
        ax_gauge.barh(0.5, thresh - prev, left=prev, height=0.4,
                      color=color, linewidth=0)
        prev = thresh
    marker_x = min(complexity, 20)
    ax_gauge.axvline(x=marker_x, color="white",
                     linewidth=1.5 * downsample_factor)
    ax_gauge.text(marker_x, 0.05, f"{complexity}", color="white",
                  fontsize=6 * downsample_factor, ha="center")
    ax_gauge.set_title("Cyclomatic Complexity", color="#aaa",
                       fontsize=5 * downsample_factor, pad=2)
    ax_gauge.axis("off")

    # Panel 3: Revision timeline
    ax_time = fig.add_subplot(gs[1, :])
    ax_time.set_facecolor("#1a1d27")
    if revision_history:
        revisions = [r["revision"] for r in revision_history]
        ax_time.scatter(revisions, [0.5] * len(revisions),
                        c="#4a90d9", s=20 * downsample_factor, zorder=3)
        ax_time.plot(revisions, [0.5] * len(revisions),
                     color="#333", linewidth=0.8 * downsample_factor, zorder=2)
        for r in revision_history:
            label = (r.get("reason") or r.get("new_hash", "")[:6] or "")[:12]
            ax_time.text(r["revision"], 0.62, label, color="#888",
                         fontsize=3.5 * downsample_factor,
                         ha="center", rotation=30)
    else:
        ax_time.text(0.5, 0.5, "no revision history yet",
                     color="#555", ha="center", va="center",
                     transform=ax_time.transAxes,
                     fontsize=5 * downsample_factor)
    ax_time.set_title("Revision Timeline", color="#aaa",
                      fontsize=5 * downsample_factor, pad=2)
    ax_time.set_ylim(0, 1)
    ax_time.axis("off")

    # Panel 4: Diagnosis text
    ax_diag = fig.add_subplot(gs[2, :])
    ax_diag.set_facecolor("#12151f")
    wrapped = textwrap.fill(diagnosis_text, width=70)
    ax_diag.text(
        0.02, 0.88, wrapped,
        color="#d0d0d0", fontsize=4.5 * downsample_factor,
        va="top", ha="left", transform=ax_diag.transAxes,
        bbox=dict(facecolor="#1a1d27", edgecolor="#333", boxstyle="round,pad=0.3"),
    )
    ax_diag.set_title("Diagnosis", color="#aaa",
                      fontsize=5 * downsample_factor, pad=2)
    ax_diag.axis("off")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def downsample_snapshot(data_uri: str, factor: float = 0.5) -> str:
    """Resize an existing snapshot data URI to simulate the forgetting curve.

    A factor of 0.5 halves both dimensions (~25 vision tokens from 100),
    mirroring the progressive blurring described in DeepSeek-OCR Figure 13.

    Args:
        data_uri: Existing ``"data:image/png;base64,..."`` string.
        factor:   Scale factor (0 < factor < 1).

    Returns:
        New data URI at reduced resolution.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg

    _, encoded = data_uri.split(",", 1)
    img_bytes = base64.b64decode(encoded)
    img_array = mpimg.imread(io.BytesIO(img_bytes))

    h, w = img_array.shape[:2]
    new_h = max(32, int(h * factor))
    new_w = max(32, int(w * factor))

    fig, ax = plt.subplots(figsize=(new_w / 96, new_h / 96), dpi=96)
    ax.imshow(img_array, aspect="auto", interpolation="bilinear")
    ax.axis("off")
    fig.subplots_adjust(0, 0, 1, 1)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=96, bbox_inches="tight")
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"
