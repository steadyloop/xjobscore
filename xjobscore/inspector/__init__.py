"""Local backend dashboard: payload/parse inspection, network telemetry,
polling policy, and -- when opted in -- the production scheduler itself.

The inspector stages its own inspection results in a dedicated PostgreSQL
schema (``inspector``). In the core pipeline's ``public`` schema its policy
layer (``policy.py``) writes only the operator-owned fields (``feed.enabled``,
``schedule.interval_s``, ``schedule.paused_at``, ``scheduler_control.running``)
and registry sync; it never touches ``schedule``'s runtime columns (etag,
res_hash, strikes, hold_until) -- those stay run.py's workers' alone to write.

That boundary is about ``policy.py`` specifically, not the process as a whole.
``inspector.runner.SchedulerRunner``, gated behind ``XJOBSCORE_INSPECTOR_RUNNER``
(off by default) and the ``scheduler_control`` flag the Scheduler page's
pause/resume button drives, calls ``run.cycle`` from inside this process on a
tick -- the exact worker path cron would otherwise run. When it is enabled and
running, the inspector process *does* write bronze (``raw_posting``) and
canonical (``job_posting``) rows, append to ``poll_observation``, and own the
``schedule`` runtime columns for the feeds it polls. See ``runner.py``'s module
docstring for the two-switch gate, and ``README.md`` for the full picture.
"""

from xjobscore.inspector.app import app, create_app

__all__ = ["app", "create_app"]
