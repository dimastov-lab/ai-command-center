"""Prometheus text exposition format 0.0.4, rendered without a dependency.

`prometheus_client` is not in `requirements.txt` and this module is the reason
it does not need to be. What the exporter actually uses of that library is the
text format for a handful of gauges — roughly the sixty lines below — while the
library brings a default registry of process/GC collectors, a multiprocess mode
and an HTTP server, none of which apply to a textfile-collector writer that
runs as a oneshot under a timer. The trade is deliberate: a dependency in a
lockfile that CI resolves on every run, against a format that has been stable
since 2014 and whose escaping rules are the four lines in `_escape_*` below.

The format, from the spec, and the parts that actually bite:

* One `# HELP` and one `# TYPE` per family, before its samples.
* All samples of a family must be contiguous. A family split across the file
  is a parse error in strict parsers, which is why `render()` takes families
  rather than a flat sample list — the grouping is not the caller's to get
  wrong.
* Help text escapes backslash and newline, and NOT double quote.
* Label values escape backslash, double quote and newline.
  (These two rules differ, which is the classic way a hand-rolled exporter
  produces a file that parses everywhere except on the one value containing a
  quote. `test_exposition.py` pins both.)
* The file must end in a newline.

Numbers are rendered by `_format_value` rather than `repr`, because Prometheus
wants Go float syntax for the three non-finite values: `NaN`, `+Inf`, `-Inf`.
Python's `repr` produces `nan`/`inf`, which the parser rejects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from command_center.telemetry.contract import Metric

__all__ = ["Sample", "Family", "render", "format_value"]


@dataclass(frozen=True, slots=True)
class Sample:
    """One point: the label values for this series, and the number.

    ``labels`` maps label name to value. It is validated against the family's
    declared label tuple at render time — a sample carrying a label the
    contract does not declare is a programming error that would otherwise
    surface as a silently different series (Prometheus treats any distinct
    label set as a distinct series, so a typo'd label name does not fail, it
    forks the timeseries and the alert stops matching).
    """

    labels: dict[str, str]
    value: float


@dataclass(frozen=True, slots=True)
class Family:
    metric: Metric
    samples: Sequence[Sample]


def _escape_help(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _escape_label_value(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def format_value(value: float) -> str:
    """Go float syntax for the non-finite values, plain decimal otherwise."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if isinstance(value, int) or float(value).is_integer():
        # Integers as integers: a queue depth reading `3` rather than `3.0`
        # is what an operator diffing two .prom files expects to see, and the
        # parser is indifferent.
        return str(int(value))
    return repr(float(value))


def _render_labels(metric: Metric, labels: dict[str, str]) -> str:
    unknown = set(labels) - set(metric.labels)
    if unknown:
        raise ValueError(
            f"{metric.name}: labels {sorted(unknown)} are not declared in the "
            f"contract (declared: {list(metric.labels)})"
        )
    missing = set(metric.labels) - set(labels)
    if missing:
        raise ValueError(
            f"{metric.name}: missing declared labels {sorted(missing)}"
        )
    if not metric.labels:
        return ""
    # Contract order, not dict order: see Metric.labels.
    pairs = ",".join(
        f'{name}="{_escape_label_value(labels[name])}"' for name in metric.labels
    )
    return "{" + pairs + "}"


def render(families: Iterable[Family]) -> str:
    """Render families to the text exposition format, newline-terminated.

    A family with no samples still emits its HELP/TYPE header. That is the
    difference between "this exporter does not know about worker metrics" and
    "no worker currently holds a lease", and only the second is normal.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for family in families:
        metric = family.metric
        if metric.name in seen:
            raise ValueError(
                f"{metric.name}: family emitted twice; samples of one family "
                "must be contiguous in the exposition"
            )
        seen.add(metric.name)
        lines.append(f"# HELP {metric.name} {_escape_help(metric.help)}")
        lines.append(f"# TYPE {metric.name} {metric.type}")
        for sample in family.samples:
            lines.append(
                f"{metric.name}{_render_labels(metric, sample.labels)} "
                f"{format_value(sample.value)}"
            )
    return "\n".join(lines) + "\n"
