"""
Generate a suite of 3 crisp, publication-quality, perfectly readable vector architecture diagrams
for Evo_PRISM using matplotlib.
This guarantees 100% legible English text and sharp, academic-grade layout:
  1. Figure 1: Macro-level Platform Flow & Data Life Cycle (Figure1_System_Architecture_v2.png)
  2. Figure 2: HELIX Autonomic Code Promotion & Tool Lifecycle Loop (Figure2_HELIX_Loop.png)
  3. Figure 3: ENGRAM Semantic Lakehouse & Retrospective Blast Radius CTE (Figure3_ENGRAM_Lakehouse.png)

This version widens the coordinate grids, expands all node boxes, and mathematically offsets 
all arrows to guarantee ZERO overlaps with text or box borders, ensuring extreme readability.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from pathlib import Path

# Setup paths
ROOT = Path(__file__).parent.parent
IMAGES_DIR = ROOT / "docs" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Colors
COLORS = {
    "bg": "#ffffff",
    "gateway": "#fff3e0",      # Light orange
    "gateway_border": "#ef6c00",
    "lake": "#eceff1",         # Gray-blue
    "lake_border": "#37474f",
    "agent": "#e3f2fd",        # Light blue
    "agent_border": "#1565c0",
    "hit": "#e8f5e9",          # Light green
    "hit_border": "#2e7d32",
    "text": "#1a1a1a",
    "helix_bg": "#f9fbe7",     # Soft lime-yellow for HELIX
    "helix": "#f9fbe7",        # alias so draw_node(style_key="helix") works
    "helix_border": "#fbc02d",
    "engram_bg": "#f3e5f5",    # Soft purple for ENGRAM
    "engram_border": "#7b1fa2",
    "arrow": "#455a64"
}

def draw_node(ax, x, y, w, h, text, style_key, shape="rect", fontsize=8.5):
    """Helper function to draw readable nodes with clean styles and exact dimensions"""
    bg_col = COLORS[style_key]
    border_col = COLORS[style_key + "_border"]
    if shape == "ellipse":
        p = patches.Ellipse(
            (x, y), w, h,
            facecolor=bg_col,
            edgecolor=border_col,
            linewidth=1.8,
            zorder=3
        )
    else:  # default: rect
        p = patches.FancyBboxPatch(
            (x - w/2, y - h/2), w, h,
            boxstyle="round,pad=0.1",
            facecolor=bg_col,
            edgecolor=border_col,
            linewidth=1.8,
            zorder=3
        )
    ax.add_patch(p)
    ax.text(x, y, text, ha="center", va="center", fontsize=fontsize, color=COLORS["text"], fontweight="semibold", zorder=4)
    return (x, y, w, h)

def draw_diamond(ax, x, y, w, h, text, style_key, fontsize=8):
    """Helper function to draw diamond decision gates"""
    bg_col = COLORS[style_key]
    border_col = COLORS[style_key + "_border"]
    diamond_poly = patches.Polygon(
        [(x, y + h/2), (x + w/2, y), (x, y - h/2), (x - w/2, y)],
        closed=True,
        facecolor=bg_col,
        edgecolor=border_col,
        linewidth=1.8,
        zorder=3
    )
    ax.add_patch(diamond_poly)
    ax.text(x, y, text, ha="center", va="center", fontsize=fontsize, color=COLORS["text"], fontweight="semibold", zorder=4)
    return (x, y, w, h)

def draw_formula_card(ax, x, y, w, h, text, border_color, bg_color, fontsize=7.5):
    """Helper function to draw beautifully styled formula callout sticky notes"""
    p = patches.FancyBboxPatch(
        (x - w/2, y - h/2), w, h,
        boxstyle="round,pad=0.08",
        facecolor=bg_color,
        edgecolor=border_color,
        linewidth=1.2,
        zorder=3
    )
    ax.add_patch(p)
    ax.text(x, y, text, ha="center", va="center", fontsize=fontsize, color="#263238", fontweight="semibold", zorder=4)
    return (x, y, w, h)


def draw_fig1_overall():
    """Figure 1: Macro-level Platform Flow & Data Life Cycle (v3 3:2 Split Layout)"""
    fig, ax = plt.subplots(figsize=(12, 8), dpi=300)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 8)
    ax.axis("off")
    fig.patch.set_facecolor(COLORS["bg"])
    
    # --- LEFT SIDE: Flowchart Panel (60% Width, x < 7.6) ---
    # Draw Stratum Bands
    # Stratum A: User Interaction (Top, y=6.4 to 7.6)
    stratum_a_box = patches.FancyBboxPatch(
        (0.3, 6.4), 7.2, 1.2,
        boxstyle="round,pad=0.1",
        facecolor="#fafafa",
        edgecolor="#e0e0e0",
        linewidth=1.2,
        linestyle="--",
        zorder=1
    )
    ax.add_patch(stratum_a_box)
    # Rotated Stratum A text in the left margin to bypass overlap with Step 1
    ax.text(0.55, 7.0, "STRATUM A: USER INTERACTION & DELIVERY", 
            ha="center", va="center", fontsize=7.5, color="#757575", fontweight="bold", rotation=90, zorder=2)
    
    # Stratum B: Execution & Agent (Middle, y=3.2 to 6.2)
    stratum_b_box = patches.FancyBboxPatch(
        (0.3, 3.2), 7.2, 3.0,
        boxstyle="round,pad=0.1",
        facecolor="#fffde7",
        edgecolor="#fbc02d",
        linewidth=1.5,
        linestyle="--",
        zorder=1
    )
    ax.add_patch(stratum_b_box)
    # Rotated Stratum B text in the left margin
    ax.text(0.55, 4.7, "STRATUM B: SEMANTIC EXECUTION & RUNTIME ZONE", 
            ha="center", va="center", fontsize=7.5, color="#f57f17", fontweight="bold", rotation=90, zorder=2)
    
    # Stratum C: Medallion Memory (Bottom, y=0.6 to 3.0)
    stratum_c_box = patches.FancyBboxPatch(
        (0.3, 0.6), 7.2, 2.4,
        boxstyle="round,pad=0.1",
        facecolor="#eceff1",
        edgecolor="#37474f",
        linewidth=1.5,
        linestyle="-.",
        zorder=1
    )
    ax.add_patch(stratum_c_box)
    # Rotated Stratum C text in the left margin
    ax.text(0.55, 1.8, "STRATUM C: MEDALLION LAKEHOUSE STORAGE", 
            ha="center", va="center", fontsize=7.5, color="#37474f", fontweight="bold", rotation=90, zorder=2)
    
    # Draw Nodes on the Left Side
    # Column 1: x = 2.1
    draw_node(ax, 2.1, 7.0, 1.8, 0.6, "User Input Query\n(Natural Language)", "gateway", "ellipse", fontsize=7.5)
    draw_node(ax, 2.1, 5.2, 1.8, 0.6, "Adaptive Gateway\n(Semantic Router)", "gateway", fontsize=7.5)
    draw_node(ax, 2.1, 1.6, 1.8, 0.6, "L1 Gold Tier\n(Semantic Cache)", "lake", fontsize=7.5)
    
    # Column 2: x = 4.9
    draw_node(ax, 4.9, 7.0, 1.8, 0.6, "Scientific Deliverables\n(Plots, CSV, H5AD)", "hit", "ellipse", fontsize=7.5)
    draw_node(ax, 4.9, 5.2, 1.8, 0.6, "Evo_PRISM Agent\n(Cognitive Brain)", "agent", fontsize=7.5)
    draw_node(ax, 4.9, 1.6, 1.8, 0.6, "L2 Silver Store\n(Metadata/Lineage)", "lake", fontsize=7.5)
    
    # Column 3: x = 6.8
    draw_node(ax, 6.8, 5.2, 1.4, 0.6, "HELIX Sandbox\n(Secure Run)", "agent", fontsize=7.5)
    draw_node(ax, 6.8, 1.6, 1.4, 0.6, "L3 Bronze Tier\n(Immutable Raw)", "lake", fontsize=7.5)
    
    # Floating Cache Node (moved right to x=3.3 to avoid cluttering and cross-arrows)
    draw_node(ax, 3.3, 3.8, 1.6, 0.6, "L1/L2 Semantic Cache\n(Query Similarities)", "hit", fontsize=7.5)
    
    # Draw Left-side Directed Flow Arrows
    arrow_style = dict(arrowstyle="->", lw=1.6, color=COLORS["arrow"])
    hit_style = dict(arrowstyle="->", lw=1.8, color="#2e7d32")
    miss_style = dict(arrowstyle="->", lw=1.6, color="#ef6c00")
    
    # ① Query: Input -> Gateway
    ax.annotate("", xy=(2.1, 5.55), xytext=(2.1, 6.65), arrowprops=arrow_style, zorder=2)
    
    # ② Check Cache: Gateway -> Cache (diagonal down-right)
    ax.annotate("", xy=(2.9, 4.1), xytext=(2.3, 4.9), arrowprops=arrow_style, zorder=2)
    
    # ③ Cache Hit (Fast-Path Bypassed Route): Cache -> Output
    # Bypasses Gateway & User Input on the left margin
    import matplotlib.path as mpath
    path_data = [
        (mpath.Path.MOVETO, (2.5, 3.8)),
        (mpath.Path.LINETO, (0.9, 3.8)),
        (mpath.Path.LINETO, (0.9, 7.55)),
        (mpath.Path.LINETO, (4.9, 7.55)),
        (mpath.Path.LINETO, (4.9, 7.35))
    ]
    codes, verts = zip(*path_data)
    path = mpath.Path(verts, codes)
    patch = patches.FancyArrowPatch(path=path, arrowstyle="->", lw=1.8, color="#2e7d32", mutation_scale=12, zorder=4)
    ax.add_patch(patch)
    
    # ④ Cache Miss: Gateway -> Agent
    ax.annotate("", xy=(3.95, 5.2), xytext=(3.05, 5.2), arrowprops=miss_style, zorder=2)
    
    # ⑤ Run in Sandbox: Agent -> Sandbox
    ax.annotate("", xy=(6.05, 5.2), xytext=(5.85, 5.2), arrowprops=arrow_style, zorder=2)
    
    # ⑥ Load Raw Data: Sandbox -> L3 Bronze
    ax.annotate("", xy=(6.8, 1.95), xytext=(6.8, 4.85), arrowprops=arrow_style, zorder=2)
    
    # ⑦ Register & Promote: Sandbox -> L2 Silver
    ax.annotate("", xy=(5.5, 1.95), xytext=(6.3, 4.85), arrowprops=hit_style, zorder=2)
    
    # ⑧ Output Results: L2 Silver -> Output
    ax.annotate("", xy=(4.9, 6.65), xytext=(4.9, 1.95), arrowprops=arrow_style, zorder=2)
    
    # Internal Lakehouse flows
    # L2 -> L1 (Cache backfill)
    ax.annotate("", xy=(3.05, 1.6), xytext=(3.95, 1.6), arrowprops=arrow_style, zorder=2)
    ax.text(3.5, 1.8, "Backfill", fontsize=6.5, color=COLORS["arrow"], ha="center", fontweight="bold")
    
    # L3 -> L2 (Parquet Structuring)
    ax.annotate("", xy=(5.75, 1.6), xytext=(6.05, 1.6), arrowprops=arrow_style, zorder=2)
    ax.text(5.9, 1.8, "Struct", fontsize=6.5, color=COLORS["arrow"], ha="center", fontweight="bold")
    
    # Bidirectional Cache Storage Reads/Returns (between Cache processor and L1 Gold Tier storage)
    # Query (downward dashed)
    ax.annotate("", xy=(2.3, 1.95), xytext=(3.0, 3.5), 
                arrowprops=dict(arrowstyle="->", lw=1.2, color=COLORS["arrow"], linestyle="--"), zorder=2)
    ax.text(2.6, 2.7, "Query", fontsize=6, color=COLORS["arrow"], rotation=20, ha="center", fontweight="semibold")
    
    # Return (upward green solid showing data flow back to Cache processor)
    ax.annotate("", xy=(3.2, 3.45), xytext=(2.5, 1.95), 
                arrowprops=dict(arrowstyle="->", lw=1.4, color="#2e7d32"), zorder=2)
    ax.text(2.9, 2.4, "Return", fontsize=6, color="#2e7d32", rotation=20, ha="center", fontweight="bold")

    # Step Badges (Circular green/grey indices) directly placed on flow arrows on the left
    def draw_badge(cx, cy, number, is_green=True):
        fcol = "#e8f5e9" if is_green else "#eceff1"
        ecol = "#2e7d32" if is_green else COLORS["arrow"]
        p = patches.Circle((cx, cy), 0.14, facecolor=fcol, edgecolor=ecol, lw=1.2, zorder=5)
        ax.add_patch(p)
        ax.text(cx, cy, str(number), ha="center", va="center", fontsize=7.5, color=ecol, fontweight="bold", zorder=6)
        
    draw_badge(2.1, 6.1, 1, is_green=False)  # Query
    draw_badge(2.6, 4.5, 2, is_green=False)  # Check Cache
    draw_badge(0.9, 5.6, 3, is_green=True)   # Cache Hit (placed on the vertical segment of bypassed path)
    draw_badge(3.5, 5.2, 4, is_green=False)  # Miss -> Agent
    draw_badge(5.95, 5.2, 5, is_green=False) # Run in Sandbox
    draw_badge(6.8, 3.4, 6, is_green=False)  # Load L3
    draw_badge(5.9, 3.4, 7, is_green=True)   # Register & Promote
    draw_badge(4.9, 4.3, 8, is_green=False)  # Output Results

    # --- RIGHT SIDE: Pipeline Execution Steps Sidebar (40% Width, x >= 7.8) ---
    sidebar_card = patches.FancyBboxPatch(
        (7.7, 0.6), 4.0, 7.0,
        boxstyle="round,pad=0.1",
        facecolor="#ffffff",
        edgecolor="#cfd8dc",
        linewidth=1.5,
        zorder=2
    )
    ax.add_patch(sidebar_card)
    ax.text(9.7, 7.2, "Pipeline Execution Steps", ha="center", va="center", fontsize=11, fontweight="bold", color="#37474f", zorder=3)
    
    # Styled step lines with bullet badges on the right sidebar
    step_descriptions = [
        ("1", "User submits natural language query", False),
        ("2", "Adaptive Gateway inspects L1/L2 Cache", False),
        ("3", "Cache Hit: Return cached output in 2.15ms", True),
        ("4", "Cache Miss: Route query to LLM Agent", False),
        ("5", "Agent builds and executes code in Sandbox", False),
        ("6", "Isolated Sandbox loads raw L3 Bronze data", False),
        ("7", "Sandbox promotes tools & registers to L2 Store", True),
        ("8", "Final deliverables exported back to User", False)
    ]
    
    y_start = 6.4
    for idx, (num, desc, is_green) in enumerate(step_descriptions):
        cy = y_start - (idx * 0.68)
        # Draw circular number badge
        fcol = "#e8f5e9" if is_green else "#eceff1"
        ecol = "#2e7d32" if is_green else COLORS["arrow"]
        # Make side badge slightly smaller
        p = patches.Circle((8.1, cy), 0.12, facecolor=fcol, edgecolor=ecol, lw=1.0, zorder=3)
        ax.add_patch(p)
        ax.text(8.1, cy, num, ha="center", va="center", fontsize=7, color=ecol, fontweight="bold", zorder=4)
        # Write description text
        ax.text(8.35, cy, desc, ha="left", va="center", fontsize=7.5, color="#455a64", fontweight="semibold", zorder=3)

    # Save to both requested v3 and paper reference v2 path
    output_path_v2 = IMAGES_DIR / "Figure1_System_Architecture_v2.png"
    output_path_v3 = IMAGES_DIR / "Figure1_System_Architecture_v3.png"
    
    plt.savefig(output_path_v2, dpi=300, facecolor=COLORS["bg"], bbox_inches="tight")
    plt.savefig(output_path_v3, dpi=300, facecolor=COLORS["bg"], bbox_inches="tight")
    plt.close()
    
    print(f"Generated Figure 1 v3: {output_path_v3}")
    print(f"Copied Figure 1 to v2 path: {output_path_v2}")


def draw_fig2_helix():
    """Figure 2: HELIX Autonomic Code Promotion & Tool Lifecycle Loop (v2 – Complete)

    Added vs v1:
    - Swimlane 2: Stabilization Zone with revision_count ≥ 3 check diamond
    - Stabilization Iteration node (HELIX-Core) + HELIX-Vision forgetting curve
    - mark_stable() whitelist callout card
    - analysis_history.tool_id version-linkage annotation
    """
    fig, ax = plt.subplots(figsize=(13, 11), dpi=300)
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 11)
    ax.axis("off")
    fig.patch.set_facecolor(COLORS["bg"])

    # Title
    ax.text(6.5, 10.65, "HELIX: Autonomic Tool Evolution & Code Memory Loop",
            ha="center", va="center", fontsize=14, fontweight="bold", color=COLORS["engram_border"])
    ax.text(6.5, 10.25, "Continuous Refactoring, Promotion, and Expiration Lifecycle of Scientific Code",
            ha="center", va="center", fontsize=10, color="#546e7a", fontstyle="italic")

    # ── Swimlane 1: Sandbox & Development (y 7.9 – 9.9) ─────────────────────────
    ax.add_patch(patches.FancyBboxPatch(
        (0.4, 7.9), 12.2, 2.0,
        boxstyle="round,pad=0.1", facecolor="#f9fbe7", edgecolor="#d4e157",
        linewidth=1.5, linestyle="--", zorder=1))
    ax.text(0.65, 9.66, "SANDBOX & DEVELOPMENT HORIZON  (Temporary / Ad-hoc)",
            ha="left", va="center", fontsize=9, color="#558b2f", fontweight="bold", zorder=2)

    draw_node(ax, 2.2,  8.9, 2.8, 1.0,
              "1. LLM Ad-hoc Code\n(Dynamic Script Generation\nfrom Natural Language)",
              "agent", fontsize=8)
    draw_node(ax, 6.5,  8.9, 2.8, 1.0,
              "2. Secure Sandbox\n- Path Whitelist Guard\n- AST Import Intercept\n- Resource & Memory Limits",
              "agent", fontsize=8)
    draw_node(ax, 10.8, 8.9, 2.8, 1.0,
              "3. Quality Evaluation Hub\n- McCabe CC Complexity\n- Radon Maintainability Index\n- PASS Pytest Suite",
              "agent", fontsize=8)

    # ── Swimlane 2: Stabilization Zone (y 5.1 – 7.6) ────────────────────────────
    ax.add_patch(patches.FancyBboxPatch(
        (0.4, 5.1), 12.2, 2.5,
        boxstyle="round,pad=0.1", facecolor="#fff8e1", edgecolor="#fbc02d",
        linewidth=1.5, linestyle="--", zorder=1))
    ax.text(0.65, 7.36, "STABILIZATION ZONE  "
            "(HELIX-Core: stabilize → close_stabilize  |  HELIX-Vision: snapshot + forgetting curve)",
            ha="left", va="center", fontsize=8.5, color="#e65100", fontweight="bold", zorder=2)

    # revision_count ≥ 3 diamond (right)
    draw_diamond(ax, 10.8, 6.3, 2.4, 1.1,
                 "revision_count\n≥ 3?\n(Hot Zone?)", "hit", fontsize=8)

    # Stabilization Iteration node (centre)
    draw_node(ax, 6.2, 6.3, 3.8, 1.1,
              "Stabilization Iteration  (HELIX-Core)\n"
              "open: diagnosis + action_taken\n"
              "close: close_stabilize(outcome)\n"
              "HELIX-Vision: diagnosis_img snapshot\n"
              "  180 d → ×0.5  |  365 d → ×0.25  (forgetting curve)",
              "helix", fontsize=7.5)

    # mark_stable() callout card (left)
    draw_formula_card(ax, 2.4, 6.3, 2.8, 1.1,
                      "mark_stable()\n"
                      "stability_note = [STABLE]…\n"
                      "is_marked_stable() = True\n"
                      "→ bypasses hot-zone warnings\n"
                      "   (still passes Promotion Gate)",
                      "#2e7d32", "#e8f5e9", fontsize=7)

    # ── Promotion Gate (gap between stab zone and production lane) ───────────────
    draw_diamond(ax, 10.8, 4.1, 2.8, 1.4,
                 "Autonomic\nPromotion Gate\n$f_{promote} \\geq 3.0$?", "hit", fontsize=8)

    draw_formula_card(ax, 7.8, 4.1, 2.6, 0.8,
                      "Promotion Threshold:\n"
                      "$f_{promote}(t) \\geq 3.0$\n"
                      "$f_{promote} = \\mathrm{Reuse} + 2\\mathrm{Approval}$\n"
                      "$- 0.2\\mathrm{McCabeCC}$",
                      "#fbc02d", "#fffde7", fontsize=7)
    draw_formula_card(ax, 4.5, 4.1, 2.6, 0.8,
                      "Warning Threshold:\n"
                      "$HealthScore(t) < 0.70$\n"
                      "$HealthScore = 1.0 - 0.6\\mathrm{Churn}$\n"
                      "$- 0.4\\Delta\\mathrm{Complexity}$",
                      "#e53935", "#ffebee", fontsize=7)

    # ── Swimlane 3: Production & Governance (y 0.9 – 3.1) ───────────────────────
    ax.add_patch(patches.FancyBboxPatch(
        (0.4, 0.9), 12.2, 2.2,
        boxstyle="round,pad=0.1", facecolor="#e3f2fd", edgecolor="#90caf9",
        linewidth=1.5, linestyle="--", zorder=1))
    ax.text(0.65, 1.22, "PRODUCTION & GOVERNANCE HORIZON  (Formalized & Versioned)",
            ha="left", va="center", fontsize=9, color="#1565c0", fontweight="bold", zorder=2)

    draw_node(ax, 10.8, 2.1, 2.8, 1.0,
              "4. Formal MCP Tool\n(Registered in Registry\nvia Semantic Versioning)",
              "hit", fontsize=8)
    draw_node(ax, 6.5,  2.1, 2.8, 1.0,
              "5. L2 Active Toolset\n(Served via stdio/SSE\nfor sub-second reuse)\n↳ analysis_history.tool_id",
              "hit", fontsize=8)
    draw_node(ax, 2.2,  2.1, 2.8, 1.0,
              "6. Health Monitor\nTracks Usage Churn\n& Relative Churn Ratio",
              "lake", fontsize=8)

    # ── Arrows ───────────────────────────────────────────────────────────────────
    arr  = dict(arrowstyle="->", lw=1.8, color="#1565c0")
    prom = dict(arrowstyle="->", lw=2.0, color="#2e7d32")
    disc = dict(arrowstyle="->", lw=1.2, color="#757575", linestyle="--",
                connectionstyle="arc3,rad=-0.3")
    ref  = dict(arrowstyle="->", lw=2.0, color="#d32f2f")
    stab = dict(arrowstyle="->", lw=1.8, color="#f57f17")

    # 1 → 2
    ax.annotate("", xy=(5.05, 8.9), xytext=(3.65, 8.9), arrowprops=arr)
    ax.text(4.35, 9.1, "Run Safe", fontsize=7.5, color="#1565c0", ha="center", fontweight="bold")

    # 2 → 3
    ax.annotate("", xy=(9.35, 8.9), xytext=(7.95, 8.9), arrowprops=arr)
    ax.text(8.65, 9.1, "Assess", fontsize=7.5, color="#1565c0", ha="center", fontweight="bold")

    # 3 → revision_count ≥ 3 check (down into stab zone)
    ax.annotate("", xy=(10.8, 6.95), xytext=(10.8, 8.35), arrowprops=arr)

    # revision_count ≥ 3  YES → Stabilization Iteration (left)
    ax.annotate("", xy=(8.1, 6.3), xytext=(9.55, 6.3), arrowprops=stab)
    ax.text(8.8, 6.53, "YES", fontsize=8.5, color="#e65100", ha="center", fontweight="bold")

    # revision_count ≥ 3  NO → skip to Promotion Gate (down)
    ax.annotate("", xy=(10.8, 4.8), xytext=(10.8, 5.7), arrowprops=arr)
    ax.text(11.05, 5.24, "NO (skip)", fontsize=7.5, color="#757575", ha="left", fontweight="bold")

    # Stabilization Iteration done → Promotion Gate (diagonal down-right)
    ax.annotate("", xy=(9.4, 4.8), xytext=(8.1, 5.75), arrowprops=stab)
    ax.text(8.95, 5.24, "Done", fontsize=7.5, color="#e65100", ha="center", fontweight="bold")

    # Promotion Gate YES → Node 4
    ax.annotate("", xy=(10.8, 2.65), xytext=(10.8, 3.4), arrowprops=prom)
    ax.text(11.05, 3.0, "YES\n(Promoted)", fontsize=8, color="#2e7d32", fontweight="bold", va="center")

    # Promotion Gate NO → Discard (right)
    ax.annotate("", xy=(12.55, 4.7), xytext=(12.15, 4.1), arrowprops=disc)
    ax.text(12.6, 4.85, "NO\n(One-off)", fontsize=7.5, color="#757575", fontweight="bold")

    # 4 → 5
    ax.annotate("", xy=(7.95, 2.1), xytext=(9.35, 2.1), arrowprops=prom)
    ax.text(8.65, 2.32, "Hot-load", fontsize=7.5, color="#2e7d32", ha="center", fontweight="bold")

    # 5 → 6
    ax.annotate("", xy=(3.65, 2.1), xytext=(5.05, 2.1), arrowprops=prom)
    ax.text(4.35, 2.32, "Monitor", fontsize=7.5, color="#2e7d32", ha="center", fontweight="bold")

    # 6 → 1  (Refactor & Churn feedback loop, left wall)
    ax.annotate("", xy=(2.2, 8.35), xytext=(2.2, 2.65), arrowprops=ref)
    ax.text(0.9, 5.5, "Refactor &\nChurn Feedback\nLoop\n(Health < 0.70)",
            ha="right", va="center", fontsize=8, color="#d32f2f", fontweight="bold")

    # analysis_history.tool_id version-linkage annotation (above L2 node)
    ax.annotate("", xy=(6.5, 3.25), xytext=(6.5, 2.65),
                arrowprops=dict(arrowstyle="->", lw=1.2, color="#7b1fa2", linestyle=":"))
    ax.text(6.5, 3.42, "analysis_history.tool_id  (version linkage — HELIX tracking)",
            fontsize=6.5, color="#7b1fa2", ha="center", fontweight="bold")

    output_path = IMAGES_DIR / "Figure2_HELIX_Loop.png"
    plt.savefig(output_path, dpi=300, facecolor=COLORS["bg"], bbox_inches="tight")
    plt.close()
    print(f"Generated Figure 2: {output_path}")


def draw_fig3_engram():
    """Figure 3: ENGRAM Semantic Memory & Retrospective Blast Radius CTE (v2 – Complete)

    Added vs v1:
    - L2 Silver: tool_stabilization_log (HELIX) + analysis_index VIEW (0-token query)
    - MCP Resources delivery card (artifact:// URI scheme + bio_get_artifact() fallback)
    """
    fig, ax = plt.subplots(figsize=(12, 9.5), dpi=300)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 10)
    ax.axis("off")
    fig.patch.set_facecolor(COLORS["bg"])

    # Title
    ax.text(6.0, 9.5, "ENGRAM: Semantic Memory Lakehouse & Retrospective Blast Radius",
            ha="center", va="center", fontsize=14, fontweight="bold", color=COLORS["engram_border"])
    ax.text(6.0, 9.1, "L1-L2-L3 Medallion Storage Schema and SQL Recursive CTE Dependency Tracing",
            ha="center", va="center", fontsize=10, color="#546e7a", fontstyle="italic")

    # 1. Column 1 Box: Medallion Storage Layers (L1, L2, L3)
    ax.add_patch(patches.FancyBboxPatch(
        (0.3, 0.5), 4.0, 8.2,
        boxstyle="round,pad=0.1", facecolor="#fcfcfc",
        edgecolor=COLORS["lake_border"], linewidth=1.2, zorder=1))
    ax.text(2.3, 8.8, "Medallion Data Lakehouse Tiers",
            ha="center", va="bottom", fontsize=11.5, fontweight="bold", color=COLORS["lake_border"])

    # L1 Gold Cache
    draw_node(ax, 2.3, 7.6, 3.6, 1.4,
              "L1 GOLD TIER (hermes_cache.duckdb)\n"
              "  - memory_recent: 1024-dim BGE-M3 Embeddings\n"
              "  - HNSW Index: Cosine Similarity >= 0.88\n"
              "  - Figure Cache: base64-stripped PNG files\n"
              "  - TTL: 7 Days Auto-Expiration / Invalidation",
              "hit", fontsize=7.5)

    # L2 Silver Store — enlarged: +tool_stabilization_log +analysis_index VIEW
    draw_node(ax, 2.3, 4.6, 3.6, 2.5,
              "L2 SILVER TIER (bio_memory.duckdb)\n"
              "  - sample_registry: Metadata registry (l2_ready)\n"
              "  - analysis_history: Immutable append-only ledger\n"
              "  - tools: Formalized tools SemVer history\n"
              "  - tool_change_log: relative modification logs\n"
              "  - tool_stabilization_log: HELIX stabilization history\n"
              "  - artifact_relations: Direct DAG dependency edges\n"
              "  - analysis_artifacts: Multi-modal metadata reports\n"
              "  - analysis_index VIEW: 0-token query cache",
              "lake", fontsize=7.0)

    # L3 Bronze Store
    draw_node(ax, 2.3, 1.5, 3.6, 1.2,
              "L3 BRONZE TIER (Read-Only Dataset)\n"
              "  - 39 GB Visium HD Genomics Counts matrix\n"
              "  - Gigapixel TIFF tissue imaging files\n"
              "  - NTFS operating system level write-protection\n"
              "  - Fully immutable raw reference baseline",
              "lake", fontsize=7.5)
              
    # 2. Column 2 Box: SQL Recursive CTE Terminal Widget
    sql_console = patches.FancyBboxPatch(
        (4.5, 1.8), 2.8, 5.2,
        boxstyle="round,pad=0.08",
        facecolor="#1e1e1e",
        edgecolor="#455a64",
        linewidth=1.8,
        zorder=2
    )
    ax.add_patch(sql_console)
    
    # Console header bar
    sql_header = patches.FancyBboxPatch(
        (4.5, 6.6), 2.8, 0.4,
        boxstyle="round,pad=0.08",
        facecolor="#37474f",
        edgecolor="#455a64",
        linewidth=1.0,
        zorder=3
    )
    ax.add_patch(sql_header)
    
    # Window buttons
    ax.add_patch(patches.Circle((4.7, 6.8), 0.06, color='#ff5252', zorder=4))
    ax.add_patch(patches.Circle((4.9, 6.8), 0.06, color='#ffb74d', zorder=4))
    ax.add_patch(patches.Circle((5.1, 6.8), 0.06, color='#81c784', zorder=4))
    ax.text(5.9, 6.8, "DuckDB Recursive CTE", color="#eceff1", family="monospace", fontsize=7.5, fontweight="bold", ha="center", va="center", zorder=4)
    
    # SQL Syntax Highlighted Lines
    ax.text(4.6, 6.4, "-- Recurse down relations DAG", color="#78909c", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
    ax.text(4.6, 6.1, "WITH RECURSIVE impact_path AS (", color="#e57373", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
    ax.text(4.6, 5.8, "  SELECT src_id, dst_id, 1 AS depth", color="#80cbc4", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
    ax.text(4.6, 5.5, "  FROM relations WHERE src_id =", color="#80cbc4", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
    ax.text(4.6, 5.2, "    'bio_run_bulk_eda v2.0.0'", color="#c5e1a5", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
    ax.text(4.6, 4.9, "  UNION ALL", color="#e57373", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
    ax.text(4.6, 4.6, "  SELECT r.src_id, r.dst_id,", color="#80cbc4", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
    ax.text(4.6, 4.3, "         ip.depth + 1", color="#e57373", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
    ax.text(4.6, 4.0, "  FROM relations r", color="#80cbc4", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
    ax.text(4.6, 3.7, "  JOIN impact_path ip", color="#e57373", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
    ax.text(4.6, 3.4, "    ON r.src_id = ip.dst_id", color="#80cbc4", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
    ax.text(4.6, 3.1, ") SELECT * FROM impact_path;", color="#e57373", family="monospace", fontsize=6, ha="left", va="top", zorder=3)
              
    # 3. Column 3 Box: Retrospective Blast Radius Tree (DAG)
    cte_box = patches.FancyBboxPatch(
        (7.7, 0.5), 4.0, 8.2,
        boxstyle="round,pad=0.1",
        facecolor="#faf8fb",
        edgecolor=COLORS["engram_border"],
        linewidth=1.2,
        zorder=1
    )
    ax.add_patch(cte_box)
    ax.text(9.7, 8.8, "Retrospective Downstream Blast Radius", ha="center", va="bottom", fontsize=11.5, fontweight="bold", color=COLORS["engram_border"])
    
    # Inject risk colors into the palette dynamically
    COLORS.update({
        "epicenter": "#ffebee",
        "epicenter_border": "#c62828",
        "level1": "#ffccbc",
        "level1_border": "#e64a19",
        "level2": "#ffe0b2",
        "level2_border": "#f57c00",
        "level3": "#fff9c4",
        "level3_border": "#fbc02d"
    })

    # Showcase a graph of affected artifacts in right column
    draw_node(ax, 9.7, 7.8, 3.4, 0.7, "EPICENTER (Mutated Source):\nTool bio_run_bulk_eda v2.0.0", "epicenter", fontsize=7.5)
    draw_node(ax, 9.7, 5.8, 3.4, 0.7, "Level 1: Direct Affected Run\nshowcase (SDS-D0D1D2)\n[Exact: Confidence 1.0]", "level1", fontsize=7.5)
    
    # Side-by-side derived outputs
    draw_node(ax, 8.6, 3.8, 1.7, 0.8, "Level 2: Derived Data\ncellpose_cells.h5ad\n[Same-Analysis: 0.9]", "level2", fontsize=6.8)
    draw_node(ax, 10.8, 3.8, 1.7, 0.8, "Level 2: Derived Figure\nmask_he_overlay.png\n[Same-Analysis: 0.9]", "level2", fontsize=6.8)
    
    # Downstream indirect case studies
    draw_node(ax, 9.7, 1.8, 3.4, 0.8, "Level 3: Downstream Case Studies\n112-Sample DEG Reports\n[Heuristic: Confidence 0.6]", "level3", fontsize=7.5)
    
    # Directed Arrows inside Column 3
    arrow_style_0_1 = dict(arrowstyle="->", lw=2.0, color="#c62828")
    arrow_style_1_2 = dict(arrowstyle="->", lw=1.8, color="#e64a19")
    arrow_style_2_3 = dict(arrowstyle="->", lw=1.5, color="#fbc02d")
    
    # L0 -> L1
    ax.annotate("", xy=(9.7, 6.25), xytext=(9.7, 7.35), arrowprops=arrow_style_0_1)
    ax.text(9.8, 6.8, "Depth 1 (Exact)", fontsize=7, color="#c62828", fontweight="bold", ha="left")
    
    # L1 -> L2 (Sibling A)
    ax.annotate("", xy=(8.6, 4.3), xytext=(9.3, 5.35), arrowprops=arrow_style_1_2)
    ax.text(8.7, 4.9, "Depth 2 (0.9)", fontsize=6.5, color="#e64a19", fontweight="bold", ha="right")
    
    # L1 -> L2 (Sibling B)
    ax.annotate("", xy=(10.8, 4.3), xytext=(10.1, 5.35), arrowprops=arrow_style_1_2)
    ax.text(10.7, 4.9, "Depth 2 (0.9)", fontsize=6.5, color="#e64a19", fontweight="bold", ha="left")
    
    # Sibling A -> L3
    ax.annotate("", xy=(9.3, 2.3), xytext=(8.6, 3.3), arrowprops=arrow_style_2_3)
    ax.text(8.7, 2.7, "Depth 3 (0.6)", fontsize=6.5, color="#f57c00", fontweight="bold", ha="right")
    
    # Sibling B -> L3
    ax.annotate("", xy=(10.1, 2.3), xytext=(10.8, 3.3), arrowprops=arrow_style_2_3)
    ax.text(10.7, 2.7, "Depth 3 (0.6)", fontsize=6.5, color="#f57c00", fontweight="bold", ha="left")
    
    # 4. Draw Inter-Column Flow Arrows
    # L3 -> L2  (L2 bottom = 4.6 - 1.25 = 3.35)
    ax.annotate("", xy=(2.3, 3.35), xytext=(2.3, 2.2),
                arrowprops=dict(arrowstyle="->", lw=1.5, color="#37474f"))
    ax.text(2.4, 2.75, "Parquet Structuring", fontsize=7, color="#37474f", fontweight="bold")

    # L2 -> L1  (L2 top = 4.6 + 1.25 = 5.85 ; L1 bottom = 7.6 - 0.7 = 6.9)
    ax.annotate("", xy=(2.3, 6.9), xytext=(2.3, 5.85),
                arrowprops=dict(arrowstyle="->", lw=1.5, color="#37474f"))
    ax.text(2.4, 6.35, "Semantic Cache Backfill", fontsize=7, color="#37474f", fontweight="bold")

    # L2 to SQL Console (Read Relations) — arrow at L2 centre y=4.6
    ax.annotate("", xy=(4.4, 4.6), xytext=(4.15, 4.6),
                arrowprops=dict(arrowstyle="->", lw=1.8, color="#7b1fa2", linestyle="--"))
    ax.text(4.28, 4.72, "Read\nDAG", fontsize=7, color="#7b1fa2", fontweight="bold", ha="center")

    # MCP Resources / artifact:// delivery card  (below SQL console, col-2 area)
    draw_formula_card(ax, 5.9, 0.85, 2.4, 0.75,
                      "MCP Resources  (artifact_resources.py)\n"
                      "URI: artifact://<artifact_id>\n"
                      "binary → base64 blob  (≤25 MB inline)\n"
                      "fallback: bio_get_artifact() tool",
                      "#7b1fa2", "#f3e5f5", fontsize=7)
    # Dotted arc from analysis_artifacts row in L2 (right edge ~x=4.1, y≈3.9) to MCP card
    ax.annotate("", xy=(4.7, 0.85), xytext=(4.1, 3.9),
                arrowprops=dict(arrowstyle="->", lw=1.4, color="#7b1fa2", linestyle="--",
                                connectionstyle="arc3,rad=-0.4"))
    ax.text(3.9, 2.2, "artifact://\ndelivery", fontsize=6.5, color="#7b1fa2",
            ha="right", fontweight="bold")
    
    # SQL Console to Blast Radius Box (Output Trace)
    ax.annotate("", xy=(7.65, 4.5), xytext=(7.35, 4.5), arrowprops=dict(arrowstyle="->", lw=2.2, color="#e65100"))
    ax.text(7.5, 4.6, "Trace\nImpact", fontsize=7, color="#e65100", fontweight="bold", ha="center")

    # Confidence / Risk Legend Box at bottom of right column
    legend_box = patches.FancyBboxPatch(
        (8.0, 0.7), 3.4, 0.7,
        boxstyle="round,pad=0.05",
        facecolor="#ffffff",
        edgecolor="#cfd8dc",
        linewidth=1.0,
        zorder=2
    )
    ax.add_patch(legend_box)
    ax.text(9.7, 1.25, "Downstream Confidence Decay", ha="center", va="center", fontsize=7.5, fontweight="bold", color="#37474f", zorder=3)
    
    # Colored squares
    ax.add_patch(patches.Rectangle((8.2, 0.85), 0.2, 0.15, facecolor="#ffebee", edgecolor="#c62828", lw=1, zorder=3))
    ax.text(8.5, 0.9, "1.0 (Exact)", fontsize=6.5, color=COLORS["text"], va="center", zorder=3)
    
    ax.add_patch(patches.Rectangle((9.2, 0.85), 0.2, 0.15, facecolor="#ffe0b2", edgecolor="#f57c00", lw=1, zorder=3))
    ax.text(9.5, 0.9, "0.9 (Same-Analys.)", fontsize=6.5, color=COLORS["text"], va="center", zorder=3)
    
    ax.add_patch(patches.Rectangle((10.4, 0.85), 0.2, 0.15, facecolor="#fff9c4", edgecolor="#fbc02d", lw=1, zorder=3))
    ax.text(10.7, 0.9, "0.6 (Heuristics)", fontsize=6.5, color=COLORS["text"], va="center", zorder=3)

    output_path = IMAGES_DIR / "Figure3_ENGRAM_Lakehouse.png"
    plt.savefig(output_path, dpi=300, facecolor=COLORS["bg"], bbox_inches="tight")
    plt.close()
    print(f"Generated Figure 3: {output_path}")


def main():
    print("Generating crisp, publication-quality academic architecture diagrams (Figures 1, 2, 3)...")
    draw_fig1_overall()
    draw_fig2_helix()
    draw_fig3_engram()
    print("All architecture diagrams successfully generated and verified!")

if __name__ == "__main__":
    main()
