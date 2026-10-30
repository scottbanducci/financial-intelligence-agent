"""Accessible horizontal hover scrolling for long FRED series labels.

Native Streamlit selectbox options are virtualized; do not replace the selector
or bind source metadata to executable JavaScript. A small, static delegated
event handler targets Streamlit's option label viewport by aria-label and the
metadata table's explicitly named label cells. It scrolls only when needed
and respects reduced-motion preference. Publisher strings are HTML-escaped.
"""
from __future__ import annotations

from html import escape
from typing import Mapping, Sequence

import streamlit as st

_SCROLL_STYLE = r"""
<style>
.fred-metadata-wrap {
    max-height: 320px;
    overflow: auto;
    border: 1px solid rgba(127, 137, 151, .28);
    border-radius: 8px;
}
.fred-metadata-table {
    border-collapse: collapse;
    width: 100%;
    table-layout: fixed;
    color: inherit;
    font-size: .86rem;
}
.fred-metadata-table th,
.fred-metadata-table td {
    border-bottom: 1px solid rgba(127, 137, 151, .18);
    padding: 8px 10px;
    text-align: left;
    vertical-align: middle;
}
.fred-metadata-table th {
    position: sticky;
    top: 0;
    z-index: 1;
    background: var(--secondary-background-color, #1c2330);
    font-weight: 600;
}
.fred-metadata-table tr:last-child td { border-bottom: 0; }
.fred-metadata-table .fred-id-cell { width: 17%; overflow-wrap: anywhere; }
.fred-metadata-table .fred-name-cell { width: 53%; }
.fred-metadata-table .fred-frequency-cell { width: 15%; }
.fred-metadata-table .fred-unit-cell { width: 15%; overflow-wrap: anywhere; }
.fred-label-viewport {
    width: 100%;
    max-width: 100%;
    overflow-x: auto;
    overflow-y: hidden;
    white-space: nowrap;
    container-type: inline-size;
    scrollbar-width: none;
    -ms-overflow-style: none;
}
.fred-label-viewport::-webkit-scrollbar { display: none; }
.fred-label-text { display: inline-block; width: max-content; min-width: 100%; }
.fred-label-viewport:hover, .fred-label-viewport:focus-visible {
    outline: 1px solid var(--primary-color, #66c8d0);
    outline-offset: 2px;
}
/* Streamlit 1.59+ uses React Aria and renders the dropdown at document
   scope. The listbox aria-label identifies only our "Series" options.
   Keep overflow scrollable instead of truncating its inner text. */
[role="listbox"] [role="option"] [data-item-hl] {
    overflow-x: auto !important;
    overflow-y: hidden !important;
    scrollbar-width: none !important;
    -ms-overflow-style: none !important;
}
[role="listbox"] [role="option"] [data-item-hl]::-webkit-scrollbar {
    display: none;
}

/* All dataset inventory tables, including Workspace and Data Catalog. */
.dataset-table-wrap {
    max-height: 370px;
    overflow: auto;
    border: 1px solid rgba(127,137,151,.28);
    border-radius: 8px;
}
.dataset-table {
    border-collapse: collapse;
    width: 100%;
    table-layout: fixed;
    font-size: .84rem;
    color: inherit;
}
.dataset-table th, .dataset-table td {
    border-bottom: 1px solid rgba(127,137,151,.18);
    padding: 8px 10px;
    text-align: left;
    vertical-align: middle;
}
.dataset-table th {
    position: sticky;
    top: 0;
    z-index: 2;
    background: var(--secondary-background-color,#1c2330);
}
.dataset-label-viewport {
    display: block;
    width: 100%;
    overflow-x: auto;
    overflow-y: hidden;
    white-space: nowrap;
    scrollbar-width: none;
    -ms-overflow-style: none;
}
.dataset-label-viewport::-webkit-scrollbar { display:none; }
.dataset-label-text { display:inline-block; min-width:100%; width:max-content; }
.dataset-label-viewport:hover, .dataset-label-viewport:focus-visible {
    outline: 1px solid var(--primary-color,#66c8d0);
    outline-offset:2px;
}
/* Horizontal reveal on selected dataset text and chips, not just options. */
[data-testid="stSelectbox"] input {
    text-overflow: clip !important;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"] {
    max-width: 100%;
    min-width: 0;
}
</style>
"""

