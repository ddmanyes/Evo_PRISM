# -*- coding: utf-8 -*-
import os
import matplotlib.pyplot as plt
import numpy as np

# Set style for academic publication
plt.rcParams['font.sans-serif'] = ['Arial', 'Helvetica', 'DejaVu Sans']
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['savefig.dpi'] = 300

# Create figures directory if it doesn't exist
figures_dir = r"i:\Evo_PRISM\docs\figures"
os.makedirs(figures_dir, exist_ok=True)

# -------------------------------------------------------------
# Plot 1: McCabe CC and Radon MI Before-After (N=5)
# -------------------------------------------------------------
tools = ['bio_run_deg', 'bio_run_bulk_eda', 'bio_run_heatmaps', 'bio_run_enrichment', 'bio_run_pathway_scoring']
cc_before = [12, 15, 8, 18, 10]
cc_after = [2, 3, 1, 4, 2]

mi_before = [45.2, 40.5, 52.0, 35.1, 48.7]
mi_after = [82.1, 78.4, 89.2, 74.8, 81.3]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
x = np.arange(len(tools))
width = 0.35

# Color scheme (premium slate blue and forest green)
color_before = '#708090'  # Slate Grey (Technical Debt / Raw)
color_after = '#2e7d32'   # Forest Green (Clean / Promoted)

# CC Subplot
rects1 = ax1.bar(x - width/2, cc_before, width, label='Before (Ad-hoc Baseline)', color=color_before, edgecolor='black', linewidth=0.7)
rects2 = ax1.bar(x + width/2, cc_after, width, label='After (HELIX Promoted)', color=color_after, edgecolor='black', linewidth=0.7)
ax1.set_ylabel('McCabe Cyclomatic Complexity (Lower is Better)', fontsize=10, fontweight='bold')
ax1.set_title('A. McCabe Cyclomatic Complexity', fontsize=12, fontweight='bold', pad=10)
ax1.set_xticks(x)
ax1.set_xticklabels(tools, rotation=20, ha='right')
ax1.grid(axis='y', linestyle='--', alpha=0.5)
ax1.legend(frameon=True, facecolor='white', edgecolor='none')

# Add values on top of bars
def autolabel(rects, ax):
    for rect in rects:
        height = rect.get_height()
        ax.annotate(f'{height}',
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 3),  # 3 points vertical offset
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=9)

autolabel(rects1, ax1)
autolabel(rects2, ax1)

# MI Subplot
rects3 = ax2.bar(x - width/2, mi_before, width, label='Before (Ad-hoc Baseline)', color=color_before, edgecolor='black', linewidth=0.7)
rects4 = ax2.bar(x + width/2, mi_after, width, label='After (HELIX Promoted)', color=color_after, edgecolor='black', linewidth=0.7)
ax2.set_ylabel('Maintainability Index (Higher is Better)', fontsize=10, fontweight='bold')
ax2.set_title('B. Radon Maintainability Index (MI)', fontsize=12, fontweight='bold', pad=10)
ax2.set_xticks(x)
ax2.set_xticklabels(tools, rotation=20, ha='right')
ax2.grid(axis='y', linestyle='--', alpha=0.5)
ax2.legend(frameon=True, facecolor='white', edgecolor='none')

# Add values on top of bars
def autolabel_float(rects, ax):
    for rect in rects:
        height = rect.get_height()
        ax.annotate(f'{height:.1f}',
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=9)

autolabel_float(rects3, ax2)
autolabel_float(rects4, ax2)

plt.tight_layout()
plot1_path = os.path.join(figures_dir, "helix_before_after.png")
plt.savefig(plot1_path, dpi=300, bbox_inches='tight')
plt.close()
print(f"Generated Figure 1: {plot1_path}")

# -------------------------------------------------------------
# Plot 2: Longitudinal Tool Health Evolution
# -------------------------------------------------------------
commits = ['C1\n(05-16)', 'C2\n(05-17)', 'C3\n(05-17)', 'C4\n(05-18)', 'C5\n(05-19)', 
           'C6\n(05-20)', 'C7\n(05-21)', 'C8\n(05-22)', 'C9\n(05-22)', 'C10\n(05-23)']
health_score = [0.95, 0.92, 0.84, 0.76, 0.61, 0.52, 0.94, 0.93, 0.89, 0.95]

plt.figure(figsize=(10, 5))
plt.plot(commits, health_score, marker='o', color='#1a5f7a', linewidth=2.5, markersize=8, label='Tool HealthScore')

# Reference lines
plt.axhline(y=0.70, color='#d9534f', linestyle='--', alpha=0.8, linewidth=1.5, label='Warning Threshold (θ_warning = 0.70)')
plt.fill_between(range(len(commits)), 0, 0.70, color='#f2dede', alpha=0.3)  # Highlight warning zone

# Annotations without emojis for clean rendering
plt.annotate('Technical Debt Accumulates\n(CC & Churn increase)', xy=(3, 0.76), xytext=(1, 0.65),
             arrowprops=dict(facecolor='black', shrink=0.08, width=0.5, headwidth=4),
             fontsize=9, color='#a94442')

plt.annotate('Warning Zone Triggered\nCC=15, Churn=0.35', xy=(5, 0.52), xytext=(2.5, 0.40),
             arrowprops=dict(facecolor='red', shrink=0.08, width=0.5, headwidth=4),
             fontsize=9, color='#a94442', fontweight='bold')

plt.annotate('HELIX Code Promotion\nRewrite & Version Bump', xy=(6, 0.94), xytext=(4, 0.98),
             arrowprops=dict(facecolor='green', shrink=0.08, width=0.5, headwidth=4),
             fontsize=9, color='#3c763d', fontweight='bold')

plt.ylabel('HELIX HealthScore (Eq.2)', fontsize=11, fontweight='bold')
plt.xlabel('Repository Commits Timeline (2026-05-16 to 2026-05-23)', fontsize=11, fontweight='bold')
plt.title('Longitudinal Tool Health Evolution and Active Self-Healing Lifecycle', fontsize=13, fontweight='bold', pad=15)
plt.grid(True, linestyle=':', alpha=0.6)
plt.ylim(0.3, 1.05)
plt.legend(loc='lower left', frameon=True)

plt.tight_layout()
plot2_path = os.path.join(figures_dir, "helix_health_evolution.png")
plt.savefig(plot2_path, dpi=300, bbox_inches='tight')
plt.close()
print(f"Generated Figure 2: {plot2_path}")
