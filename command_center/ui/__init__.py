"""Streamlit presentation layer extracted out of `app.py`.

Every other package under `command_center/*` follows a strict "zero
Streamlit import" rule (see `docs/adr/0001-engineering-control-center-v2-
increment-1.md` §2) so its logic is testable with plain `pytest`, independent
of `streamlit.testing.v1.AppTest`. `command_center.ui` is the one deliberate,
documented exception: it exists specifically to keep `app.py` thin by holding
Streamlit rendering code that used to live inline in `app.py`, so it *does*
import `streamlit`.

The boundary this package must respect in exchange: a `command_center.ui`
module never contains business logic of its own — it only collects widget
input and calls into a Streamlit-free module (`recommend`,
`recommendation_service`, `execution_queue`, `project_intelligence`,
`project_config`, `models`, `tasks_repository`, `launch_service`) for
anything that isn't rendering, exactly the division `app.py`'s own
`render_*` functions already followed. No module outside `command_center.ui`
(and `app.py` itself) may import from this package — the dependency only
ever points one way, in, from `app.py`.
"""
