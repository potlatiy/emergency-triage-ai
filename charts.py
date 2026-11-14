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


ORANGE, AQUA = "#eb6834", "#1baf7a"       # with BLUE: three colours checked for colour-blind readers


def chain_time_chart(average, rows):
    """The whole-chain simulation (chain_time.py) in one picture.

    Top: where the minutes go from the door to the doctor, before and after, with our assumed timings.
    Bottom: the same total for everyone under each changed assumption, so the reader sees how much the
    answer depends on what we assumed."""
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(8.8, 8.2), facecolor=SURFACE, dpi=150,
                                      gridspec_kw={"height_ratios": [1, 1.9], "hspace": 0.5})

    # ---- top: three parts of the wait, stacked
    bars = [("Everyone\nbefore", average["Before"]["everyone"]), ("Everyone\nafter", average["After"]["everyone"]),
            ("Most urgent\nbefore", average["Before"]["urgent"]), ("Most urgent\nafter", average["After"]["urgent"])]
    parts = [("Waiting for the nurse", BLUE), ("With the nurse", ORANGE), ("Waiting for a doctor", AQUA)]
    longest = max(values[3] for _, values in bars)
    for row, (name, values) in enumerate(bars):
        left = 0.0
        for (label, colour), minutes in zip(parts, values[:3]):
            top.barh(row, minutes, left=left, color=colour, height=0.56, edgecolor=SURFACE, linewidth=2,
                     label=label if row == 0 else None)
            if minutes > longest * 0.07:                     # a number only where there is room for it
                top.text(left + minutes / 2, row, f"{minutes:.0f}", ha="center", va="center", fontsize=9.5,
                         color="white", fontweight="bold")
            left += minutes
        top.text(left + longest * 0.012, row, f"{values[3]:.0f} min", va="center", fontsize=10.5, color=INK, fontweight="bold")
    top.set_yticks(range(len(bars)), [name for name, _ in bars], fontsize=9.5, color=INK)
    top.invert_yaxis()
    top.set_xlim(0, longest * 1.13)
    top.set_xlabel("Average minutes from the door to the doctor", color=SOFT, fontsize=9.5)
    top.set_title("A simulation with assumed timings: where the minutes go, before and after the chain",
                  loc="left", fontsize=11.5, color=INK, pad=30)
    top.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=9.5, labelcolor=INK,
               handlelength=1.1, columnspacing=1.6, borderaxespad=0.2)
    top.grid(axis="x", color="#e4e3df", linewidth=0.8)
    tidy(top)

    # ---- bottom: one row per changed assumption, before -> after for everyone
    names = [name for name, _, _ in rows]
    before = [a["Before"]["everyone"][3] for _, a, _ in rows]
    after = [a["After"]["everyone"][3] for _, a, _ in rows]
    widest = max(before + after)
    for row, (b, a) in enumerate(zip(before, after)):
        bottom.plot([b, a], [row, row], color=GREY, linewidth=2, zorder=1)
        bottom.scatter([b], [row], s=64, color=SURFACE, edgecolor="#8a897f", linewidth=2, zorder=2,
                       label="Before" if row == 0 else None)
        bottom.scatter([a], [row], s=64, color=BLUE, edgecolor=SURFACE, linewidth=2, zorder=3,
                       label="After" if row == 0 else None)
        change = a - b
        bottom.text(max(a, b) + widest * 0.02, row, f"{a:.0f} min  ({change:+.0f})", va="center", fontsize=9.5, color=INK)
    wrapped = [name.replace(" (", "\n(").replace(": ", ":\n") for name in names]
    bottom.set_yticks(range(len(names)), wrapped, fontsize=9, color=INK)
    bottom.invert_yaxis()
    bottom.set_xlim(0, widest * 1.24)
    bottom.set_xlabel("Average minutes from the door to the doctor, everyone", color=SOFT, fontsize=9.5)
    bottom.set_title("How much the answer depends on each assumption", loc="left", fontsize=11.5, color=INK, pad=30)
    bottom.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, frameon=False, fontsize=9.5, labelcolor=INK,
                  handletextpad=0.3, columnspacing=1.6, borderaxespad=0.2)
    bottom.grid(axis="x", color="#e4e3df", linewidth=0.8)
    tidy(bottom)
    return fig