# No interpolated publisher labels or untrusted data appear in this script.
# Event delegation works with dynamically mounted/virtualized option rows
# and is installed only once per browser page.
_SCROLL_SCRIPT = r"""
<script>
(function () {
  if (window.__datasetLongLabelScrollInstalled) return;
  window.__datasetLongLabelScrollInstalled = true;

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let active = null;
  let animation = null;
  let pending = null;

  function stop(reset) {
    if (pending !== null) window.clearTimeout(pending);
    if (animation !== null) window.cancelAnimationFrame(animation);
    pending = animation = null;
    if (reset && active && active.isConnected) active.scrollLeft = 0;
    active = null;
  }

  function viewportFrom(node) {
    if (!(node instanceof Element)) return null;
    // Source table cells may lose CSS :hover while Streamlit recomputes
    // layout; continue their scrolling until complete even if that occurs.
    const tableCell = node.closest(".fred-label-viewport, .dataset-label-viewport");
    if (tableCell) return tableCell;
    const option = node.closest('[role="option"]');
    if (option && option.closest('[role="listbox"]')) {
      return option.querySelector("[data-item-hl]") || option;
    }
    // The selected display of st.selectbox is an input. Keep search and
    // password fields untouched, and never animate an actively edited input.
    const selected = node.closest('[data-testid="stSelectbox"] input');
    if (selected && document.activeElement !== selected) return selected;
    // MultiSelect selected tags can truncate dataset keys.
    const tag = node.closest('[data-testid="stMultiSelect"] [data-baseweb="tag"]');
    if (tag) {
      return tag.querySelector('span') || tag;
    }
    return null;
  }

  function start(target) {
    if (!target || target === active) return;
    stop(true);
    active = target;
    if (reducedMotion.matches) return;
    const overflow = target.scrollWidth - target.clientWidth;
    if (overflow <= 3) return;
    // Give users time to read the visible beginning. Move at a readable
    // speed, with animation distance determined by actual rendered widths.
    pending = window.setTimeout(() => {
      pending = null;
      const started = performance.now();
      // Cap very long labels so the entire value is revealed promptly.
      const duration = Math.min(14000, Math.max(1700, overflow * 17));
      function step(timestamp) {
        if (!target.isConnected || active !== target) return;
        const progress = Math.min(1, (timestamp - started) / duration);
        target.scrollLeft = overflow * progress;
        if (progress < 1) animation = window.requestAnimationFrame(step);
        else {
          animation = null;
          if (target.classList.contains("fred-label-viewport") ||
              target.classList.contains("dataset-label-viewport")) {
            // Reset shortly after finishing, unless the reader moved to
            // another label. The animation is never tied to network state.
            window.setTimeout(() => {
              if (active === target && !target.matches(":hover")) stop(true);
            }, 1600);
          }
        }
      }
      animation = window.requestAnimationFrame(step);
    }, 350);
  }

  document.addEventListener("pointerover", function (event) {
    start(viewportFrom(event.target));
  }, true);
  document.addEventListener("pointerout", function (event) {
    if (!active) return;
    const current = viewportFrom(event.target);
    if (current !== active) return;
    if (active.classList.contains("fred-label-viewport") ||
        active.classList.contains("dataset-label-viewport")) {
      // Text/canvas overlays can emit a synthetic pointerout while the user
      // is still reading. Keep the table cell's scroll in progress.
      return;
    }
    const next = event.relatedTarget;
    if (next instanceof Element && viewportFrom(next) === active) return;
    stop(true);
  }, true);
})();
</script>
"""


def install_dataset_label_scrolling() -> None:
    """Install the single app-wide CSS and static delegated hover handler."""
    st.html(_SCROLL_STYLE)
    st.html(_SCROLL_SCRIPT, unsafe_allow_javascript=True)


