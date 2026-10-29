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
@keyframes fred-label-pan {
    0%, 4% { transform: translateX(0); }
    86%, 100% {
        /* Container query units account for each cell's actual width;
           transform percentages refer to the full text width. */
        transform: translateX(min(0px, calc(100cqw - 100%)));
    }
}
.fred-label-viewport:hover .fred-label-text,
.fred-label-viewport:focus-visible .fred-label-text {
    animation: fred-label-pan var(--fred-pan-duration, 10s) linear alternate infinite;
}
@media (prefers-reduced-motion: reduce) {
    .fred-label-viewport:hover .fred-label-text,
    .fred-label-viewport:focus-visible .fred-label-text {
        animation: none;
    }
}
.fred-label-viewport:hover, .fred-label-viewport:focus-visible {
    outline: 1px solid var(--primary-color, #66c8d0);
    outline-offset: 2px;
}
/* Streamlit 1.59+ uses React Aria and renders the dropdown at document
   scope. The listbox aria-label identifies only our "Series" options.
   Keep overflow scrollable instead of truncating its inner text. */
[role="listbox"][aria-label="Series"] [role="option"] [data-item-hl] {
    overflow-x: auto !important;
    overflow-y: hidden !important;
    scrollbar-width: none !important;
    -ms-overflow-style: none !important;
}
[role="listbox"][aria-label="Series"] [role="option"] [data-item-hl]::-webkit-scrollbar {
    display: none;
}
</style>
"""

# No interpolated publisher labels or untrusted data appear in this script.
# Event delegation works with dynamically mounted/virtualized option rows
# and is installed only once per browser page.
_SCROLL_SCRIPT = r"""
<script>
(function () {
  if (window.__fredLongLabelScrollInstalled) return;
  window.__fredLongLabelScrollInstalled = true;

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
    // Metadata cells use native CSS animation, avoiding pointerout races.
    const option = node.closest('[role="option"]');
    if (!option) return null;
    const listbox = option.closest('[role="listbox"][aria-label="Series"]');
    if (!listbox) return null;
    return option.querySelector("[data-item-hl]");
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
      const duration = Math.max(1700, overflow * 17);
      function step(timestamp) {
        if (!target.isConnected || active !== target) return;
        const progress = Math.min(1, (timestamp - started) / duration);
        target.scrollLeft = overflow * progress;
        if (progress < 1) animation = window.requestAnimationFrame(step);
        else animation = null;
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
    const next = event.relatedTarget;
    if (next instanceof Element && viewportFrom(next) === active) return;
    stop(true);
  }, true);
})();
</script>
"""


def install_fred_label_scrolling() -> None:
    """Install CSS and a static, delegated browser hover handler."""
    st.html(_SCROLL_STYLE)
    st.html(_SCROLL_SCRIPT, unsafe_allow_javascript=True)


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
