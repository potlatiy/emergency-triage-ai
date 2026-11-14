"""The two charts that sum up the project. Run:  python charts.py   (saves them to images/)"""

from pathlib import Path

import matplotlib.pyplot as plt

BLUE, GREY, INK, SOFT, SURFACE = "#2a78d6", "#c3c2b7", "#222222", "#555555", "#fcfcfb"


def tidy(ax):
    ax.set_facecolor(SURFACE)
    for side in ["top", "right", "left"]:
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#bbbbbb")
    ax.tick_params(length=0, colors=SOFT)
    ax.set_axisbelow(True)


def wait_chart(rows):
    """rows: {queue order: (minutes urgent patients wait, minutes everyone waits)}"""
    names = [n.replace(" (", "\n(") for n in rows]
    minutes = [v[0] for v in rows.values()]
    fig, ax = plt.subplots(figsize=(8.6, 4.2), facecolor=SURFACE, dpi=150)
    bars = ax.bar(names, minutes, width=0.55, color=BLUE)
    ax.bar_label(bars, fmt="%.0f min", padding=3, fontsize=11, color=INK)
    ax.set_ylim(0, max(minutes) * 1.18)
    ax.set_ylabel("Minutes until first seen", color=SOFT)
    ax.set_title("Simulated wait for the most urgent patients (levels 1-2), by how the queue is ordered",
                 loc="left", fontsize=11.5, color=INK, pad=12)
    ax.grid(axis="y", color="#e4e3df", linewidth=0.8)
    tidy(ax)
    fig.tight_layout()
    return fig


def second_check_chart(caught, missed):
    """Of the patients nurses rated too low, how many the AI flags."""
    total = caught + missed
    fig, ax = plt.subplots(figsize=(8.6, 2.3), facecolor=SURFACE, dpi=150)
    ax.barh([0], [caught], color=BLUE, height=0.5)
    ax.barh([0], [missed], left=[caught + total * 0.004], color=GREY, height=0.5)
    ax.text(caught / 2, 0, f"{caught} flagged by the AI ({caught / total:.0%})", ha="center", va="center",
            color="white", fontsize=11.5, fontweight="bold")
    ax.text(caught + missed / 2, 0, f"{missed} not\nflagged", ha="center", va="center", color=INK, fontsize=10)
    ax.set_xlim(0, total * 1.01)
    ax.set_ylim(-0.6, 0.6)
    ax.set_yticks([])
    ax.set_xticks([])
    ax.set_title(f"The {total} patients that nurses rated less urgent than the experts did",
                 loc="left", fontsize=11.5, color=INK, pad=10)
    tidy(ax)
    ax.spines["bottom"].set_visible(False)
    fig.tight_layout()
    return fig


if __name__ == "__main__":
    from triage import compare_queues, fair_probabilities, load, safety_first

    df = load()
    safe = safety_first(fair_probabilities(df))
    nurse, truth = df["KTAS_RN"].values, df["KTAS_expert"].values
    under = nurse > truth
    flagged = safe < nurse
    out = Path(__file__).parent / "images"
    out.mkdir(exist_ok=True)
    wait_chart(compare_queues(df, safe)).savefig(out / "waiting.png", facecolor=SURFACE)
    second_check_chart(int((flagged & under).sum()), int((~flagged & under).sum())).savefig(
        out / "second_check.png", facecolor=SURFACE)
    print("saved charts to", out)