def install_fred_label_scrolling() -> None:
    """Backward-compatible alias for the FRED metadata view."""
    install_dataset_label_scrolling()


def metadata_html(rows: Sequence[Mapping[str, object]]) -> str:
    """Build an escaped, scrollable table of FRED metadata (max 75 entries).

    Escaping source-provided labels protects the application from malicious
    publisher strings. The full label is also available to assistive technology through aria-label
    and keyboard focus, even if animation is disabled by reduced-motion settings.
    """
    headers = ("ID", "Label", "Frequency", "Unit")
    classes = ("fred-id-cell", "fred-name-cell", "fred-frequency-cell", "fred-unit-cell")
    output = ['<div class="fred-metadata-wrap"><table class="fred-metadata-table">',
              "<thead><tr>"]
    for label, cls in zip(headers, classes):
        output.append(f'<th scope="col" class="{cls}">{label}</th>')
    output.append("</tr></thead><tbody>")
    for row in rows[:75]:
        identifier = escape(str(row.get("id", "")), quote=True)
        label = escape(str(row.get("label", "")), quote=True)
        frequency = escape(str(row.get("frequency", "")), quote=True)
        unit = escape(str(row.get("unit", "")), quote=True)
        # The longer a label, the longer the pan duration. The distance
        # itself is computed by CSS using the rendered cell width.
        seconds = min(36, max(7, len(str(row.get("label", ""))) / 22))
        output.append(
            '<tr><td class="fred-id-cell">' + identifier + '</td>'
            '<td class="fred-name-cell"><div class="fred-label-viewport" '
            'tabindex="0" aria-label="' + label + '" '
            'style="--fred-pan-duration:' + f'{seconds:.1f}s' + '">'
            '<span class="fred-label-text">' + label + '</span></div></td>'
            '<td class="fred-frequency-cell">' + frequency + '</td>'
            '<td class="fred-unit-cell">' + unit + '</td></tr>'
        )
    output.append("</tbody></table></div>")
    return "".join(output)


def render_fred_metadata(rows: Sequence[Mapping[str, object]]) -> None:
    st.html(metadata_html(rows))


def dataset_table_html(
    rows: Sequence[Mapping[str, object]],
    columns: Sequence[str],
    *,
    max_rows: int = 150,
    height: int = 370,
) -> str:
    """Build a readable, escaped inventory table with all text cells scrollable.

    This is for dataset catalogs / metadata, not numeric observation
    spreadsheets, where the native sortable data grid is retained.
    """
    if not 1 <= max_rows <= 500:
        raise ValueError("Dataset table max_rows must be 1..500")
    if not 100 <= height <= 1000:
        raise ValueError("Dataset table height must be 100..1000")
    safe_columns=[str(c) for c in columns]
    out=[f'<div class="dataset-table-wrap" style="max-height:{height}px">',
         '<table class="dataset-table"><thead><tr>']
    for col in safe_columns:
        out.append(f'<th scope="col">{escape(col,quote=True)}</th>')
    out.append('</tr></thead><tbody>')
    for record in rows[:max_rows]:
        out.append('<tr>')
        for column in safe_columns:
            raw=record.get(column,"")
            if raw is None:
                raw=""
            # Protect source titles, URLs, derived names, and cell data.
            value=escape(str(raw),quote=True)
            out.append(
                '<td><div class="dataset-label-viewport" tabindex="0" '
                'aria-label="' + value + '">'
                '<span class="dataset-label-text">' + value + '</span>'
                '</div></td>'
            )
        out.append('</tr>')
    out.append('</tbody></table></div>')
    return "".join(out)


def render_dataset_table(
    rows: Sequence[Mapping[str, object]],
    columns: Sequence[str],
    *,
    max_rows: int = 150,
    height: int = 370,
) -> None:
    """Render dataset metadata with hover-revealed values in every column."""
    st.html(dataset_table_html(rows,columns,max_rows=max_rows,height=height))
