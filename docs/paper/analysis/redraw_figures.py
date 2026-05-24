"""
Redraw Figure 1, 2, 3a, 3b — simplified academic versions
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np

OUT = "I:/Evo_PRISM/docs/images/"

# ─────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────

def box(ax, x, y, w, h, label, sublabel="",
        fc="#FFFFFF", ec="#333333", lw=1.4,
        fs=9, sfs=7.5, bold=False, radius=0.03,
        ha="center", va="center", zorder=3):
    bp = FancyBboxPatch((x - w/2, y - h/2), w, h,
                         boxstyle=f"round,pad={radius}",
                         facecolor=fc, edgecolor=ec, linewidth=lw, zorder=zorder)
    ax.add_patch(bp)
    weight = "bold" if bold else "normal"
    ax.text(x, y + (0.012 if sublabel else 0), label,
            ha=ha, va=va, fontsize=fs, fontweight=weight,
            color="#1a1a1a", zorder=zorder+1, wrap=True)
    if sublabel:
        ax.text(x, y - 0.018, sublabel,
                ha="center", va="center", fontsize=sfs,
                color="#555555", zorder=zorder+1)

def arrow(ax, x0, y0, x1, y1, label="", color="#555555",
          lw=1.3, style="-|>", zorder=2):
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(arrowstyle=style, color=color,
                                lw=lw, connectionstyle="arc3,rad=0.0"),
                zorder=zorder)
    if label:
        mx, my = (x0+x1)/2, (y0+y1)/2
        ax.text(mx+0.01, my, label, fontsize=7, color=color,
                ha="left", va="center", zorder=zorder+1)

def diamond(ax, cx, cy, w, h, label, fc="#fff9c4", ec="#f0a500",
            fs=8.5, lw=1.4, zorder=3):
    pts = np.array([[cx, cy+h/2], [cx+w/2, cy],
                    [cx, cy-h/2], [cx-w/2, cy]])
    patch = plt.Polygon(pts, closed=True,
                         facecolor=fc, edgecolor=ec, linewidth=lw, zorder=zorder)
    ax.add_patch(patch)
    ax.text(cx, cy, label, ha="center", va="center",
            fontsize=fs, fontweight="bold", color="#6b4f00", zorder=zorder+1)

def band(ax, y, h, label, fc, ec, fs=8.5, lw=1.2, zorder=1, x0=0, x1=1):
    rect = FancyBboxPatch((x0, y - h/2), x1-x0, h,
                           boxstyle="round,pad=0.005",
                           facecolor=fc, edgecolor=ec,
                           linewidth=lw, zorder=zorder,
                           transform=ax.transAxes, clip_on=False)
    ax.add_patch(rect)
    ax.text(x0 + 0.008, y - h/2 + 0.013, label,
            ha="left", va="bottom", fontsize=fs,
            fontweight="bold", color=ec,
            transform=ax.transAxes, zorder=zorder+1)


# ═══════════════════════════════════════════════════════════════
# FIGURE 1  —  System Architecture (simplified)
# ═══════════════════════════════════════════════════════════════

def draw_fig1():
    """
    Layout (no in-figure title — use figure caption in paper):

    LEFT COLUMN  (x ~ 0.30)          RIGHT PANEL  (x ~ 0.72)
    ─────────────────────────         ─────────────────────────
    [User Query]                      ┌── HELIX (dashed) ──┐
         ↓                            │  LLM Brain  Toolset │
    [Gateway]  ─── Miss ──────────→  │  Code Gen   Sandbox │
     ↙ L1  ↓ L2                      └─────────────────────┘
    [L1 Cache] [L2 Cache]
         ↘       ↓          ← HELIX ↙
          [Final Output]
    ════════════════════════════════════════
    [ENGRAM: L1 Gold | L2 Silver | L3 Bronze]
    """
    fig, ax = plt.subplots(figsize=(12, 9))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("#fafafa")

    C_GW  = "#fff3e0";  E_GW  = "#e65100"
    C_L1  = "#e8f5e9";  E_L1  = "#2e7d32"
    C_L2  = "#e3f2fd";  E_L2  = "#1565c0"
    E_HX  = "#f9a825"
    E_ENG = "#6a1b9a"
    C_OUT = "#e8f5e9";  E_OUT = "#2e7d32"
    C_USR = "#fce4ec";  E_USR = "#c62828"

    # ── Y anchors ──
    Y_USR   = .88
    Y_GW    = .74
    Y_CACHE = .58
    Y_OUT   = .41
    Y_ENG   = .19
    ENG_H   = .18

    # ── X split: left flow at ~.30, HELIX panel at .58–.97 ──
    LX = .28   # left column centre

    # ═══ Left flow ═══

    # User Query
    box(ax, LX, Y_USR, .38, .078,
        "User Query / Natural Language Request",
        fc=C_USR, ec=E_USR, bold=True, fs=10)

    # Gateway
    box(ax, LX, Y_GW, .44, .082,
        "Adaptive De-duplication & Routing Gateway",
        fc=C_GW, ec=E_GW, bold=True, fs=9.5)

    # User → Gateway
    arrow(ax, LX, Y_USR - .039, LX, Y_GW + .041)

    # L1 Cache (left)
    box(ax, .10, Y_CACHE, .22, .082,
        "L1 Gold Cache Hit",
        "Sub-second Response",
        fc=C_L1, ec=E_L1, fs=9, sfs=8)

    # L2 Cache (right of L1)
    box(ax, .35, Y_CACHE, .22, .082,
        "L2 Silver Cache Hit",
        "Fast SQL / MCP Exec",
        fc=C_L2, ec=E_L2, fs=9, sfs=8)

    # Gateway → L1 / L2
    arrow(ax, .17, Y_GW, .10, Y_CACHE + .041,
          label="L1 Hit", color=E_L1)
    arrow(ax, .33, Y_GW, .35, Y_CACHE + .041,
          label="L2 Hit", color=E_L2)

    # Final Output
    box(ax, LX, Y_OUT, .36, .082,
        "Final Scientific Output",
        "PNG · DEG CSV · H5AD · Reports",
        fc=C_OUT, ec=E_OUT, fs=9.5, sfs=8)

    # L1 → Output
    arrow(ax, .10, Y_CACHE - .041, .13, Y_OUT + .041, color=E_OUT)
    # L2 → Output
    arrow(ax, .35, Y_CACHE - .041, .32, Y_OUT + .041, color=E_OUT)

    # ═══ HELIX panel (right, tall, clear of left flow) ═══
    HX_L = .57; HX_R = .97
    HX_B = .47; HX_T = .84

    hx_patch = FancyBboxPatch(
        (HX_L, HX_B), HX_R - HX_L, HX_T - HX_B,
        boxstyle="round,pad=0.012",
        facecolor="#fffde7", edgecolor=E_HX,
        linewidth=1.8, linestyle="--", zorder=2)
    ax.add_patch(hx_patch)
    ax.text((HX_L + HX_R)/2, HX_T - .024,
            "Self-Evolving Tool Loop  (HELIX)",
            ha="center", va="center", fontsize=9.5,
            fontweight="bold", color=E_HX, zorder=3)

    # 2×2 grid inside HELIX — generous spacing
    HX_CX = (HX_L + HX_R) / 2       # 0.77
    BW = .15;  BH = .09
    dx = BW/2 + .020
    y_top_b = HX_B + (HX_T - HX_B) * .64
    y_bot_b = HX_B + (HX_T - HX_B) * .28

    xl = HX_CX - dx;  xr = HX_CX + dx

    box(ax, xl, y_top_b, BW, BH, "LLM Agent Brain",
        fc="#fff8e1", ec=E_HX, fs=8.5)
    box(ax, xl, y_bot_b, BW, BH, "Ad-hoc Code\nGeneration",
        fc="#fff8e1", ec=E_HX, fs=8)
    box(ax, xr, y_bot_b, BW, BH, "Secure\nSandbox",
        fc="#fff8e1", ec=E_HX, fs=8)
    box(ax, xr, y_top_b, BW, BH, "L2 Active\nToolset",
        fc=C_L2, ec=E_L2, fs=8.5)

    # counter-clockwise cycle
    arrow(ax, xl, y_top_b - BH/2, xl, y_bot_b + BH/2)
    arrow(ax, xl + BW/2, y_bot_b, xr - BW/2, y_bot_b)
    arrow(ax, xr, y_bot_b + BH/2, xr, y_top_b - BH/2)
    arrow(ax, xr - BW/2, y_top_b, xl + BW/2, y_top_b)

    # Gateway → HELIX: straight horizontal arrow at Gateway level
    gw_right = LX + .44/2   # right edge of gateway ~0.50
    ax.annotate("", xy=(HX_L, Y_GW),
                xytext=(gw_right, Y_GW),
                arrowprops=dict(arrowstyle="-|>", color=E_HX, lw=1.5))
    ax.text((gw_right + HX_L)/2, Y_GW + .025,
            "Miss / No Tool", ha="center", va="bottom",
            fontsize=8, color=E_HX, fontweight="bold")

    # HELIX → Output (bottom-left of HELIX → Output box)
    arrow(ax, HX_L, HX_B + .02, .46, Y_OUT + .041, color=E_OUT)

    # ═══ ENGRAM band ═══
    bp2 = FancyBboxPatch(
        (.03, Y_ENG - ENG_H/2), .94, ENG_H,
        boxstyle="round,pad=0.012",
        facecolor="#f8f0ff", edgecolor=E_ENG,
        linewidth=1.8, linestyle="--", zorder=2)
    ax.add_patch(bp2)
    ax.text(.50, Y_ENG + ENG_H/2 - .024,
            "ENGRAM: Semantic Memory Lakehouse",
            ha="center", va="center", fontsize=10.5,
            fontweight="bold", color=E_ENG, zorder=3)

    box(ax, .16, Y_ENG - .018, .24, .078,
        "L1 Gold  (hermes_cache)",
        "Embeddings · TTL 7d · Auto-Inval",
        fc="#e8f5e9", ec=E_L1, fs=8, sfs=7.2)
    box(ax, .50, Y_ENG - .018, .26, .078,
        "L2 Silver  (bio_memory)",
        "Sample Registry · Analysis History",
        fc="#e3f2fd", ec=E_L2, fs=8, sfs=7.2)
    box(ax, .83, Y_ENG - .018, .24, .078,
        "L3 Bronze  (Read-Only)",
        "Raw Genomics Counts & Files",
        fc="#fafafa", ec="#78909c", fs=8, sfs=7.2)

    arrow(ax, .285, Y_ENG - .018, .365, Y_ENG - .018, color=E_ENG)
    arrow(ax, .635, Y_ENG - .018, .710, Y_ENG - .018, color=E_ENG)

    # Gateway → ENGRAM: dashed, runs left of left flow
    ax.annotate("",
        xy=(.10, Y_ENG + ENG_H/2),
        xytext=(.06, Y_GW - .041),
        arrowprops=dict(arrowstyle="-|>", color="#9c27b0",
                        lw=1.1, linestyle="dashed",
                        connectionstyle="arc3,rad=0.0"))
    ax.text(.03, (Y_GW + Y_ENG)/2 + .03,
            "Read Cache\n(0-Token)",
            ha="left", va="center", fontsize=7.5,
            color="#9c27b0", style="italic")

    # Cache → ENGRAM backfill (very faint, shows data flow)
    arrow(ax, .10, Y_CACHE - .041, .13, Y_ENG + ENG_H/2,
          color=E_L1, lw=0.8)
    arrow(ax, .35, Y_CACHE - .041, .46, Y_ENG + ENG_H/2,
          color=E_L2, lw=0.8)

    fig.tight_layout(pad=0.5)
    fig.savefig(OUT + "Figure1_System_Architecture_v3.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    print("Figure 1 done.")


# ═══════════════════════════════════════════════════════════════
# FIGURE 2  —  HELIX Loop (simplified, no formula cards)
# ═══════════════════════════════════════════════════════════════

def draw_fig2():
    fig, ax = plt.subplots(figsize=(11, 6.5))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis("off")

    # ── Band backgrounds ──
    # Sandbox band (top)
    bp_sb = FancyBboxPatch((.02, .52), .96, .38,
                            boxstyle="round,pad=0.01",
                            facecolor="#fffde7", edgecolor="#f9a825",
                            linewidth=1.6, linestyle="--", zorder=1)
    ax.add_patch(bp_sb)
    ax.text(.03, .875, "SANDBOX & DEVELOPMENT HORIZON  (Temporary / Ad-hoc)",
            fontsize=8.5, fontweight="bold", color="#f57f17",
            ha="left", va="center", zorder=2)

    # Production band (bottom)
    bp_pr = FancyBboxPatch((.02, .10), .96, .38,
                            boxstyle="round,pad=0.01",
                            facecolor="#e8f5e9", edgecolor="#2e7d32",
                            linewidth=1.6, linestyle="--", zorder=1)
    ax.add_patch(bp_pr)
    ax.text(.03, .455, "PRODUCTION & GOVERNANCE HORIZON  (Formalized & Versioned)",
            fontsize=8.5, fontweight="bold", color="#1b5e20",
            ha="left", va="center", zorder=2)

    # ── Colours ──
    C_SB = "#fff8e1"; E_SB = "#f9a825"
    C_PR = "#e8f5e9"; E_PR = "#2e7d32"
    C_DI = "#fff9c4"; E_DI = "#f0a500"

    # ── Step boxes ──
    # Top row: steps 1 → 2 → 3 → [gate]
    y_top = .70
    y_bot = .27

    box(ax, .12, y_top, .16, .11,
        "1. LLM\nAd-hoc Code",
        "Dynamic Script\nGeneration",
        fc=C_SB, ec=E_SB, fs=8.5, sfs=7.5)

    box(ax, .35, y_top, .16, .11,
        "2. Secure Sandbox",
        "Path Whitelist · AST\nResource Limits",
        fc=C_SB, ec=E_SB, fs=8.5, sfs=7.5)

    box(ax, .58, y_top, .17, .11,
        "3. Quality Evaluation",
        "McCabe CC · Halstead\nPASS 664-Test Suite",
        fc=C_SB, ec=E_SB, fs=8.5, sfs=7.5)

    # Promotion gate diamond
    diamond(ax, .81, y_top, .14, .13,
            "Autonomic\nPromotion\nGate",
            fc=C_DI, ec=E_DI, fs=8)

    # Bottom row: steps 4 ← 5 ← 6
    box(ax, .81, y_bot, .16, .11,
        "4. Formal MCP Tool",
        "Registered via\nSemantic Versioning",
        fc=C_PR, ec=E_PR, fs=8.5, sfs=7.5)

    box(ax, .58, y_bot, .17, .11,
        "5. L2 Active Toolset",
        "Hot-loaded via stdio/SSE\nfor Sub-second Reuse",
        fc=C_PR, ec=E_PR, fs=8.5, sfs=7.5)

    box(ax, .35, y_bot, .17, .11,
        "6. Health Monitor",
        "Usage Churn &\nRelative Churn Ratio",
        fc=C_PR, ec=E_PR, fs=8.5, sfs=7.5)

    # ── Main flow arrows (top) ──
    arrow(ax, .205, y_top, .265, y_top, color="#555", lw=1.4)   # 1→2
    arrow(ax, .435, y_top, .49,  y_top, color="#555", lw=1.4)   # 2→3
    arrow(ax, .672, y_top, .735, y_top, color="#555", lw=1.4)   # 3→gate

    # Gate YES → step 4 (down)
    ax.annotate("", xy=(.81, y_bot + .055), xytext=(.81, y_top - .065),
                arrowprops=dict(arrowstyle="-|>", color=E_PR, lw=1.5))
    ax.text(.83, (y_top + y_bot)/2, "YES\n(Promoted)", fontsize=7.5,
            color=E_PR, ha="left", va="center", fontweight="bold")

    # Gate NO → feedback (right curve back)
    ax.annotate("", xy=(.12, y_top + .06), xytext=(.81, y_top + .06),
                arrowprops=dict(arrowstyle="-|>", color="#e53935", lw=1.3,
                                connectionstyle="arc3,rad=-0.25"))
    ax.text(.46, y_top + .10, "NO — Refactor & Churn  (Health < 0.70)",
            ha="center", va="center", fontsize=7.5, color="#c62828",
            fontweight="bold")

    # ── Bottom flow arrows: 4 → 5 → 6 (right to left) ──
    # 4 → 5
    ax.annotate("", xy=(.672, y_bot), xytext=(.728, y_bot),
                arrowprops=dict(arrowstyle="-|>", color="#2e7d32", lw=1.5))
    ax.text(.700, y_bot + .045, "Hot-load", ha="center",
            fontsize=7.5, color="#2e7d32", fontweight="bold")
    # 5 → 6
    ax.annotate("", xy=(.445, y_bot), xytext=(.498, y_bot),
                arrowprops=dict(arrowstyle="-|>", color="#2e7d32", lw=1.5))
    ax.text(.472, y_bot + .045, "Monitor", ha="center",
            fontsize=7.5, color="#2e7d32", fontweight="bold")
    # 6 → feedback up the left side back to step 1
    ax.annotate("", xy=(.12, y_top - .055), xytext=(.35 - .085, y_bot),
                arrowprops=dict(arrowstyle="-|>", color="#e53935", lw=1.4,
                                connectionstyle="arc3,rad=0.4"))
    ax.text(.09, (y_top + y_bot)/2, "Churn\nFeedback",
            ha="center", va="center", fontsize=7, color="#c62828",
            fontweight="bold")

    # ── Title ──
    ax.text(.50, .96, "HELIX: Autonomic Tool Evolution & Code Memory Loop",
            ha="center", va="center", fontsize=13, fontweight="bold", color="#1a1a1a")
    ax.text(.50, .935, "Continuous Refactoring, Promotion, and Expiration Lifecycle of Scientific Code",
            ha="center", va="center", fontsize=8.5, color="#555555")

    fig.tight_layout(pad=0.3)
    fig.savefig(OUT + "Figure2_HELIX_Loop_v2.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    print("Figure 2 done.")


# ═══════════════════════════════════════════════════════════════
# FIGURE 3a  —  ENGRAM Medallion Lakehouse (clean 3-tier)
# ═══════════════════════════════════════════════════════════════

def draw_fig3a():
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis("off")

    # Background
    fig.patch.set_facecolor("#fafafa")

    col_x = [.17, .50, .83]
    col_w = .27
    col_h = .58
    col_y = .42

    tier_data = [
        {
            "title": "L1  GOLD TIER",
            "subtitle": "hermes_cache  (DuckDB)",
            "items": [
                "1024-dim BGE-M3 Embeddings",
                "HNSW Index · Cosine Sim ≥ 0.88",
                "Figure Cache (base64-stripped PNG)",
                "TTL 7 Days · Auto-Invalidation",
            ],
            "fc": "#e8f5e9", "ec": "#2e7d32", "tc": "#1b5e20",
            "badge_fc": "#2e7d32", "badge_tc": "white",
        },
        {
            "title": "L2  SILVER TIER",
            "subtitle": "bio_memory  (DuckDB)",
            "items": [
                "Sample Registry · analysis_history",
                "Formalized Tool Registry (SemVer)",
                "tool_change_log · Modification Logs",
                "artifact_relations: DAG Dependency",
                "analysis_artifacts: Multi-modal Reports",
            ],
            "fc": "#e3f2fd", "ec": "#1565c0", "tc": "#0d47a1",
            "badge_fc": "#1565c0", "badge_tc": "white",
        },
        {
            "title": "L3  BRONZE TIER",
            "subtitle": "Read-Only Dataset",
            "items": [
                "39 GB Visium HD Genomics Matrix",
                "Gigapixel TIFF Tissue Imaging",
                "NTFS System-Level Write-Protection",
                "Fully Immutable Raw Reference",
            ],
            "fc": "#f5f5f5", "ec": "#607d8b", "tc": "#37474f",
            "badge_fc": "#607d8b", "badge_tc": "white",
        },
    ]

    for i, (cx, td) in enumerate(zip(col_x, tier_data)):
        # Main box
        bp = FancyBboxPatch((cx - col_w/2, col_y - col_h/2), col_w, col_h,
                             boxstyle="round,pad=0.012",
                             facecolor=td["fc"], edgecolor=td["ec"],
                             linewidth=2.0, zorder=2)
        ax.add_patch(bp)

        # Badge
        badge = FancyBboxPatch((cx - col_w/2 + 0.005, col_y + col_h/2 - 0.085),
                                col_w - 0.010, 0.075,
                                boxstyle="round,pad=0.006",
                                facecolor=td["badge_fc"], edgecolor="none",
                                zorder=3)
        ax.add_patch(badge)
        ax.text(cx, col_y + col_h/2 - 0.048, td["title"],
                ha="center", va="center", fontsize=9.5, fontweight="bold",
                color=td["badge_tc"], zorder=4)

        ax.text(cx, col_y + col_h/2 - 0.105, td["subtitle"],
                ha="center", va="center", fontsize=8, color=td["tc"],
                fontstyle="italic", zorder=3)

        for j, item in enumerate(td["items"]):
            iy = col_y + col_h/2 - 0.165 - j * 0.082
            ax.text(cx - col_w/2 + 0.022, iy, f"• {item}",
                    ha="left", va="center", fontsize=7.5, color="#333333",
                    zorder=3, wrap=True)

    # Arrows between tiers — draw above the boxes so labels don't overlap bullets
    mid_y = col_y + col_h/2 + .055   # above the tier boxes
    for x0, x1 in [(.305, .365), (.635, .695)]:
        ax.annotate("", xy=(x1, mid_y), xytext=(x0, mid_y),
                    arrowprops=dict(arrowstyle="<->", color="#90a4ae", lw=1.5))

    ax.text(.335, mid_y + .018, "Cache Backfill", ha="center",
            fontsize=7.5, color="#607d8b", fontweight="bold")
    ax.text(.665, mid_y + .018, "Parquet Structuring", ha="center",
            fontsize=7.5, color="#607d8b", fontweight="bold")

    # Immutable badge on L3
    ax.text(.83, col_y - col_h/2 + .03, "IMMUTABLE",
            ha="center", va="center", fontsize=7, fontweight="bold",
            color="white",
            bbox=dict(fc="#b71c1c", ec="none", boxstyle="round,pad=0.04"),
            zorder=5)

    # Backfill note at top
    ax.text(.50, .95, "ENGRAM: Semantic Memory Lakehouse",
            ha="center", va="center", fontsize=13, fontweight="bold",
            color="#4a148c")
    ax.text(.50, .925,
            "L1–L2–L3 Medallion Storage Schema with Semantic Cache & Immutable Raw Baseline",
            ha="center", va="center", fontsize=8.5, color="#555555")

    # Incoming query arrow
    ax.annotate("", xy=(.17, col_y + col_h/2 + .005),
                xytext=(.17, .88),
                arrowprops=dict(arrowstyle="-|>", color="#2e7d32", lw=1.5))
    ax.text(.17, .90, "Query / Embed", ha="center", fontsize=7.5, color="#2e7d32")

    fig.tight_layout(pad=0.4)
    fig.savefig(OUT + "Figure3a_ENGRAM_Lakehouse_v2.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    print("Figure 3a done.")


# ═══════════════════════════════════════════════════════════════
# FIGURE 3b  —  Retrospective Blast Radius (clean tree)
# ═══════════════════════════════════════════════════════════════

def draw_fig3b():
    fig, ax = plt.subplots(figsize=(9, 7))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("#fafafa")

    # Colour scale: conf 1.0 → 0.6
    def conf_color(c):
        if c >= 0.95:   return "#1b5e20", "#e8f5e9"   # dark green / light green
        elif c >= 0.85: return "#1565c0", "#e3f2fd"   # blue
        elif c >= 0.65: return "#e65100", "#fff3e0"   # orange
        else:           return "#b71c1c", "#ffebee"   # red

    def node(cx, cy, w, h, title, subtitle, conf, zorder=3):
        ec, fc = conf_color(conf)
        bp = FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                             boxstyle="round,pad=0.01",
                             facecolor=fc, edgecolor=ec,
                             linewidth=1.8, zorder=zorder)
        ax.add_patch(bp)
        ax.text(cx, cy + (0.012 if subtitle else 0), title,
                ha="center", va="center", fontsize=8.2,
                fontweight="bold", color=ec, zorder=zorder+1)
        if subtitle:
            ax.text(cx, cy - 0.020, subtitle,
                    ha="center", va="center", fontsize=7,
                    color="#444444", zorder=zorder+1)
        # confidence badge
        bx = cx + w/2 - 0.005
        by = cy + h/2 - 0.005
        ax.text(bx, by, f"{conf:.1f}",
                ha="right", va="top", fontsize=7.5,
                fontweight="bold", color=ec, zorder=zorder+2)

    def edge(ax, x0, y0, x1, y1, label="", color="#888"):
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="-|>", color=color,
                                    lw=1.3, connectionstyle="arc3,rad=0.0"),
                    zorder=2)
        if label:
            mx = (x0+x1)/2 + .012
            my = (y0+y1)/2
            ax.text(mx, my, label, fontsize=7, color=color, ha="left", va="center")

    # ── Nodes ──
    # EPICENTER
    node(.50, .87, .32, .09,
         "EPICENTER (Mutated Source)",
         "Tool: bio_run_bulk_eda v2.0.0",
         conf=1.0)

    # Depth 1
    node(.50, .70, .34, .08,
         "Level 1: Direct Affected Run",
         "showcase SDS-D0D1D2  [Exact]",
         conf=1.0)

    # Depth 2 — two nodes
    node(.25, .53, .34, .08,
         "Level 2: Derived Data",
         "cellpose_cells.h5ad  [Same-Analysis]",
         conf=0.9)
    node(.75, .53, .34, .08,
         "Level 2: Derived Figure",
         "mask_he_overlay.png  [Same-Analysis]",
         conf=0.9)

    # Depth 3 — one node spanning
    node(.50, .36, .40, .08,
         "Level 3: Downstream Case Studies",
         "112-Sample DEG Reports  [Heuristic]",
         conf=0.6)

    # ── Edges ──
    edge(ax, .50, .826, .50, .744, color="#1b5e20")        # epi → L1
    edge(ax, .35, .696, .27, .574, color="#1565c0")         # L1 → L2 left
    edge(ax, .65, .696, .73, .574, color="#1565c0")         # L1 → L2 right
    edge(ax, .30, .506, .40, .404, color="#e65100")         # L2L → L3
    edge(ax, .70, .506, .60, .404, color="#e65100")         # L2R → L3

    # Depth labels (bottom of each node, centred)
    for y, label in [
        (.87, "Epicenter"),
        (.70, "Depth 1  ·  Exact  ·  1.0"),
        (.53, "Depth 2  ·  Same-Analysis  ·  0.9"),
        (.36, "Depth 3  ·  Heuristic  ·  0.6"),
    ]:
        ax.text(.50, y - .058, label, ha="center", va="center",
                fontsize=7, color="#777777", style="italic")

    # ── Legend ──
    legend_x, legend_y = .05, .20
    ax.text(legend_x, legend_y + .03, "Confidence Decay",
            fontsize=8, fontweight="bold", color="#333333")
    for conf, label in [(1.0, "Exact"), (0.9, "Same-Analysis"), (0.6, "Heuristic")]:
        ec, fc = conf_color(conf)
        bp = FancyBboxPatch((legend_x, legend_y - .04), .10, .03,
                             boxstyle="round,pad=0.005",
                             facecolor=fc, edgecolor=ec, linewidth=1.4, zorder=3)
        ax.add_patch(bp)
        ax.text(legend_x + .052, legend_y - .025, f"{conf:.1f}  {label}",
                ha="left", va="center", fontsize=7.5, color=ec)
        legend_y -= .055

    # ── Title ──
    ax.text(.50, .97, "ENGRAM: Retrospective Downstream Blast Radius",
            ha="center", va="center", fontsize=12.5,
            fontweight="bold", color="#1a1a1a")
    ax.text(.50, .948,
            "Recursive CTE Dependency Tracing with Confidence Decay",
            ha="center", va="center", fontsize=8.5, color="#555555")

    fig.tight_layout(pad=0.4)
    fig.savefig(OUT + "Figure3b_Blast_Radius_v2.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    print("Figure 3b done.")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    draw_fig1()
    draw_fig2()
    draw_fig3a()
    draw_fig3b()
    print("All figures saved to", OUT)
